import csv
import io
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from core.alerts import BAD_WEBHOOK_SIGNATURES, count_event, event_count
from core.net import client_ip
from core.privacy import mask_pii
from core.request_id import JsonFormatter, RequestIdFilter, _current
from notifications.models import EmailLog
from notifications.utils import send_email
from payments.models import PaymentIntent
from services.models import AirtimeTransaction, DataTransaction, ProviderLog
from users.models import TransactionPin, User
from wallets import services as wallet_services
from wallets.models import Transaction, Wallet

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

    def test_api_docs_build_and_list_each_operation_once(self):
        response = self.client.get("/api/schema/", {"format": "json"})

        paths = json.loads(response.content)["paths"]
        self.assertIn("/api/users/login/", paths)
        self.assertNotIn("/api/users/login", paths)  # the no-slash alias still works but isn't documented

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


class RequestIdTests(APITestCase):
    def test_every_response_carries_a_request_id_and_a_sane_incoming_one_is_kept(self):
        fresh = self.client.get("/api/health/")
        kept = self.client.get("/api/health/", HTTP_X_REQUEST_ID="render-abc12345")
        replaced = self.client.get("/api/health/", HTTP_X_REQUEST_ID="<script>alert(1)</script>")

        self.assertRegex(fresh["X-Request-ID"], r"^[0-9a-f]{32}$")
        self.assertEqual(kept["X-Request-ID"], "render-abc12345")
        self.assertRegex(replaced["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_error_responses_include_the_request_id(self):
        self.client.force_authenticate(User.objects.create_user(email="rid@example.com", password="Pass-Phrase-9"))

        with (
            patch("wallets.views.Wallet.objects.get", side_effect=RuntimeError("boom")),
            self.assertLogs("core.exceptions", level="ERROR"),
        ):
            response = self.client.get(reverse("wallet-balance"))

        self.assertEqual(response.json()["request_id"], response["X-Request-ID"])

    def test_log_lines_carry_the_request_id_as_json(self):
        record = logging.LogRecord("services.views", logging.ERROR, __file__, 1, "Purchase %s failed", ("REF-1",), None)
        token = _current.set("abc123def456")
        try:
            RequestIdFilter().filter(record)
        finally:
            _current.reset(token)

        late = logging.LogRecord("django.request", logging.ERROR, __file__, 1, "Internal Server Error", (), None)
        late.request = RequestFactory().get("/")
        late.request.request_id = "from-the-request"
        RequestIdFilter().filter(late)
        self.assertEqual(late.request_id, "from-the-request")

        entry = json.loads(JsonFormatter().format(record))
        self.assertEqual(
            {k: entry[k] for k in ("level", "logger", "request_id", "message")},
            {
                "level": "ERROR",
                "logger": "services.views",
                "request_id": "abc123def456",
                "message": "Purchase REF-1 failed",
            },
        )


class SentryTests(SimpleTestCase):
    def test_a_dsn_turns_sentry_on_without_sending_request_bodies(self):
        code = (
            "import quicksurf.settings, sentry_sdk; client = sentry_sdk.get_client(); "
            "print(client.is_active(), client.options['max_request_body_size'], client.options['send_default_pii'])"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            env={**os.environ, "SENTRY_DSN": "https://public@o0.ingest.sentry.io/1"},
        )

        self.assertEqual(result.stdout.split(), ["True", "never", "False"], result.stderr)


@override_settings(OPS_ALERT_EMAILS=["ops@example.com"])
class OpsAlertTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email="ops-user@example.com", password="Pass-Phrase-9")

    def test_all_clear_sends_nothing(self):
        out = io.StringIO()
        with patch("core.management.commands.ops_alerts.get_balance", return_value=Decimal("50000.00")):
            call_command("ops_alerts", stdout=out)

        self.assertIn("All clear", out.getvalue())
        self.assertEqual(len(mail.outbox), 0)

    def test_problems_are_logged_emailed_once_and_fail_the_command(self):
        stuck = AirtimeTransaction.objects.create(
            user=self.user,
            network="mtn",
            phone=PHONE,
            amount=Decimal("300"),
            status="pending",
            client_reference="STUCK-OPS",
        )
        AirtimeTransaction.objects.filter(pk=stuck.pk).update(timestamp=timezone.now() - timedelta(hours=2))
        for i in range(5):
            AirtimeTransaction.objects.create(
                user=self.user,
                network="mtn",
                phone=PHONE,
                amount=Decimal("100"),
                status="failed",
                client_reference=f"FAIL-OPS-{i}",
                raw_response={"code": "018"},
            )
        PaymentIntent.objects.create(user=self.user, amount=Decimal("500"), reference="PAY-REVIEW", status="review")
        Wallet.objects.filter(user=self.user).update(balance=Decimal("999.00"))  # outside the ledger
        for _ in range(10):
            count_event(BAD_WEBHOOK_SIGNATURES)

        with patch("core.management.commands.ops_alerts.get_balance", return_value=Decimal("500.00")):
            with (
                self.assertLogs("core.management.commands.ops_alerts", level="ERROR") as logs,
                self.assertRaises(CommandError),
            ):
                call_command("ops_alerts", stdout=io.StringIO())
            with self.assertLogs("core.management.commands.ops_alerts", level="ERROR"), self.assertRaises(CommandError):
                call_command("ops_alerts", stdout=io.StringIO())  # 15 minutes later: same problems

        self.assertEqual(len(logs.records), 6)
        self.assertEqual(len(mail.outbox), 1)  # not emailed again for the same problems
        body = mail.outbox[0].body
        for expected in (
            "1 purchase(s) pending",
            "5 of 5 purchases failed",
            "code: 018",
            "PAY-REVIEW",
            "₦500.00",
            "don't match their ledger",
            "10 Paystack webhooks rejected",
        ):
            self.assertIn(expected, body)

    def test_rejected_paystack_webhooks_are_counted(self):
        with self.assertLogs("payments.views", level="WARNING"):
            response = self.client.post(
                reverse("payments_webhook"),
                data="{}",
                content_type="application/json",
                HTTP_X_PAYSTACK_SIGNATURE="forged",
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(event_count(BAD_WEBHOOK_SIGNATURES), 1)


@override_settings(OPS_ALERT_EMAILS=["ops@example.com"], PAYSTACK_SECRET_KEY="sk_test_report")
class DailyReportTests(TestCase):
    def test_totals_and_paystack_mismatches_are_reported(self):
        start = timezone.make_aware(datetime.combine(datetime(2026, 10, 20).date(), time.min))
        user = User.objects.create_user(email="report@example.com", password="Pass-Phrase-9")
        PaymentIntent.objects.create(user=user, amount=Decimal("1500"), reference="PAY-A", status="pending")
        wallet_services.credit_topup(user, Decimal("1500"), "PAY-A")
        PaymentIntent.objects.filter(reference="PAY-A").update(status="success", paid_at=start + timedelta(hours=2))
        Transaction.objects.filter(reference="PAY-A").update(timestamp=start + timedelta(hours=2))
        PaymentIntent.objects.create(user=user, amount=Decimal("500"), reference="PAY-B", status="pending")
        PaymentIntent.objects.create(
            user=user, amount=Decimal("700"), reference="PAY-C", status="success", paid_at=start + timedelta(hours=4)
        )
        paystack = [
            {"reference": "PAY-A", "amount": 150000, "paid_at": "2026-10-20T01:00:00.000Z"},
            {"reference": "PAY-B", "amount": 50000, "paid_at": "2026-10-20T02:00:00.000Z"},
        ]
        out = io.StringIO()

        with (
            patch("core.management.commands.daily_report.list_successful", return_value=paystack),
            self.assertRaises(CommandError) as failure,
        ):
            call_command("daily_report", "--date", "2026-10-20", stdout=out)

        self.assertIn("Top-ups credited:   1 totalling ₦1500.00", out.getvalue())
        self.assertIn("paid at Paystack but not credited: PAY-B", str(failure.exception))
        self.assertIn("credited but Paystack shows no successful payment: PAY-C", str(failure.exception))
        self.assertNotIn("PAY-A", str(failure.exception))
        self.assertIn("2 Paystack mismatch(es)", mail.outbox[0].subject)

    @override_settings(PAYSTACK_SECRET_KEY="")
    def test_without_a_paystack_key_the_reconciliation_is_skipped(self):
        out = io.StringIO()

        call_command("daily_report", stdout=out)

        self.assertIn("Paystack reconciliation skipped", out.getvalue())


@override_settings(STORAGES=PLAIN_STATIC)
class SupportAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser(email="support@example.com", password="Pass-Phrase-9"))
        self.customer = User.objects.create_user(email="customer@example.com", password="Pass-Phrase-9")
        TransactionPin.objects.create(
            user=self.customer, pin_hash="x", locked_until=timezone.now() + timedelta(minutes=30)
        )

    def test_customers_and_their_purchases_can_be_searched(self):
        users = self.client.get(reverse("admin:users_user_changelist"), {"q": "customer"})
        purchases = self.client.get(
            reverse("admin:services_airtimetransaction_changelist"), {"q": "customer@example.com"}
        )
        detail = self.client.get(reverse("admin:users_user_change", args=[self.customer.pk]))
        sign_ins = self.client.get(reverse("admin:core_loginactivity_changelist"))

        self.assertContains(users, "customer@example.com")
        self.assertEqual((purchases.status_code, detail.status_code, sign_ins.status_code), (200, 200, 200))

    def test_support_actions_work_and_are_recorded_in_the_history(self):
        url = reverse("admin:users_user_changelist")
        for action in ("unlock_pin", "sign_out_everywhere", "mark_email_verified"):
            self.client.post(url, {"action": action, "_selected_action": [self.customer.pk]})

        self.customer.refresh_from_db()
        self.assertIsNone(TransactionPin.objects.get(user=self.customer).locked_until)
        self.assertTrue(self.customer.email_verified)
        history = LogEntry.objects.filter(object_id=str(self.customer.pk)).values_list("change_message", flat=True)
        self.assertEqual(
            sorted(history), ["Marked email as verified", "Signed out on all devices", "Unlocked transaction PIN"]
        )
