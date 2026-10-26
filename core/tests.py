import csv
import io
import os
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from core.net import client_ip
from core.privacy import mask_pii
from notifications.models import EmailLog
from notifications.utils import send_email
from payments.models import PaymentIntent
from services.models import AirtimeTransaction, DataTransaction, ProviderLog
from users.models import User
from wallets import services as wallet_services
from wallets.models import Wallet

PHONE = "08031234567"
REDIS_THAT_IS_DOWN = {
    "default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": "redis://127.0.0.1:9/0"}
}


def _audit_rows():
    out = io.StringIO()
    call_command("audit_money", "--csv", stdout=out)
    return list(csv.DictReader(io.StringIO(out.getvalue())))


def _section(rows, name):
    return [row for row in rows if row["section"] == name]


class AuditMoneyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="audit@example.com", password="StrongPass123")
        self.wallet = Wallet.objects.get(user=self.user)

    def _set_wallet(self, balance, locked="0.00"):
        Wallet.objects.filter(pk=self.wallet.pk).update(balance=Decimal(balance), locked=Decimal(locked))

    def test_wallet_that_matches_its_history_is_not_flagged(self):
        PaymentIntent.objects.create(user=self.user, amount=Decimal("1000.00"), reference="A-1", status="success")
        AirtimeTransaction.objects.create(
            user=self.user,
            network="mtn",
            phone=PHONE,
            amount=Decimal("300.00"),
            status="successful",
            client_reference="AIR-A-1",
        )
        AirtimeTransaction.objects.create(
            user=self.user,
            network="mtn",
            phone=PHONE,
            amount=Decimal("200.00"),
            status="pending",
            client_reference="AIR-A-2",
        )
        self._set_wallet("500.00", locked="200.00")

        self.assertEqual(_section(_audit_rows(), "wallet_mismatch"), [])

    def test_wallet_holding_more_than_its_history_explains_is_flagged(self):
        PaymentIntent.objects.create(user=self.user, amount=Decimal("1000.00"), reference="A-2", status="success")
        self._set_wallet("2000.00")

        [row] = _section(_audit_rows(), "wallet_mismatch")

        self.assertEqual(row["user"], "audit@example.com")
        self.assertEqual(Decimal(row["difference"]), Decimal("1000.00"))

    def test_data_purchase_charged_below_vtpass_amount_is_flagged(self):
        DataTransaction.objects.create(
            user=self.user,
            network="mtn",
            phone=PHONE,
            plan="mtn-1gb",
            amount=Decimal("1.00"),
            status="successful",
            client_reference="DATA-A-1",
            raw_response={"code": "000", "content": {"transactions": {"status": "delivered", "amount": 1200}}},
        )

        [row] = _section(_audit_rows(), "underpriced_data")

        self.assertEqual(row["reference"], "DATA-A-1")
        self.assertEqual(Decimal(row["difference"]), Decimal("-1199.00"))

    def test_payment_credited_twice_by_old_verify_logic_is_flagged(self):
        PaymentIntent.objects.create(user=self.user, amount=Decimal("1500.00"), reference="PAY-A-1", status="success")
        success = {"status": True, "data": {"status": "success", "currency": "NGN"}}
        gateway_error = {"raw": "<html>Bad gateway</html>", "http_status": 502}
        for status_code, body in (("200", success), ("502", gateway_error), ("200", success)):
            ProviderLog.objects.create(
                service_type="paystack",
                client_reference="PAY-A-1",
                endpoint="/transaction/verify",
                status_code=status_code,
                request_payload={},
                response_payload=body,
            )

        [row] = _section(_audit_rows(), "paystack_multi_credit")

        self.assertEqual(row["reference"], "PAY-A-1")
        self.assertEqual(Decimal(row["difference"]), Decimal("1500.00"))

    def test_refund_after_network_error_is_flagged_and_failures_are_grouped_by_code(self):
        AirtimeTransaction.objects.create(
            user=self.user,
            network="mtn",
            phone=PHONE,
            amount=Decimal("500.00"),
            status="failed",
            client_reference="AIR-A-3",
            raw_response={"code": "999", "response_description": "Network error"},
        )
        AirtimeTransaction.objects.create(
            user=self.user,
            network="mtn",
            phone=PHONE,
            amount=Decimal("100.00"),
            status="failed",
            client_reference="AIR-A-4",
            raw_response={"code": "011", "response_description": "INVALID ARGUMENTS"},
        )

        rows = _audit_rows()
        [refund] = _section(rows, "unknown_outcome_refund")
        codes = {row["reference"] for row in _section(rows, "failure_codes")}

        self.assertEqual(refund["reference"], "AIR-A-3")
        self.assertEqual(codes, {"999", "011"})

    def test_requery_pending_refund_also_frees_the_locked_money(self):
        wallet_services.credit_topup(self.user, Decimal("1000.00"), "PAY-RQ-1")
        wallet_services.hold(self.user, Decimal("300.00"), "airtime:RQ-1")
        txn = AirtimeTransaction.objects.create(
            user=self.user,
            network="mtn",
            phone=PHONE,
            amount=Decimal("300.00"),
            status="pending",
            client_reference="RQ-1",
        )
        failed = {"provider": {"code": "011", "content": {"transactions": {"status": "failed"}}}}

        with patch("core.management.commands.requery_pending.requery_status", return_value=failed):
            call_command("requery_pending", "--age-mins", "0", stdout=io.StringIO())

        txn.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(txn.status, "failed")
        self.assertEqual((self.wallet.balance, self.wallet.locked), (Decimal("1000.00"), Decimal("0.00")))
        call_command("check_wallets", stdout=io.StringIO())

    def test_health_reports_database_and_cache(self):
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"], {"database": "ok", "cache": "ok"})

    @override_settings(CACHES=REDIS_THAT_IS_DOWN)
    def test_health_stays_up_when_only_the_cache_is_down(self):
        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"], {"database": "ok", "cache": "error"})

    def test_text_report_lists_every_section(self):
        out = io.StringIO()
        call_command("audit_money", stdout=out)

        self.assertIn("Wallets whose balance doesn't match their history: 0", out.getvalue())
        self.assertIn("Read-only: nothing was changed.", out.getvalue())


