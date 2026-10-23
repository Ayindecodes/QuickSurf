import importlib
import io
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

import requests
from django.apps import apps
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase, APITransactionTestCase

from notifications.models import EmailLog
from rewards.models import LoyaltyLedger
from services.models import AirtimeTransaction, DataTransaction
from services.vtpass import generate_request_id, get_balance, purchase_airtime, purchase_data, strict_map_outcome
from users.models import User
from wallets import services as wallet_services
from wallets.models import Transaction, Wallet

VTPASS_DELIVERED = {
    "provider": {
        "code": "000",
        "response_description": "TRANSACTION SUCCESSFUL",
        "content": {"transactions": {"status": "delivered"}},
    }
}
VTPASS_PENDING = {
    "provider": {
        "code": "099",
        "response_description": "TRANSACTION IS PROCESSING",
        "content": {"transactions": {"status": "pending"}},
    }
}
VTPASS_FAILED = {
    "provider": {
        "code": "011",
        "response_description": "INVALID ARGUMENTS",
        "content": {"transactions": {"status": "failed"}},
    }
}


def _ledger_types(reference):
    return list(Transaction.objects.filter(reference=reference).order_by("id").values_list("type", flat=True))


@override_settings(TRANSACTION_PIN_REQUIRED=False)  # about settlement/pricing, not the PIN (see TransactionPinTests)
class PurchaseSettlementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="settle@example.com", password="StrongPass123")
        wallet_services.credit_topup(self.user, Decimal("1000.00"), f"SEED-{self.user.id}")
        self.client.force_authenticate(self.user)
        self.url = reverse("airtime-purchase")

    def _buy(self, client_reference, provider_response):
        with patch("services.views.purchase_airtime", return_value=provider_response):
            return self.client.post(
                self.url,
                {"amount": "300", "network": "mtn", "phone": "08031234567", "client_reference": client_reference},
                format="json",
            )

    def _assert_wallet(self, balance, locked):
        wallet = Wallet.objects.get(user=self.user)
        self.assertEqual((wallet.balance, wallet.locked), (Decimal(balance), Decimal(locked)))
        call_command("check_wallets", stdout=io.StringIO())

    def test_successful_purchase_holds_then_captures(self):
        self._buy("SET-1", VTPASS_DELIVERED)

        self.assertEqual(_ledger_types("airtime:SET-1"), ["hold", "capture"])
        self._assert_wallet("700.00", "0.00")

    def test_failed_purchase_holds_then_releases(self):
        self._buy("SET-2", VTPASS_FAILED)

        self.assertEqual(_ledger_types("airtime:SET-2"), ["hold", "release"])
        self._assert_wallet("1000.00", "0.00")

    def test_status_check_settles_a_pending_purchase_once_then_stops_asking_vtpass(self):
        self._buy("SET-3", VTPASS_PENDING)
        self._assert_wallet("700.00", "300.00")
        url = reverse("purchase-status", args=["SET-3"])

        with patch("services.views.requery_status", return_value=VTPASS_DELIVERED):
            first = self.client.get(url)
        with patch("services.views.requery_status", return_value=VTPASS_FAILED) as later_requery:
            second = self.client.get(url)

        self.assertEqual(first.data["status"], "successful")
        self.assertEqual(second.data["status"], "successful")
        self.assertEqual(later_requery.call_count, 0)
        self.assertEqual(_ledger_types("airtime:SET-3"), ["hold", "capture"])
        self._assert_wallet("700.00", "0.00")

    def test_airtime_amount_must_be_whole_naira(self):
        with patch("services.views.purchase_airtime") as mock_purchase_airtime:
            response = self.client.post(
                self.url, {"amount": "50.50", "network": "mtn", "phone": "08031234567"}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(mock_purchase_airtime.call_count, 0)


@override_settings(TRANSACTION_PIN_REQUIRED=False)  # about settlement/pricing, not the PIN (see TransactionPinTests)
class AirtimePurchaseTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="service@example.com", password="StrongPass123")
        wallet_services.credit_topup(self.user, Decimal("5000.00"), f"SEED-{self.user.id}")
        self.wallet = Wallet.objects.get(user=self.user)
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
        self.assertRegex(txn.provider_request_id, r"^\d{12}[0-9A-F]{12}$")  # VTpass request ID format
        self.assertEqual(mock_purchase_airtime.call_args.kwargs["request_id"], txn.provider_request_id)
        self.assertEqual(txn.provider_reference, "TX-123")  # VTpass's own transactionId
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


