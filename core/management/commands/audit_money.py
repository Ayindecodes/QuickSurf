# core/management/commands/audit_money.py
import csv
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Sum

from payments.models import PaymentIntent
from services.models import AirtimeTransaction, DataTransaction, ProviderLog
from wallets.models import Transaction, Wallet

ZERO = Decimal("0.00")
COLUMNS = ["section", "user", "reference", "status", "amount", "compare_amount", "difference", "detail"]
SECTION_TITLES = {
    "wallet_mismatch": "Wallets whose balance doesn't match their history",
    "underpriced_data": "Data purchases charged less than VTpass's price",
    "data_no_vtpass_amount": "Successful data purchases with no VTpass amount (check by hand)",
    "paystack_multi_credit": "Paystack payments likely credited more than once (estimate)",
    "unknown_outcome_refund": "Purchases refunded without a clear VTpass answer (may have been delivered)",
    "failure_codes": "Failed purchases by VTpass response code",
}
LABELS = {
    "user": "user",
    "reference": "ref",
    "status": "status",
    "amount": "amount",
    "compare_amount": "vs",
    "difference": "diff",
}


class Command(BaseCommand):
    help = (
        "Read-only money audit: wallets that don't add up, underpriced data purchases, "
        "Paystack payments credited more than once, and refunds with no clear VTpass outcome."
    )

    def add_arguments(self, parser):
        parser.add_argument("--csv", action="store_true", help="Print findings as CSV instead of a text report.")

    def handle(self, *args, **opts):
        # Never write: run in a transaction that is always rolled back (and READ ONLY on Postgres).
        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
            rows = [
                *self._wallet_mismatches(),
                *self._underpriced_data(),
                *self._paystack_multi_credits(),
                *self._unknown_outcome_refunds(),
                *self._failure_codes(),
            ]
            transaction.set_rollback(True)

        if opts["csv"]:
            writer = csv.DictWriter(self.stdout, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        else:
            self._write_report(rows)

    def _wallet_mismatches(self):
        """
        A wallet's total (balance + locked) should equal Paystack top-ups credited + staff credits
        and adjustments + manual locks - manual unlocks - successful purchases. Pending purchases only
        move money from balance to locked, and failed ones are refunded, so neither changes the total.
        """
        topups = _sum_by(PaymentIntent.objects.filter(status="success"), "user_id")
        spent = _sum_by(AirtimeTransaction.objects.filter(status="successful"), "user_id")
        pending = _sum_by(AirtimeTransaction.objects.filter(status="pending"), "user_id")
        for user_id, amount in _sum_by(DataTransaction.objects.filter(status="successful"), "user_id").items():
            spent[user_id] += amount
        for user_id, amount in _sum_by(DataTransaction.objects.filter(status="pending"), "user_id").items():
            pending[user_id] += amount

        ledger = defaultdict(lambda: defaultdict(lambda: ZERO))
        rows = (
            Transaction.objects.order_by()
            .values("wallet_id", "type")
            .annotate(total=Sum("amount"), delta=Sum("balance_delta"))
        )
        for row in rows:
            # Adjustments go either way, so use their signed delta rather than the amount.
            value = row["delta"] if row["type"] == Transaction.TYPE_ADJUSTMENT else row["total"]
            ledger[row["wallet_id"]][row["type"]] = value or ZERO

        for wallet in Wallet.objects.select_related("user").order_by("id"):
            moves = ledger[wallet.id]
            expected = (
                topups[wallet.user_id]
                + moves["credit"]
                + moves["adjustment"]
                + moves["lock"]
                - moves["unlock"]
                - spent[wallet.user_id]
            )
            actual = wallet.balance + wallet.locked
            if actual == expected:
                continue
            detail = f"balance {wallet.balance}, locked {wallet.locked}, pending purchases {pending[wallet.user_id]}"
            if wallet.locked != pending[wallet.user_id]:
                detail += "; locked doesn't match pending purchases"
            yield _row(
                "wallet_mismatch",
                wallet.user,
                f"wallet {wallet.id}",
                amount=actual,
                compare_amount=expected,
                difference=actual - expected,
                detail=detail,
            )

    def _underpriced_data(self):
        for txn in DataTransaction.objects.filter(status__in=["successful", "pending"]).select_related("user"):
            vtpass_amount = _vtpass_amount(txn.raw_response)
            if vtpass_amount is None:
                if txn.status == "successful":
                    yield _row(
                        "data_no_vtpass_amount",
                        txn.user,
                        txn.client_reference,
                        txn.status,
                        txn.amount,
                        detail=f"plan {txn.plan}",
                    )
            elif txn.amount < vtpass_amount:
                yield _row(
                    "underpriced_data",
                    txn.user,
                    txn.client_reference,
                    txn.status,
                    txn.amount,
                    compare_amount=vtpass_amount,
                    difference=txn.amount - vtpass_amount,
                    detail=f"plan {txn.plan}",
                )

    def _paystack_multi_credits(self):
        """
        Replays each payment's logged verify/webhook events through the pre-fix logic, where any
        non-success answer reset a credited payment so the next success credited it again.
        An estimate: confirm against the same user's wallet_mismatch row.
        """
        events = defaultdict(list)
        logs = (
            ProviderLog.objects.filter(service_type="paystack")
            .order_by("timestamp", "id")
            .values_list("client_reference", "endpoint", "status_code", "response_payload")
        )
        for reference, endpoint, status_code, body in logs.iterator():
            event = _paystack_event(endpoint, status_code, body)
            if event:
                events[reference].append(event)

        for intent in PaymentIntent.objects.filter(reference__in=list(events)).select_related("user"):
            credits = _count_old_credits(events[intent.reference])
            if credits > 1:
                yield _row(
                    "paystack_multi_credit",
                    intent.user,
                    intent.reference,
                    intent.status,
                    intent.amount,
                    compare_amount=intent.amount * credits,
                    difference=intent.amount * (credits - 1),
                    detail=f"credited about {credits} times",
                )

    def _unknown_outcome_refunds(self):
        """Failed (refunded) purchases where VTpass gave no response code or we hit a network error (999)."""
        for model, label in ((AirtimeTransaction, "airtime"), (DataTransaction, "data")):
            for txn in model.objects.filter(status="failed").select_related("user"):
                code = _vtpass_code(txn.raw_response)
                if code in (None, "999"):
                    yield _row(
                        "unknown_outcome_refund",
                        txn.user,
                        txn.client_reference,
                        f"{label} failed",
                        txn.amount,
                        detail=f"VTpass code {code or 'missing'}; check this request with VTpass",
                    )

    def _failure_codes(self):
        """Failed purchases grouped by VTpass code. One dominant code usually means a setup problem."""
        groups = defaultdict(lambda: {"count": 0, "total": ZERO, "description": ""})
        for model, label in ((AirtimeTransaction, "airtime"), (DataTransaction, "data")):
            for raw, amount in model.objects.filter(status="failed").values_list("raw_response", "amount").iterator():
                group = groups[(label, _vtpass_code(raw) or "missing")]
                group["count"] += 1
                group["total"] += amount
                if not group["description"] and isinstance(raw, dict):
                    group["description"] = str(raw.get("response_description") or "")[:60]

        for (label, code), group in sorted(groups.items(), key=lambda item: -item[1]["count"]):
            yield _row(
                "failure_codes",
                reference=code,
                status=label,
                amount=group["total"],
                detail=f"{group['count']} failed: {group['description']}",
            )

    def _write_report(self, rows):
        by_section = defaultdict(list)
        for row in rows:
            by_section[row["section"]].append(row)

        for section, title in SECTION_TITLES.items():
            found = by_section.get(section, [])
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{title}: {len(found)}"))
            for row in found:
                line = "  " + "  ".join(f"{label}={row[col]}" for col, label in LABELS.items() if row[col] != "")
                if row["detail"]:
                    line += f"  ({row['detail']})"
                self.stdout.write(line)
        self.stdout.write("\nRead-only: nothing was changed.")


def _row(section, user=None, reference="", status="", amount="", compare_amount="", difference="", detail=""):
    return {
        "section": section,
        "user": getattr(user, "email", "") if user else "",
        "reference": reference,
        "status": status,
        "amount": amount,
        "compare_amount": compare_amount,
        "difference": difference,
        "detail": detail,
    }


def _sum_by(queryset, field):
    totals = defaultdict(lambda: ZERO)
    for row in queryset.order_by().values(field).annotate(total=Sum("amount")):
        totals[row[field]] = row["total"] or ZERO
    return totals


def _vtpass_amount(raw):
    """What VTpass said the purchase cost, from the stored /pay response (None if it isn't there)."""
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    tx = content.get("transactions") if isinstance(content, dict) else None
    tx = tx if isinstance(tx, dict) else {}
    for value in (tx.get("amount"), tx.get("unit_price"), raw.get("amount")):
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if amount.is_finite() and amount > 0:
            return amount
    return None


def _vtpass_code(raw):
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code") or "").strip()
    return code or None


def _paystack_event(endpoint, status_code, body):
    """What the pre-fix code did with a logged Paystack call: "success", "pending", "failed" or None."""
    if endpoint == "webhook:charge.success":
        return "success"
    if endpoint == "webhook:charge.failed":
        return "failed"
    if endpoint != "/transaction/verify":
        return None
    body = body if isinstance(body, dict) else {}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    if (
        status_code == "200"
        and body.get("status")
        and data.get("status") == "success"
        and data.get("currency") == "NGN"
    ):
        return "success"
    if data.get("status") in ("failed", "abandoned"):
        return "failed"
    return "pending"


def _count_old_credits(events):
    credits, state = 0, None
    for event in events:
        if event == "success" and state != "success":
            credits += 1
        state = event
    return credits
