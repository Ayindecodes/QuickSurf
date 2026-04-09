from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from services.models import AirtimeTransaction, DataTransaction
from users.models import User
from wallets.models import Wallet


class AirtimePurchaseTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="service@example.com", password="StrongPass123")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.balance = Decimal("5000.00")
        self.wallet.save(update_fields=["balance"])
        self.client.force_authenticate(self.user)
        self.url = reverse("airtime-purchase")

    @patch("services.views.purchase_airtime")
    def test_airtime_purchase_success_debits_wallet_and_marks_successful(self, mock_purchase_airtime):
        mock_purchase_airtime.return_value = {
            "ok": True,
            "provider": {
                "code": "000",
                "requestId": "VT-123",
                "response_description": "Transaction successful",
                "content": {
                    "transactions": {
                        "status": "delivered",
                        "transactionId": "TX-123",
                    }
                },
            },
        }

        response = self.client.post(
            self.url,
            {
                "amount": "1000.00",
                "network": "mtn",
                "phone": "08031234567",
                "client_reference": "AIR-REF-001",
            },
            format="json",
        )

        self.wallet.refresh_from_db()
        txn = AirtimeTransaction.objects.get(client_reference="AIR-REF-001")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(txn.status, "successful")
        self.assertEqual(txn.provider_request_id, "VT-123")
        self.assertEqual(self.wallet.balance, Decimal("4000.00"))
        self.assertEqual(self.wallet.locked, Decimal("0.00"))

    @patch("services.views.purchase_airtime")
    def test_airtime_purchase_is_idempotent_for_existing_client_reference(self, mock_purchase_airtime):
        mock_purchase_airtime.return_value = {
            "ok": True,
            "provider": {
                "code": "000",
                "requestId": "VT-456",
                "response_description": "Transaction successful",
                "content": {
                    "transactions": {
                        "status": "delivered",
                        "transactionId": "TX-456",
                    }
                },
            },
        }
        payload = {
            "amount": "500.00",
            "network": "mtn",
            "phone": "08031234567",
            "client_reference": "AIR-IDEMP-001",
        }

        first = self.client.post(self.url, payload, format="json")
        second = self.client.post(self.url, payload, format="json")

        self.wallet.refresh_from_db()

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(AirtimeTransaction.objects.filter(client_reference="AIR-IDEMP-001").count(), 1)
        self.assertEqual(mock_purchase_airtime.call_count, 1)
        self.assertEqual(self.wallet.balance, Decimal("4500.00"))

    @patch("services.views.purchase_airtime")
    def test_airtime_purchase_rejects_when_wallet_balance_is_insufficient(self, mock_purchase_airtime):
        response = self.client.post(
            self.url,
            {
                "amount": "9000.00",
                "network": "mtn",
                "phone": "08031234567",
                "client_reference": "AIR-NOFUNDS-001",
            },
            format="json",
        )

        self.wallet.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"], "Insufficient funds.")
        self.assertFalse(AirtimeTransaction.objects.filter(client_reference="AIR-NOFUNDS-001").exists())
        self.assertEqual(self.wallet.balance, Decimal("5000.00"))
        self.assertEqual(mock_purchase_airtime.call_count, 0)

    @patch("services.views.purchase_airtime")
    def test_airtime_purchase_failure_refunds_wallet_and_marks_failed(self, mock_purchase_airtime):
        mock_purchase_airtime.return_value = {
            "ok": False,
            "provider": {
                "code": "999",
                "requestId": "VT-FAIL-1",
                "response_description": "Transaction failed",
                "content": {
                    "transactions": {
                        "status": "failed",
                        "transactionId": "TX-FAIL-1",
                    }
                },
            },
        }

        response = self.client.post(
            self.url,
            {
                "amount": "700.00",
                "network": "mtn",
                "phone": "08031234567",
                "client_reference": "AIR-FAIL-001",
            },
            format="json",
        )

        self.wallet.refresh_from_db()
        txn = AirtimeTransaction.objects.get(client_reference="AIR-FAIL-001")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(txn.status, "failed")
        self.assertEqual(self.wallet.balance, Decimal("5000.00"))
        self.assertEqual(self.wallet.locked, Decimal("0.00"))


class DataPurchaseTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="data@example.com", password="StrongPass123")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.balance = Decimal("3000.00")
        self.wallet.save(update_fields=["balance"])
        self.client.force_authenticate(self.user)
        self.url = reverse("data-purchase")

    @patch("services.views.purchase_data")
    def test_data_purchase_success_debits_wallet_and_marks_successful(self, mock_purchase_data):
        mock_purchase_data.return_value = {
            "ok": True,
            "provider": {
                "code": "000",
                "requestId": "VT-DATA-1",
                "response_description": "Data delivered",
                "content": {
                    "transactions": {
                        "status": "delivered",
                        "transactionId": "TX-DATA-1",
                    }
                },
            },
        }

        response = self.client.post(
            self.url,
            {
                "amount": "1200.00",
                "network": "mtn",
                "phone": "08031234567",
                "plan": "mtn-1gb",
                "client_reference": "DATA-REF-001",
            },
            format="json",
        )

        self.wallet.refresh_from_db()
        txn = DataTransaction.objects.get(client_reference="DATA-REF-001")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(txn.status, "successful")
        self.assertEqual(self.wallet.balance, Decimal("1800.00"))
        self.assertEqual(self.wallet.locked, Decimal("0.00"))

    @patch("services.views.purchase_data")
    def test_data_purchase_failure_refunds_wallet_and_marks_failed(self, mock_purchase_data):
        mock_purchase_data.return_value = {
            "ok": False,
            "provider": {
                "code": "999",
                "requestId": "VT-DATA-FAIL-1",
                "response_description": "Data failed",
                "content": {
                    "transactions": {
                        "status": "failed",
                        "transactionId": "TX-DATA-FAIL-1",
                    }
                },
            },
        }

        response = self.client.post(
            self.url,
            {
                "amount": "900.00",
                "network": "mtn",
                "phone": "08031234567",
                "plan": "mtn-500mb",
                "client_reference": "DATA-FAIL-001",
            },
            format="json",
        )

        self.wallet.refresh_from_db()
        txn = DataTransaction.objects.get(client_reference="DATA-FAIL-001")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(txn.status, "failed")
        self.assertEqual(self.wallet.balance, Decimal("3000.00"))
        self.assertEqual(self.wallet.locked, Decimal("0.00"))