@override_settings(TRANSACTION_PIN_REQUIRED=False)  # about settlement/pricing, not the PIN (see TransactionPinTests)
class DataPurchaseTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="data@example.com", password="StrongPass123")
        wallet_services.credit_topup(self.user, Decimal("3000.00"), f"SEED-{self.user.id}")
        self.wallet = Wallet.objects.get(user=self.user)
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
        self.assertEqual(_ledger_types("data:DATA-NOAMT-001"), ["hold", "capture"])

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
        wallet_services.credit_topup(self.user, Decimal("5000.00"), f"SEED-{self.user.id}")
        self.wallet = Wallet.objects.get(user=self.user)
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


@override_settings(TRANSACTION_PIN_REQUIRED=False)  # about settlement/pricing, not the PIN (see TransactionPinTests)
class RewardAndReceiptSignalTests(APITransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="signals@example.com", password="StrongPass123")
        wallet_services.credit_topup(self.user, Decimal("5000.00"), f"SEED-{self.user.id}")
        self.wallet = Wallet.objects.get(user=self.user)
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


# ===================== VTpass client (https://www.vtpass.com/documentation/) =====================
class VTpassClientTests(SimpleTestCase):
    def test_request_id_starts_with_the_lagos_date_and_minute(self):
        lagos_now = datetime.now(dt_timezone.utc) + timedelta(hours=1)
        request_id = generate_request_id()

        self.assertRegex(request_id, r"^\d{12}[0-9A-F]{12}$")
        self.assertIn(
            request_id[:12],
            {lagos_now.strftime("%Y%m%d%H%M"), (lagos_now + timedelta(minutes=1)).strftime("%Y%m%d%H%M")},
        )

    def test_outcomes_follow_the_vtpass_response_codes(self):
        cases = [
            ({"code": "000", "content": {"transactions": {"status": "delivered"}}}, "successful"),
            ({"code": "000", "content": {"transactions": {"status": "pending"}}}, "pending"),
            ({"code": "000", "content": {"transactions": {"status": "initiated"}}}, "pending"),
            ({"code": "099"}, "pending"),  # processing
            ({"code": "014"}, "pending"),  # request ID already used: the first attempt may have gone through
            ({"code": "083"}, "pending"),  # system error
            ({"code": "999", "response_description": "Network error"}, "pending"),
            ({"code": "123"}, "pending"),  # unknown code
            ({}, "pending"),
            ({"code": "016"}, "failed"),  # transaction failed
            ({"code": "018"}, "failed"),  # low wallet balance
            ({"code": "085"}, "failed"),  # improper request ID
            ({"code": "015"}, "failed"),  # requery: VTpass never received it
            ({"code": "000", "content": {"transactions": {"status": "failed"}}}, "failed"),
            ({"code": "040", "content": {"transactions": {"status": "reversed"}}}, "reversed"),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(strict_map_outcome(body), expected)

    def test_9mobile_uses_vtpass_etisalat_service_ids(self):
        with patch("services.vtpass._request", return_value=(200, {"code": "000"})) as send:
            purchase_airtime("9mobile", "08091234567", 100)
            purchase_data("9mobile", "08091234567", "eti-100")

        self.assertEqual([c.kwargs["json"]["serviceID"] for c in send.call_args_list], ["etisalat", "etisalat-data"])

    def test_pay_is_not_resent_after_a_read_timeout(self):
        with (
            patch("services.vtpass.Session.request", side_effect=requests.exceptions.ReadTimeout()) as send,
            patch("services.vtpass.time.sleep"),
        ):
            result = purchase_airtime("mtn", "08031234567", 100, request_id="202610231200ABCDEF123456")

        self.assertEqual(send.call_count, 1)
        self.assertEqual(result["state"], "pending")

    def test_pay_is_retried_after_a_connect_timeout(self):
        with (
            patch("services.vtpass.Session.request", side_effect=requests.exceptions.ConnectTimeout()) as send,
            patch("services.vtpass.time.sleep"),
        ):
            purchase_airtime("mtn", "08031234567", 100, request_id="202610231200ABCDEF123456")

        self.assertEqual(send.call_count, 2)

    def test_balance_is_read_from_the_response_contents(self):
        with patch(
            "services.vtpass._request", return_value=(200, {"code": 1, "contents": {"balance": 1081.8199999998}})
        ):
            self.assertEqual(get_balance(), Decimal("1081.82"))


@override_settings(TRANSACTION_PIN_REQUIRED=False)  # about settlement/pricing, not the PIN (see TransactionPinTests)
class VTpassPurchaseTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="vt@example.com", password="StrongPass123")
        wallet_services.credit_topup(self.user, Decimal("1000.00"), f"SEED-{self.user.id}")
        self.client.force_authenticate(self.user)

    def _buy(self, client_reference, provider_response, amount="300"):
        with patch("services.views.purchase_airtime", return_value=provider_response):
            return self.client.post(
                reverse("airtime-purchase"),
                {"amount": amount, "network": "mtn", "phone": "08031234567", "client_reference": client_reference},
                format="json",
            )

    def _wallet(self):
        wallet = Wallet.objects.get(user=self.user)
        return wallet.balance, wallet.locked

    def _callback(self, request_id, claimed_status="delivered"):
        payload = {
            "type": "transaction-update",
            "data": {"code": "000", "requestId": request_id, "content": {"transactions": {"status": claimed_status}}},
        }
        return self.client.post(reverse("vtpass-callback"), payload, format="json")

    def test_a_timeout_keeps_the_money_held_instead_of_refunding(self):
        with (
            patch("services.vtpass.Session.request", side_effect=requests.exceptions.ReadTimeout()),
            patch("services.vtpass.time.sleep"),
        ):
            self.client.post(
                reverse("airtime-purchase"),
                {"amount": "300", "network": "mtn", "phone": "08031234567", "client_reference": "TO-1"},
                format="json",
            )

        self.assertEqual(AirtimeTransaction.objects.get(client_reference="TO-1").status, "pending")
        self.assertEqual(_ledger_types("airtime:TO-1"), ["hold"])
        self.assertEqual(self._wallet(), (Decimal("700.00"), Decimal("300.00")))

    def test_status_requeries_by_request_id_at_most_every_30_seconds(self):
        self._buy("ST-1", VTPASS_PENDING)
        txn = AirtimeTransaction.objects.get(client_reference="ST-1")
        url = reverse("purchase-status", args=["ST-1"])

        with patch("services.views.requery_status", return_value=VTPASS_PENDING) as requery:
            self.client.get(url)
            self.client.get(url)

        self.assertEqual(requery.call_count, 1)
        self.assertEqual(requery.call_args.args[0], txn.provider_request_id)

    def test_callback_settles_by_asking_vtpass_not_by_trusting_the_payload(self):
        self._buy("CB-1", VTPASS_PENDING)
        txn = AirtimeTransaction.objects.get(client_reference="CB-1")

        with patch("services.views.requery_status", return_value=VTPASS_FAILED) as requery:
            response = self._callback(txn.provider_request_id, claimed_status="delivered")

        txn.refresh_from_db()
        self.assertEqual(response.data, {"response": "success"})
        self.assertEqual(requery.call_args.args[0], txn.provider_request_id)
        self.assertEqual(txn.status, "failed")
        self.assertEqual(self._wallet(), (Decimal("1000.00"), Decimal("0.00")))

    def test_callback_for_an_unknown_request_id_is_acknowledged_and_ignored(self):
        with patch("services.views.requery_status") as requery:
            response = self._callback("202610231200NOTOURS00000")

        self.assertEqual(response.data, {"response": "success"})
        self.assertEqual(requery.call_count, 0)

    def test_a_reversal_after_delivery_refunds_the_user_once(self):
        self._buy("REV-1", VTPASS_DELIVERED)
        txn = AirtimeTransaction.objects.get(client_reference="REV-1")
        reversed_body = {
            "provider": {
                "code": "040",
                "response_description": "TRANSACTION REVERSAL TO WALLET",
                "content": {"transactions": {"status": "reversed"}},
            }
        }

        with patch("services.views.requery_status", return_value=reversed_body):
            self._callback(txn.provider_request_id, claimed_status="reversed")
            self._callback(txn.provider_request_id, claimed_status="reversed")

        txn.refresh_from_db()
        self.assertEqual(txn.status, "reversed")
        self.assertEqual(_ledger_types("airtime:REV-1"), ["hold", "capture", "reversal"])
        self.assertEqual(self._wallet(), (Decimal("1000.00"), Decimal("0.00")))
        call_command("check_wallets", stdout=io.StringIO())

    def test_another_users_client_reference_is_refused_without_moving_money(self):
        other = User.objects.create_user(email="other@example.com", password="StrongPass123")
        wallet_services.credit_topup(other, Decimal("1000.00"), f"SEED-{other.id}")
        self._buy("SHARED-1", VTPASS_DELIVERED)
        self.client.force_authenticate(other)

        response = self._buy("SHARED-1", VTPASS_DELIVERED)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Wallet.objects.get(user=other).balance, Decimal("1000.00"))

    @override_settings(DAILY_PURCHASE_LIMIT=500)
    def test_purchases_stop_at_the_daily_limit(self):
        first = self._buy("DAY-1", VTPASS_DELIVERED)
        second = self._buy("DAY-2", VTPASS_DELIVERED)

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Daily purchase limit", second.data["error"])
        self.assertEqual(self._wallet(), (Decimal("700.00"), Decimal("0.00")))

    @override_settings(AIRTIME_MIN_AMOUNT=50, AIRTIME_MAX_AMOUNT=500)
    def test_airtime_must_be_within_the_allowed_amounts(self):
        too_small = self._buy("MIN-1", VTPASS_DELIVERED, amount="20")
        too_big = self._buy("MAX-1", VTPASS_DELIVERED, amount="600")

        self.assertEqual(too_small.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(too_big.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self._wallet(), (Decimal("1000.00"), Decimal("0.00")))

    @override_settings(
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": "redis://127.0.0.1:9/0"}
        }
    )
    def test_purchases_still_work_when_the_cache_is_down(self):
        with self.assertLogs("services.views", level="ERROR"):
            response = self._buy("NOCACHE-1", VTPASS_DELIVERED)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(_ledger_types("airtime:NOCACHE-1"), ["hold", "capture"])