class ProductionSettingsTests(SimpleTestCase):
    """Production must refuse to start with settings that would lose money or data."""

    SAFE_LIVE = {
        "ENV": "production",
        "SECRET_KEY": "x" * 50,
        "DATABASE_URL": "postgres://u:p@db.example.com:5432/quicksurf",
        "PAYSTACK_SECRET_KEY": "sk_live_123",
        "PROVIDER_MODE": "LIVE",
        "VTPASS_BASE_URL": "https://vtpass.com/api",
        "VTPASS_API_KEY": "a",
        "VTPASS_PUBLIC_KEY": "b",
        "VTPASS_SECRET_KEY": "c",
    }

    def _boot(self, **changes):
        # Blank values (not missing ones): python-decouple would fall back to a developer's .env file.
        env = {**os.environ, **self.SAFE_LIVE, **changes}
        return subprocess.run(
            [sys.executable, "-c", "import quicksurf.settings"],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_a_complete_live_configuration_starts(self):
        result = self._boot()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_refuses_to_start_without_an_explicit_provider_mode(self):
        result = self._boot(PROVIDER_MODE="")
        self.assertIn("PROVIDER_MODE must be LIVE or SANDBOX", result.stderr)

    def test_refuses_live_mode_without_vtpass_keys(self):
        result = self._boot(VTPASS_SECRET_KEY="")
        self.assertIn("missing: VTPASS_SECRET_KEY", result.stderr)

    def test_refuses_live_mode_pointed_at_the_vtpass_sandbox(self):
        result = self._boot(VTPASS_BASE_URL="https://sandbox.vtpass.com/api")
        self.assertIn("points at the VTpass sandbox", result.stderr)

    def test_refuses_a_short_secret_key(self):
        result = self._boot(SECRET_KEY="too-short")
        self.assertIn("SECRET_KEY must be at least 50 characters", result.stderr)

    def test_refuses_to_start_without_a_database_url(self):
        result = self._boot(DATABASE_URL="")
        self.assertIn("DATABASE_URL is required", result.stderr)


PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class RequestSafetyTests(APITestCase):
    def test_client_ip_trusts_cloudflare_not_x_forwarded_for(self):
        factory = RequestFactory()
        spoofed = factory.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="10.0.0.1")
        via_cloudflare = factory.get("/", HTTP_CF_CONNECTING_IP="41.58.1.2", HTTP_X_FORWARDED_FOR="1.2.3.4")

        self.assertEqual(client_ip(spoofed), "10.0.0.1")
        self.assertEqual(client_ip(via_cloudflare), "41.58.1.2")

    def test_unexpected_errors_return_json_without_internal_details(self):
        self.client.force_authenticate(User.objects.create_user(email="err@example.com", password="Pass-Phrase-9"))

        with (
            patch("wallets.views.Wallet.objects.get", side_effect=RuntimeError("db password is hunter2")),
            self.assertLogs("core.exceptions", level="ERROR"),
        ):
            response = self.client.get(reverse("wallet-balance"))

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "server_error")
        self.assertNotIn("hunter2", response.content.decode())

    def test_logs_get_masked_phone_numbers_and_no_secrets(self):
        masked = mask_pii(
            {
                "phone": "08031234567",
                "pin": "2580",
                "data": [{"email": "ade@example.com", "unique_element": "08031234567"}],
            }
        )

        self.assertEqual(
            masked, {"phone": "080***4567", "data": [{"email": "ad***@example.com", "unique_element": "080***4567"}]}
        )


