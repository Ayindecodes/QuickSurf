from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import User
from wallets.models import Transaction, Wallet


class WalletEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="wallet@example.com", password="StrongPass123")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.balance = Decimal("2000.00")
        self.wallet.save(update_fields=["balance"])
        self.client.force_authenticate(self.user)

    def test_wallet_lock_and_unlock_update_balances_and_ledger(self):
        lock_response = self.client.post(
            reverse("wallet-lock"),
            {"amount": "500.00"},
            format="json",
        )
        unlock_response = self.client.post(
            reverse("wallet-unlock"),
            {"amount": "200.00"},
            format="json",
        )

        self.wallet.refresh_from_db()
        tx_types = list(self.wallet.transactions.order_by("timestamp").values_list("type", flat=True))

        self.assertEqual(lock_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(unlock_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.wallet.balance, Decimal("2000.00"))
        self.assertEqual(self.wallet.locked, Decimal("300.00"))
        self.assertEqual(tx_types, [Transaction.TYPE_LOCK, Transaction.TYPE_UNLOCK])

    def test_wallet_lock_rejects_amount_above_available_balance(self):
        response = self.client.post(
            reverse("wallet-lock"),
            {"amount": "2500.00"},
            format="json",
        )

        self.wallet.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.wallet.locked, Decimal("0.00"))

    def test_wallet_summary_returns_current_locked_value(self):
        lock_response = self.client.post(
            reverse("wallet-lock"),
            {"amount": "500.00"},
            format="json",
        )
        summary_response = self.client.get(reverse("wallet-summary"))

        self.assertEqual(lock_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(summary_response.status_code, status.HTTP_200_OK)
        self.assertEqual(summary_response.data["balance"], "2000.00")
        self.assertEqual(summary_response.data["locked"], "500.00")
