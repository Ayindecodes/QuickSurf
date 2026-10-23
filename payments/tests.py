import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from payments.models import PaymentIntent
from payments.services import credit_wallet_once
from services.models import ProviderLog
from users.models import User
from wallets.models import Transaction, Wallet

VERIFY_GATEWAY_ERROR = (502, {"raw": "<html>Bad gateway</html>", "http_status": 502})


def _paid(reference, amount_kobo, currency="NGN", **extra):
    """Paystack transaction data for a successful charge."""
    return {"status": "success", "reference": reference, "amount": amount_kobo, "currency": currency, **extra}


def _verify_success(reference, amount_kobo, currency="NGN", **extra):
    return (200, {"status": True, "data": _paid(reference, amount_kobo, currency, **extra)})


def _charge_success(reference, amount_kobo, currency="NGN"):
    return {"event": "charge.success", "data": _paid(reference, amount_kobo, currency)}


class PaymentTestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="pay@example.com", password="StrongPass123",
                                             email_verified_at=timezone.now())
        self.wallet = Wallet.objects.get(user=self.user)
        self.client.force_authenticate(self.user)

    def _intent(self, reference, amount="1500.00", status="pending"):
        return PaymentIntent.objects.create(user=self.user, amount=Decimal(amount), reference=reference, status=status)

    def _balance(self):
        self.wallet.refresh_from_db()
        return self.wallet.balance