@override_settings(STORAGES=PLAIN_STATIC)
class StaffAccessTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="ops@example.com", password="Pass-Phrase-9", is_staff=True)

    def test_admin_sign_in_goes_through_allauth(self):
        response = self.client.get(reverse("admin:login"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/accounts/login/"))

    @override_settings(STAFF_MFA_REQUIRED=True)
    def test_staff_must_set_up_an_authenticator_app_before_using_the_admin(self):
        self.client.force_login(self.staff)

        without = self.client.get(reverse("admin:index"))
        Authenticator.objects.create(user=self.staff, type=Authenticator.Type.TOTP, data={})
        with_app = self.client.get(reverse("admin:index"))

        self.assertEqual(without.status_code, 302)
        self.assertTrue(without["Location"].startswith(reverse("mfa_activate_totp")))
        self.assertEqual(with_app.status_code, 200)

    def test_public_signup_on_the_staff_pages_is_closed(self):
        self.client.post(
            "/accounts/signup/",
            {"email": "intruder@example.com", "password1": "Pass-Phrase-9", "password2": "Pass-Phrase-9"},
        )

        self.assertFalse(User.objects.filter(email="intruder@example.com").exists())

    def test_staff_roles_give_wallet_adjustments_only_to_finance(self):
        call_command("setup_staff_roles", stdout=io.StringIO())
        call_command("setup_staff_roles", stdout=io.StringIO())  # safe to run again

        finance = set(Group.objects.get(name="Finance").permissions.values_list("codename", flat=True))
        support = set(Group.objects.get(name="Support").permissions.values_list("codename", flat=True))
        self.assertIn("adjust_wallet", finance)
        self.assertNotIn("adjust_wallet", support)
        self.assertIn("view_paymentintent", support)


class LogRetentionTests(TestCase):
    def test_old_logs_are_only_deleted_with_apply(self):
        old = ProviderLog.objects.create(
            service_type="vtpass", request_payload={}, response_payload={}, status_code="000"
        )
        recent = ProviderLog.objects.create(
            service_type="vtpass", request_payload={}, response_payload={}, status_code="000"
        )
        ProviderLog.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(days=200))

        call_command("purge_old_logs", stdout=io.StringIO())
        self.assertTrue(ProviderLog.objects.filter(pk=old.pk).exists())

        call_command("purge_old_logs", "--apply", stdout=io.StringIO())
        self.assertFalse(ProviderLog.objects.filter(pk=old.pk).exists())
        self.assertTrue(ProviderLog.objects.filter(pk=recent.pk).exists())


class EmailTests(TestCase):
    def test_a_failed_send_is_recorded_and_reported(self):
        with (
            patch("notifications.utils.send_mail", side_effect=OSError("SMTP server unreachable")),
            self.assertLogs("notifications.utils", level="ERROR"),
        ):
            sent = send_email("ade@example.com", "Hello", "Body")

        log = EmailLog.objects.get(to="ade@example.com")
        self.assertFalse(sent)
        self.assertEqual(log.status, "failed")
        self.assertIn("SMTP server unreachable", log.error)
