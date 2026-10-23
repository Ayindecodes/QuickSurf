import csv
import io
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from payments.models import PaymentIntent
from services.models import AirtimeTransaction, DataTransaction, ProviderLog
from users.models import User
from wallets import services as wallet_services
from wallets.models import Wallet

PHONE = "08031234567"


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
        AirtimeTransaction.objects.create(user=self.user, network="mtn", phone=PHONE, amount=Decimal("300.00"),
                                          status="successful", client_reference="AIR-A-1")
        AirtimeTransaction.objects.create(user=self.user, network="mtn", phone=PHONE, amount=Decimal("200.00"),
                                          status="pending", client_reference="AIR-A-2")
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
            user=self.user, network="mtn", phone=PHONE, plan="mtn-1gb", amount=Decimal("1.00"),
            status="successful", client_reference="DATA-A-1",
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
            ProviderLog.objects.create(service_type="paystack", client_reference="PAY-A-1",
                                       endpoint="/transaction/verify", status_code=status_code,
                                       request_payload={}, response_payload=body)

        [row] = _section(_audit_rows(), "paystack_multi_credit")

        self.assertEqual(row["reference"], "PAY-A-1")
        self.assertEqual(Decimal(row["difference"]), Decimal("1500.00"))

    def test_refund_after_network_error_is_flagged_and_failures_are_grouped_by_code(self):
        AirtimeTransaction.objects.create(user=self.user, network="mtn", phone=PHONE, amount=Decimal("500.00"),
                                          status="failed", client_reference="AIR-A-3",
                                          raw_response={"code": "999", "response_description": "Network error"})
        AirtimeTransaction.objects.create(user=self.user, network="mtn", phone=PHONE, amount=Decimal("100.00"),
                                          status="failed", client_reference="AIR-A-4",
                                          raw_response={"code": "011", "response_description": "INVALID ARGUMENTS"})

        rows = _audit_rows()
        [refund] = _section(rows, "unknown_outcome_refund")
        codes = {row["reference"] for row in _section(rows, "failure_codes")}

        self.assertEqual(refund["reference"], "AIR-A-3")
        self.assertEqual(codes, {"999", "011"})

    def test_requery_pending_refund_also_frees_the_locked_money(self):
        wallet_services.credit_topup(self.user, Decimal("1000.00"), "PAY-RQ-1")
        wallet_services.hold(self.user, Decimal("300.00"), "airtime:RQ-1")
        txn = AirtimeTransaction.objects.create(user=self.user, network="mtn", phone=PHONE, amount=Decimal("300.00"),
                                                status="pending", client_reference="RQ-1")
        failed = {"provider": {"code": "011", "content": {"transactions": {"status": "failed"}}}}

        with patch("core.management.commands.requery_pending.requery_status", return_value=failed):
            call_command("requery_pending", "--age-mins", "0", stdout=io.StringIO())

        txn.refresh_from_db()
        self.wallet.refresh_from_db()
        self.assertEqual(txn.status, "failed")
        self.assertEqual((self.wallet.balance, self.wallet.locked), (Decimal("1000.00"), Decimal("0.00")))
        call_command("check_wallets", stdout=io.StringIO())

    def test_text_report_lists_every_section(self):
        out = io.StringIO()
        call_command("audit_money", stdout=out)

        self.assertIn("Wallets whose balance doesn't match their history: 0", out.getvalue())
        self.assertIn("Read-only: nothing was changed.", out.getvalue())
