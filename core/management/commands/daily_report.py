# core/management/commands/daily_report.py
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.alerts import notify_ops
from payments.models import PaymentIntent
from payments.paystack import kobo, list_successful
from services.models import AirtimeTransaction, DataTransaction
from wallets.models import Transaction

ZERO = Decimal("0")


class Command(BaseCommand):
    help = (
        "Money summary for one day (Lagos time; default yesterday) plus a Paystack reconciliation: payments "
        "Paystack took that we didn't credit, and credits Paystack has no payment for. Emails OPS_ALERT_EMAILS "
        "and exits with an error if they disagree. Run daily."
    )

    def add_arguments(self, parser):
        parser.add_argument("--date", type=date.fromisoformat, help="Day to report, YYYY-MM-DD (default: yesterday)")

    def handle(self, *args, **opts):
        day = opts["date"] or timezone.localdate() - timedelta(days=1)
        start = timezone.make_aware(datetime.combine(day, time.min))
        end = start + timedelta(days=1)

        lines = [f"QuickSurf daily report for {day} (Lagos time)", ""]
        lines += self._money(start, end)
        problems, reconciliation = self._reconcile_paystack(start, end)
        lines += ["", *reconciliation]

        self.stdout.write("\n".join(lines))
        subject = f"QuickSurf daily report {day}" + (f": {len(problems)} Paystack mismatch(es)" if problems else "")
        notify_ops(subject, lines)
        if problems:
            raise CommandError(f"{len(problems)} Paystack mismatch(es) on {day}: " + "; ".join(problems))

    def _money(self, start, end):
        in_day = {"timestamp__gte": start, "timestamp__lt": end}
        ledger = {
            row["type"]: row
            for row in Transaction.objects.filter(**in_day)
            .order_by()
            .values("type")
            .annotate(count=Count("id"), total=Sum("amount"), delta=Sum("balance_delta"))
        }

        def entries(entry_type):
            row = ledger.get(entry_type, {})
            return row.get("count", 0), row.get("total") or ZERO

        topups, topped_up = entries("topup")
        refunds = entries("release")[1] + entries("reversal")[1]
        adjusted = (ledger.get("adjustment") or {}).get("delta") or ZERO

        lines = [
            f"Top-ups credited:   {topups} totalling ₦{topped_up}",
            f"Refunds:            ₦{refunds} (failed or reversed purchases)",
            f"Staff adjustments:  ₦{adjusted}",
            f"New users:          {get_user_model().objects.filter(date_joined__gte=start, date_joined__lt=end).count()}",
            "",
            "Purchases (by status):",
        ]
        for label, model in (("Airtime", AirtimeTransaction), ("Data", DataTransaction)):
            rows = (
                model.objects.filter(**in_day).order_by().values("status").annotate(n=Count("id"), total=Sum("amount"))
            )
            summary = ", ".join(f"{row['status']} {row['n']} (₦{row['total']})" for row in rows) or "none"
            lines.append(f"  {label}: {summary}")
        pending = sum(
            model.objects.filter(status__in=["initiated", "pending"]).count()
            for model in (AirtimeTransaction, DataTransaction)
        )
        lines.append(
            f"Still pending now: {pending} purchase(s); top-ups waiting for review: "
            f"{PaymentIntent.objects.filter(status='review').count()}"
        )
        return lines

    def _reconcile_paystack(self, start, end):
        if not settings.PAYSTACK_SECRET_KEY:
            return [], ["Paystack reconciliation skipped: PAYSTACK_SECRET_KEY isn't set."]

        # Look a day either side, so a payment made just before midnight and credited just after still matches.
        paid = {tx["reference"]: tx for tx in list_successful(start - timedelta(days=1), end + timedelta(days=1))}
        paid_today = {ref: tx for ref, tx in paid.items() if start <= _paid_at(tx) < end}
        intents = PaymentIntent.objects.in_bulk(list(paid_today), field_name="reference")

        problems, waiting = [], []
        for reference, tx in paid_today.items():
            intent = intents.get(reference)
            if intent is None or intent.status not in ("success", "review"):
                problems.append(f"paid at Paystack but not credited: {reference} (₦{Decimal(tx['amount']) / 100})")
            elif intent.status == "review":
                waiting.append(reference)
            elif kobo(intent.amount) != int(tx["amount"]):
                problems.append(
                    f"amount differs for {reference}: credited ₦{intent.amount}, paid ₦{Decimal(tx['amount']) / 100}"
                )
        for intent in PaymentIntent.objects.filter(status="success", paid_at__gte=start, paid_at__lt=end):
            if intent.reference not in paid:
                problems.append(
                    f"credited but Paystack shows no successful payment: {intent.reference} (₦{intent.amount})"
                )

        lines = [f"Paystack reconciliation: {len(paid_today)} successful payment(s) at Paystack that day."]
        if waiting:
            lines.append(f"  Waiting for staff review: {', '.join(waiting)}")
        lines += [f"  MISMATCH: {problem}" for problem in problems] or ["  Everything matches."]
        return problems, lines


def _paid_at(tx) -> datetime:
    stamp = parse_datetime(str(tx.get("paid_at") or tx.get("paidAt") or tx.get("created_at") or ""))
    return stamp or datetime.min.replace(tzinfo=timezone.get_current_timezone())
