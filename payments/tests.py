from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from payments.models import PaymentIntent
from users.models import User
from wallets.models import Wallet


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