@override_settings(TRANSACTION_PIN_REQUIRED=False)  # about settlement/pricing, not the PIN (see TransactionPinTests)
class DataPlanCatalogTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email="plans@example.com", password="StrongPass123")
        wallet_services.credit_topup(self.user, Decimal("3000.00"), f"SEED-{self.user.id}")
        self.client.force_authenticate(self.user)
        patcher = patch(
            "services.views.get_service_variations",
            return_value=_variations_response(DATA_PLAN_PRICES, key="varations"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_plans_are_listed_at_the_vtpass_price_by_default(self):
        response = self.client.get(reverse("data-plans", args=["mtn"]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {p["code"]: p["price"] for p in response.data["plans"]}, {"mtn-1gb": "1200.00", "mtn-500mb": "900.00"}
        )

    @override_settings(DATA_PRICE_MARKUP_PERCENT=Decimal("10"))
    @patch("services.views.purchase_data", return_value=DATA_DELIVERED)
    def test_a_markup_is_shown_in_the_catalog_and_charged(self, _mock_purchase_data):
        listed = self.client.get(reverse("data-plans", args=["mtn"]))
        bought = self.client.post(
            reverse("data-purchase"),
            {"amount": "1320.00", "network": "mtn", "phone": "08031234567", "plan": "mtn-1gb"},
            format="json",
        )

        self.assertEqual({p["code"]: p["price"] for p in listed.data["plans"]}["mtn-1gb"], "1320.00")
        self.assertEqual(bought.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1680.00"))

    def test_unknown_network_is_not_found(self):
        self.assertEqual(self.client.get(reverse("data-plans", args=["mtel"])).status_code, status.HTTP_404_NOT_FOUND)


class VTpassOpsTests(TestCase):
    def test_low_vtpass_balance_raises_an_alert(self):
        with patch("services.management.commands.vtpass_balance.get_balance", return_value=Decimal("5000.00")):
            with self.assertRaises(CommandError):
                call_command("vtpass_balance", "--warn-below", "10000", stdout=io.StringIO())
        with patch("services.management.commands.vtpass_balance.get_balance", return_value=Decimal("20000.00")):
            out = io.StringIO()
            call_command("vtpass_balance", "--warn-below", "10000", stdout=out)
        self.assertIn("20000.00", out.getvalue())

    def test_purchases_stuck_pending_are_logged_as_errors(self):
        user = User.objects.create_user(email="stuck@example.com", password="StrongPass123")
        txn = AirtimeTransaction.objects.create(
            user=user,
            network="mtn",
            phone="08031234567",
            amount=Decimal("100"),
            status="pending",
            client_reference="STUCK-1",
            provider_request_id="202610231200STUCK0000000",
        )
        AirtimeTransaction.objects.filter(pk=txn.pk).update(timestamp=timezone.now() - timedelta(hours=2))

        with (
            patch("core.management.commands.requery_pending.requery_status", return_value=VTPASS_PENDING),
            self.assertLogs("core.management.commands.requery_pending", level="ERROR") as logs,
        ):
            call_command("requery_pending", stdout=io.StringIO())

        self.assertIn("STUCK-1", logs.output[0])

    def test_migration_records_the_request_id_that_was_actually_sent(self):
        user = User.objects.create_user(email="legacy@example.com", password="StrongPass123")
        txn = AirtimeTransaction.objects.create(
            user=user,
            network="mtn",
            phone="08031234567",
            amount=Decimal("100"),
            status="successful",
            client_reference="AIRTIME_7_3",
            provider_request_id="echoed-value",
            raw_response={"code": "000", "content": {"transactions": {"status": "delivered", "transactionId": "TX-9"}}},
        )

        importlib.import_module("services.migrations.0009_vtpass_request_ids").use_sent_request_ids(apps, None)

        txn.refresh_from_db()
        self.assertEqual(txn.provider_request_id, "AIRTIME_7_3")
        self.assertEqual(txn.provider_reference, "TX-9")
