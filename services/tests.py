from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APITransactionTestCase

from services.models import AirtimeTransaction, DataTransaction
from rewards.models import LoyaltyLedger
from notifications.models import EmailLog
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


DATA_PLAN_PRICES = {"mtn-1gb": "1200.00", "mtn-500mb": "900.00"}

DATA_DELIVERED = {
    "ok": True,
    "provider": {
        "code": "000",
        "response_description": "TRANSACTION SUCCESSFUL",
        "content": {"transactions": {"status": "delivered", "transactionId": "TX-DATA"}},
    },
}


def _variations_response(prices, key="variations"):
    """Shape returned by services.vtpass.get_service_variations."""
    plans = [{"variation_code": code, "variation_amount": amount} for code, amount in prices.items()]
    return {"ok": True, "code": "000", "content": {key: plans}, "http_status": 200}


class DataPurchaseTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="data@example.com", password="StrongPass123")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.balance = Decimal("3000.00")
        self.wallet.save(update_fields=["balance"])
        self.client.force_authenticate(self.user)
        self.url = reverse("data-purchase")

        patcher = patch(
            "services.views.get_service_variations",
            return_value=_variations_response(DATA_PLAN_PRICES),
        )
        self.mock_variations = patcher.start()
        self.addCleanup(patcher.stop)

    @patch("services.views.purchase_data")
    def test_data_purchase_rejects_amount_that_does_not_match_vtpass_price(self, mock_purchase_data):
        response = self.client.post(
            self.url,
            {
                "amount": "1.00",
                "network": "mtn",
                "phone": "08031234567",
                "plan": "mtn-1gb",
                "client_reference": "DATA-CHEAP-001",
            },
            format="json",
        )

        self.wallet.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["price"], "1200.00")
        self.assertFalse(DataTransaction.objects.filter(client_reference="DATA-CHEAP-001").exists())
        self.assertEqual(self.wallet.balance, Decimal("3000.00"))
        self.assertEqual(mock_purchase_data.call_count, 0)

    @patch("services.views.purchase_data", return_value=DATA_DELIVERED)
    def test_data_purchase_without_amount_charges_vtpass_price(self, _mock_purchase_data):
        response = self.client.post(
            self.url,
            {
                "network": "mtn",
                "phone": "08031234567",
                "plan": "mtn-1gb",
                "client_reference": "DATA-NOAMT-001",
            },
            format="json",
        )

        self.wallet.refresh_from_db()
        txn = DataTransaction.objects.get(client_reference="DATA-NOAMT-001")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(txn.amount, Decimal("1200.00"))
        self.assertEqual(self.wallet.balance, Decimal("1800.00"))

    @patch("services.views.purchase_data", return_value=DATA_DELIVERED)
    def test_data_purchase_reads_misspelled_varations_key(self, _mock_purchase_data):
        self.mock_variations.return_value = _variations_response(DATA_PLAN_PRICES, key="varations")

        response = self.client.post(
            self.url,
            {"amount": "900.00", "network": "mtn", "phone": "08031234567", "plan": "mtn-500mb"},
            format="json",
        )

        self.wallet.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.wallet.balance, Decimal("2100.00"))

    @patch("services.views.purchase_data")
    def test_data_purchase_rejects_unknown_plan(self, mock_purchase_data):
        response = self.client.post(
            self.url,
            {"amount": "1.00", "network": "mtn", "phone": "08031234567", "plan": "mtn-made-up-plan"},
            format="json",
        )

        self.wallet.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.wallet.balance, Decimal("3000.00"))
        self.assertEqual(mock_purchase_data.call_count, 0)

    @patch("services.views.purchase_data")
    def test_data_purchase_fails_closed_when_vtpass_prices_unavailable(self, mock_purchase_data):
        self.mock_variations.return_value = {
            "ok": False,
            "code": "999",
            "response_description": "Network error",
            "content": None,
            "http_status": 0,
        }

        response = self.client.post(
            self.url,
            {
                "amount": "1200.00",
                "network": "mtn",
                "phone": "08031234567",
                "plan": "mtn-1gb",
                "client_reference": "DATA-NOPRICE-001",
            },
            format="json",
        )

        self.wallet.refresh_from_db()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(DataTransaction.objects.filter(client_reference="DATA-NOPRICE-001").exists())
        self.assertEqual(self.wallet.balance, Decimal("3000.00"))
        self.assertEqual(mock_purchase_data.call_count, 0)

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


@override_settings(PURCHASES_ENABLED=False)
class PurchaseKillSwitchTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="paused@example.com", password="StrongPass123")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.balance = Decimal("5000.00")
        self.wallet.save(update_fields=["balance"])
        self.client.force_authenticate(self.user)

    @patch("services.views.purchase_data")
    @patch("services.views.purchase_airtime")
    def test_purchases_are_refused_while_switched_off(self, mock_purchase_airtime, mock_purchase_data):
        airtime = self.client.post(
            reverse("airtime-purchase"),
            {"amount": "100.00", "network": "mtn", "phone": "08031234567"},
            format="json",
        )
        data = self.client.post(
            reverse("data-purchase"),
            {"network": "mtn", "phone": "08031234567", "plan": "mtn-1gb"},
            format="json",
        )

        self.wallet.refresh_from_db()

        self.assertEqual(airtime.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(data.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(self.wallet.balance, Decimal("5000.00"))
        self.assertEqual(mock_purchase_airtime.call_count, 0)
        self.assertEqual(mock_purchase_data.call_count, 0)


class RewardAndReceiptSignalTests(APITransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="signals@example.com", password="StrongPass123")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.balance = Decimal("5000.00")
        self.wallet.save(update_fields=["balance"])
        self.client.force_authenticate(self.user)
        self.url = reverse("airtime-purchase")

    @patch("services.views.purchase_airtime")
    def test_successful_airtime_creates_loyalty_entry_and_receipt_log_once(self, mock_purchase_airtime):
        mock_purchase_airtime.return_value = {
            "ok": True,
            "provider": {
                "code": "000",
                "requestId": "VT-SIG-1",
                "response_description": "Transaction successful",
                "content": {
                    "transactions": {
                        "status": "delivered",
                        "transactionId": "TX-SIG-1",
                    }
                },
            },
        }
        payload = {
            "amount": "1000.00",
            "network": "mtn",
            "phone": "08031234567",
            "client_reference": "AIR-SIG-001",
        }

        first = self.client.post(self.url, payload, format="json")
        second = self.client.post(self.url, payload, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)

        loyalty_entries = LoyaltyLedger.objects.filter(user=self.user, txn_type="airtime")
        receipt_logs = EmailLog.objects.filter(to=self.user.email, subject__icontains="Airtime")

        self.assertEqual(loyalty_entries.count(), 1)
        self.assertEqual(loyalty_entries.first().points, 10)
        self.assertEqual(receipt_logs.count(), 1)
