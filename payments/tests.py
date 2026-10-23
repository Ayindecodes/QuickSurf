from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from payments.models import PaymentIntent
from payments.views import _credit_wallet_once
from users.models import User
from wallets.models import Wallet

VERIFY_GATEWAY_ERROR = (502, {"raw": "<html>Bad gateway</html>", "http_status": 502})


def _verify_success(amount_kobo):
    return (200, {"status": True, "data": {"status": "success", "amount": amount_kobo, "currency": "NGN"}})


class PaymentFlowTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="pay@example.com", password="StrongPass123")
        self.wallet = Wallet.objects.get(user=self.user)
        self.client.force_authenticate(self.user)

    @patch("payments.views.ps_verify")
    def test_verify_credits_wallet_once(self, mock_verify):
        intent = PaymentIntent.objects.create(
            user=self.user,
            amount=Decimal("1500.00"),
            reference="PAY-VERIFY-1",
            status="pending",
        )
        mock_verify.return_value = (
            200,
            {
                "status": True,
                "data": {
                    "status": "success",
                    "amount": 150000,
                    "currency": "NGN",
                },
            },
        )

        url = reverse("payments_verify", args=[intent.reference])

        first = self.client.get(url)
        second = self.client.get(url)

        self.wallet.refresh_from_db()
        intent.refresh_from_db()

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertTrue(first.data["credited"])
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertFalse(second.data["credited"])
        self.assertEqual(self.wallet.balance, Decimal("1500.00"))
        self.assertEqual(intent.status, "success")

    @patch("payments.views.ps_verify")
    def test_verify_does_not_credit_again_after_a_transient_provider_error(self, mock_verify):
        intent = PaymentIntent.objects.create(
            user=self.user,
            amount=Decimal("1500.00"),
            reference="PAY-TRANSIENT-1",
            status="pending",
        )
        mock_verify.side_effect = [_verify_success(150000), VERIFY_GATEWAY_ERROR, _verify_success(150000)]
        url = reverse("payments_verify", args=[intent.reference])

        responses = [self.client.get(url) for _ in range(3)]

        self.wallet.refresh_from_db()
        intent.refresh_from_db()

        self.assertEqual([r.data["status"] for r in responses], ["success", "success", "success"])
        self.assertEqual(self.wallet.balance, Decimal("1500.00"))
        self.assertEqual(intent.status, "success")

    @patch("payments.views.ps_verify")
    def test_verify_never_downgrades_an_intent_credited_during_the_call(self, mock_verify):
        intent = PaymentIntent.objects.create(
            user=self.user,
            amount=Decimal("1500.00"),
            reference="PAY-RACE-1",
            status="pending",
        )

        def webhook_credits_while_verify_waits(reference):
            _credit_wallet_once(self.user, intent.amount, reference)
            return VERIFY_GATEWAY_ERROR

        mock_verify.side_effect = webhook_credits_while_verify_waits
        url = reverse("payments_verify", args=[intent.reference])

        self.client.get(url)
        intent.refresh_from_db()
        self.assertEqual(intent.status, "success")

        mock_verify.side_effect = None
        mock_verify.return_value = _verify_success(150000)
        later = self.client.get(url)

        self.wallet.refresh_from_db()

        self.assertFalse(later.data["credited"])
        self.assertEqual(self.wallet.balance, Decimal("1500.00"))

    @override_settings(TOPUPS_ENABLED=False)
    @patch("payments.views.ps_initialize")
    def test_init_is_refused_while_topups_switched_off(self, mock_initialize):
        resp = self.client.post(reverse("payments_init"), {"amount": "500.00"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(mock_initialize.call_count, 0)
        self.assertFalse(PaymentIntent.objects.filter(user=self.user).exists())

    @override_settings(TOPUPS_ENABLED=False)
    @patch("payments.views.ps_verify", return_value=_verify_success(150000))
    def test_verify_still_credits_while_topups_switched_off(self, _mock_verify):
        intent = PaymentIntent.objects.create(
            user=self.user,
            amount=Decimal("1500.00"),
            reference="PAY-PAUSED-1",
            status="pending",
        )

        resp = self.client.get(reverse("payments_verify", args=[intent.reference]))

        self.wallet.refresh_from_db()

        self.assertTrue(resp.data["credited"])
        self.assertEqual(self.wallet.balance, Decimal("1500.00"))

    @patch("payments.views.ps_initialize", side_effect=Exception("provider timeout"))
    def test_init_handles_provider_exception_and_marks_intent_failed(self, _mock_initialize):
        url = reverse("payments_init")
        resp = self.client.post(url, {"amount": "500.00"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertIn("detail", resp.data)

        intent = PaymentIntent.objects.get(user=self.user)
        self.assertEqual(intent.status, "failed")
        self.assertEqual((intent.init_response or {}).get("status"), False)

    @patch("payments.views.ps_initialize")
    def test_init_returns_502_when_provider_rejects_request(self, mock_initialize):
        mock_initialize.return_value = (
            400,
            {"status": False, "message": "bad request"},
        )

        url = reverse("payments_init")
        resp = self.client.post(url, {"amount": "500.00"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        intent = PaymentIntent.objects.get(user=self.user)
        self.assertEqual(intent.status, "failed")

    @patch("payments.views.valid_webhook", return_value=True)
    def test_webhook_credits_wallet_once_even_when_replayed(self, _mock_valid_webhook):
        intent = PaymentIntent.objects.create(
            user=self.user,
            amount=Decimal("800.00"),
            reference="PAY-WEBHOOK-1",
            status="pending",
        )

        payload = {
            "event": "charge.success",
            "data": {
                "reference": intent.reference,
                "status": "success",
            },
        }

        url = reverse("payments_webhook")
        first = self.client.post(url, payload, format="json")
        second = self.client.post(url, payload, format="json")

        self.wallet.refresh_from_db()
        intent.refresh_from_db()

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(self.wallet.balance, Decimal("800.00"))
        self.assertEqual(intent.status, "success")
        self.assertEqual(len(intent.webhook_events or []), 2)

    @patch("payments.views.valid_webhook", return_value=True)
    def test_charge_failed_webhook_does_not_undo_a_credit(self, _mock_valid_webhook):
        intent = PaymentIntent.objects.create(
            user=self.user,
            amount=Decimal("800.00"),
            reference="PAY-WEBHOOK-2",
            status="pending",
        )
        success = {"event": "charge.success", "data": {"reference": intent.reference, "status": "success"}}
        failed = {"event": "charge.failed", "data": {"reference": intent.reference, "status": "failed"}}
        url = reverse("payments_webhook")

        for payload in (success, failed, success):
            self.client.post(url, payload, format="json")

        self.wallet.refresh_from_db()
        intent.refresh_from_db()

        self.assertEqual(self.wallet.balance, Decimal("800.00"))
        self.assertEqual(intent.status, "success")