class PaymentFlowTests(PaymentTestCase):
    @patch("payments.views.ps_verify", return_value=_verify_success("PAY-VERIFY-1", 150000))
    def test_verify_credits_wallet_once(self, _mock_verify):
        intent = self._intent("PAY-VERIFY-1")
        url = reverse("payments_verify", args=[intent.reference])

        first = self.client.get(url)
        second = self.client.get(url)

        intent.refresh_from_db()
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertTrue(first.data["credited"])
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertFalse(second.data["credited"])
        self.assertEqual(self._balance(), Decimal("1500.00"))
        self.assertEqual(intent.status, "success")

    @patch("payments.views.ps_verify")
    def test_verify_does_not_credit_again_after_a_transient_provider_error(self, mock_verify):
        intent = self._intent("PAY-TRANSIENT-1")
        ok = _verify_success("PAY-TRANSIENT-1", 150000)
        mock_verify.side_effect = [ok, VERIFY_GATEWAY_ERROR, ok]
        url = reverse("payments_verify", args=[intent.reference])

        responses = [self.client.get(url) for _ in range(3)]

        intent.refresh_from_db()
        self.assertEqual([r.data["status"] for r in responses], ["success", "success", "success"])
        self.assertEqual(self._balance(), Decimal("1500.00"))
        self.assertEqual(intent.status, "success")

    @patch("payments.views.ps_verify")
    def test_verify_never_downgrades_an_intent_credited_during_the_call(self, mock_verify):
        intent = self._intent("PAY-RACE-1")

        def webhook_credits_while_verify_waits(reference):
            credit_wallet_once(self.user, intent.amount, reference)
            return VERIFY_GATEWAY_ERROR

        mock_verify.side_effect = webhook_credits_while_verify_waits
        url = reverse("payments_verify", args=[intent.reference])

        self.client.get(url)
        intent.refresh_from_db()
        self.assertEqual(intent.status, "success")

        mock_verify.side_effect = None
        mock_verify.return_value = _verify_success("PAY-RACE-1", 150000)
        later = self.client.get(url)

        self.assertFalse(later.data["credited"])
        self.assertEqual(self._balance(), Decimal("1500.00"))

    @patch("payments.views.ps_verify")
    def test_payment_of_a_different_amount_is_held_for_review_not_credited(self, mock_verify):
        intent = self._intent("PAY-SHORT-1")
        url = reverse("payments_verify", args=[intent.reference])

        mock_verify.return_value = _verify_success("PAY-SHORT-1", 100)  # paid ₦1 for a ₦1,500 top-up
        first = self.client.get(url)
        mock_verify.return_value = _verify_success("PAY-SHORT-1", 150000)
        second = self.client.get(url)

        intent.refresh_from_db()
        self.assertEqual(first.data["status"], "review")
        self.assertEqual(second.data["status"], "review")
        self.assertEqual(intent.status, "review")
        self.assertEqual(self._balance(), Decimal("0.00"))

    @patch("payments.views.ps_verify", return_value=_verify_success("PAY-USD-1", 150000, currency="USD"))
    def test_payment_in_a_different_currency_is_held_for_review(self, _mock_verify):
        intent = self._intent("PAY-USD-1")

        response = self.client.get(reverse("payments_verify", args=[intent.reference]))

        self.assertEqual(response.data["status"], "review")
        self.assertEqual(self._balance(), Decimal("0.00"))

    @patch("payments.views.ps_verify", return_value=_verify_success(
        "PAY-TRIM-1", 150000,
        authorization={"authorization_code": "AUTH_secret", "last4": "4081", "reusable": True},
        customer={"email": "pay@example.com", "phone": "08031234567"},
        log={"history": []},
    ))
    def test_stored_paystack_payloads_drop_card_and_customer_details(self, _mock_verify):
        intent = self._intent("PAY-TRIM-1")

        self.client.get(reverse("payments_verify", args=[intent.reference]))

        intent.refresh_from_db()
        logged = ProviderLog.objects.get(client_reference="PAY-TRIM-1", endpoint="/transaction/verify")
        for stored in (intent.verify_response, logged.response_payload):
            self.assertEqual(stored["data"]["amount"], 150000)
            for dropped in ("authorization", "customer", "log"):
                self.assertNotIn(dropped, stored["data"])
        self.assertEqual(self._balance(), Decimal("1500.00"))

    @override_settings(TOPUPS_ENABLED=False)
    @patch("payments.views.ps_initialize")
    def test_init_is_refused_while_topups_switched_off(self, mock_initialize):
        resp = self.client.post(reverse("payments_init"), {"amount": "500.00"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(mock_initialize.call_count, 0)
        self.assertFalse(PaymentIntent.objects.filter(user=self.user).exists())

    @override_settings(TOPUPS_ENABLED=False)
    @patch("payments.views.ps_verify", return_value=_verify_success("PAY-PAUSED-1", 150000))
    def test_verify_still_credits_while_topups_switched_off(self, _mock_verify):
        intent = self._intent("PAY-PAUSED-1")

        resp = self.client.get(reverse("payments_verify", args=[intent.reference]))

        self.assertTrue(resp.data["credited"])
        self.assertEqual(self._balance(), Decimal("1500.00"))

    @override_settings(PAYSTACK_CALLBACK_URL="https://app.example.com/payments/callback")
    @patch("payments.views.ps_initialize", return_value=(
        200, {"status": True, "data": {"authorization_url": "https://checkout.paystack.com/x", "access_code": "x"}}))
    def test_init_uses_a_random_reference_and_the_configured_callback(self, mock_initialize):
        first = self.client.post(reverse("payments_init"), {"amount": "500.00"}, format="json")
        second = self.client.post(reverse("payments_init"), {"amount": "500.00"}, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertRegex(first.data["reference"], r"^QS-[0-9a-f]{32}$")
        self.assertNotEqual(first.data["reference"], second.data["reference"])
        self.assertEqual(mock_initialize.call_args.kwargs["callback_url"], "https://app.example.com/payments/callback")

    @patch("payments.views.ps_initialize", side_effect=Exception("provider timeout"))
    def test_init_handles_provider_exception_and_marks_intent_failed(self, _mock_initialize):
        resp = self.client.post(reverse("payments_init"), {"amount": "500.00"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn("detail", resp.data)
        intent = PaymentIntent.objects.get(user=self.user)
        self.assertEqual(intent.status, "failed")
        self.assertEqual((intent.init_response or {}).get("status"), False)

    @patch("payments.views.ps_initialize", return_value=(400, {"status": False, "message": "bad request"}))
    def test_init_returns_502_when_provider_rejects_request(self, _mock_initialize):
        resp = self.client.post(reverse("payments_init"), {"amount": "500.00"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(PaymentIntent.objects.get(user=self.user).status, "failed")


@patch("payments.views.valid_webhook", return_value=True)
class PaystackWebhookTests(PaymentTestCase):
    def _post(self, payload):
        return self.client.post(reverse("payments_webhook"), payload, format="json")

    def test_webhook_credits_wallet_once_even_when_replayed(self, _mock_valid_webhook):
        intent = self._intent("PAY-WEBHOOK-1", amount="800.00")
        payload = _charge_success(intent.reference, 80000)

        first = self._post(payload)
        second = self._post(payload)

        intent.refresh_from_db()
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(self._balance(), Decimal("800.00"))
        self.assertEqual(intent.status, "success")
        self.assertEqual(len(intent.webhook_events or []), 2)

    @patch("payments.views.ps_verify", return_value=_verify_success("PAY-BOTH-1", 150000))
    def test_verify_and_webhook_together_write_one_ledger_entry(self, _mock_verify, _mock_valid_webhook):
        intent = self._intent("PAY-BOTH-1")

        self.client.get(reverse("payments_verify", args=[intent.reference]))
        self._post(_charge_success(intent.reference, 150000))

        self.assertEqual(Transaction.objects.filter(type="topup", reference="PAY-BOTH-1").count(), 1)
        self.assertEqual(self._balance(), Decimal("1500.00"))

    def test_charge_failed_webhook_does_not_undo_a_credit(self, _mock_valid_webhook):
        intent = self._intent("PAY-WEBHOOK-2", amount="800.00")
        failed = {"event": "charge.failed", "data": {"reference": intent.reference, "status": "failed"}}

        for payload in (_charge_success(intent.reference, 80000), failed, _charge_success(intent.reference, 80000)):
            self._post(payload)

        intent.refresh_from_db()
        self.assertEqual(self._balance(), Decimal("800.00"))
        self.assertEqual(intent.status, "success")

    def test_webhook_for_a_different_amount_is_held_for_review(self, _mock_valid_webhook):
        intent = self._intent("PAY-WEBHOOK-3", amount="800.00")

        self._post(_charge_success(intent.reference, 100))

        intent.refresh_from_db()
        self.assertEqual(intent.status, "review")
        self.assertEqual(self._balance(), Decimal("0.00"))

    @patch("core.throttling.SafeAnonRateThrottle.wait", return_value=None)
    @patch("core.throttling.SafeAnonRateThrottle.allow_request", return_value=False)
    def test_webhook_is_never_rate_limited(self, _mock_throttle, _mock_wait, _mock_valid_webhook):
        intent = self._intent("PAY-WEBHOOK-4", amount="800.00")
        self.client.force_authenticate(None)

        login = self.client.post(reverse("user-login"), {"email": "x@example.com", "password": "x"}, format="json")
        webhook = self._post(_charge_success(intent.reference, 80000))

        self.assertEqual(login.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(webhook.status_code, status.HTTP_200_OK)
        self.assertEqual(self._balance(), Decimal("800.00"))


class PaystackWebhookSignatureTests(PaymentTestCase):
    def _post_signed(self, payload, key):
        body = json.dumps(payload).encode()
        signature = hmac.new(key.encode(), body, hashlib.sha512).hexdigest()
        return self.client.post(reverse("payments_webhook"), body, content_type="application/json",
                                HTTP_X_PAYSTACK_SIGNATURE=signature)

    @patch("payments.paystack.WEBHOOK_SECRET", "")
    def test_webhook_is_rejected_when_no_secret_is_configured(self):
        intent = self._intent("PAY-SIG-1", amount="50000.00", status="failed")

        response = self._post_signed(_charge_success(intent.reference, 5000000), key="")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self._balance(), Decimal("0.00"))

    @patch("payments.paystack.WEBHOOK_SECRET", "sk_test_dummy_secret")
    def test_only_webhooks_signed_with_the_secret_are_accepted(self):
        intent = self._intent("PAY-SIG-2", amount="800.00")
        payload = _charge_success(intent.reference, 80000)

        forged = self._post_signed(payload, key="wrong-key")
        genuine = self._post_signed(payload, key="sk_test_dummy_secret")

        self.assertEqual(forged.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(genuine.status_code, status.HTTP_200_OK)
        self.assertEqual(self._balance(), Decimal("800.00"))


class VerifyPendingTopupsTests(PaymentTestCase):
    def _aged(self, reference, age):
        intent = self._intent(reference)
        PaymentIntent.objects.filter(pk=intent.pk).update(created=timezone.now() - age)
        return intent

    def test_paid_topups_are_credited_and_old_unpaid_ones_abandoned(self):
        paid = self._aged("PAY-CRON-PAID", timedelta(minutes=30))
        stale = self._aged("PAY-CRON-OLD", timedelta(days=2))
        recent = self._intent("PAY-CRON-NEW")
        answers = {
            paid.reference: _verify_success(paid.reference, 150000),
            stale.reference: (200, {"status": True, "data": {"status": "ongoing", "reference": stale.reference}}),
        }

        with patch("payments.management.commands.verify_pending_topups.ps_verify",
                   side_effect=lambda reference: answers[reference]) as mock_verify:
            call_command("verify_pending_topups", stdout=io.StringIO())

        statuses = dict(PaymentIntent.objects.values_list("reference", "status"))
        self.assertEqual(statuses, {paid.reference: "success", stale.reference: "abandoned",
                                    recent.reference: "pending"})
        self.assertEqual(self._balance(), Decimal("1500.00"))
        self.assertEqual(mock_verify.call_count, 2)


class PaymentIntentAdminTests(PaymentTestCase):
    @patch("payments.admin.ps_verify", return_value=_verify_success("PAY-ADMIN-1", 150000))
    def test_reverify_action_credits_a_paid_intent(self, _mock_verify):
        intent = self._intent("PAY-ADMIN-1")
        staff = User.objects.create_superuser(email="admin@example.com", password="StrongPass123")
        self.client.force_login(staff)

        response = self.client.post(
            reverse("admin:payments_paymentintent_changelist"),
            {"action": "reverify", "_selected_action": [intent.pk]},
        )

        intent.refresh_from_db()
        self.assertEqual(response.status_code, 302)  # back to the list after running the action
        self.assertEqual(intent.status, "success")
        self.assertEqual(self._balance(), Decimal("1500.00"))


class ProductionPaystackKeyTests(SimpleTestCase):
    def _boot(self, paystack_key):
        env = {**os.environ, "ENV": "production", "SECRET_KEY": "x" * 50, "PAYSTACK_SECRET_KEY": paystack_key,
               "DATABASE_URL": "postgres://u:p@db.example.com:5432/quicksurf", "PROVIDER_MODE": "SANDBOX"}
        return subprocess.run([sys.executable, "-c", "import quicksurf.settings"], cwd=settings.BASE_DIR,
                              env=env, capture_output=True, text=True)

    def test_production_refuses_to_start_with_a_test_paystack_key(self):
        result = self._boot("sk_test_123")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be a live key", result.stderr)

    def test_production_starts_with_a_live_paystack_key(self):
        result = self._boot("sk_live_123")

        self.assertEqual(result.returncode, 0, result.stderr)
