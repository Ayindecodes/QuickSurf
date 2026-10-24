# core/management/commands/ops_alerts.py
import io
import logging
from collections import Counter
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from django.utils import timezone

from core.alerts import BAD_WEBHOOK_SIGNATURES, event_count, notify_ops
from payments.models import PaymentIntent
from services.models import AirtimeTransaction, DataTransaction
from services.vtpass import get_balance
from users.models import User

logger = logging.getLogger(__name__)
PURCHASES = (AirtimeTransaction, DataTransaction)


class Command(BaseCommand):
    help = (
        "Look for problems that need a person: purchases stuck pending, a spike of failed purchases, "
        "top-ups waiting for review, a low VTpass balance, wallets that don't match their ledger and "
        "rejected Paystack webhooks. Logs and emails them (OPS_ALERT_EMAILS) and exits with an error "
        "if any. Run every 15 minutes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--pending-mins", type=int, default=30, help="Stuck after N minutes (default: 30)")
        parser.add_argument(
            "--balance-below", type=Decimal, default=Decimal("10000"), help="VTpass balance alert (default: 10000)"
        )
        parser.add_argument("--skip-balance", action="store_true", help="Don't ask VTpass for the balance")

    def handle(self, *args, **opts):
        checks = [
            self._stuck_purchases(opts["pending_mins"]),
            self._failure_spike(),
            self._topups_in_review(),
            None if opts["skip_balance"] else self._vtpass_balance(opts["balance_below"]),
            self._ledger_mismatches(),
            self._bad_webhook_signatures(),
            self._flagged_accounts(),
        ]
        problems = [problem for problem in checks if problem]
        if not problems:
            self.stdout.write(self.style.SUCCESS("All clear."))
            return

        for _, message in problems:
            logger.error("OPS ALERT: %s", message)
        notify_ops(
            f"QuickSurf: {len(problems)} problem(s) need attention",
            [f"- {message}" for _, message in problems],
            kinds=[kind for kind, _ in problems],
        )
        raise CommandError(" | ".join(message for _, message in problems))

    def _stuck_purchases(self, minutes):
        cutoff = timezone.now() - timedelta(minutes=minutes)
        stuck = [
            model.objects.filter(status__in=["initiated", "pending"], timestamp__lte=cutoff) for model in PURCHASES
        ]
        count = sum(queryset.count() for queryset in stuck)
        if not count:
            return None
        held = sum((queryset.aggregate(total=Sum("amount"))["total"] or 0 for queryset in stuck), Decimal("0"))
        return (
            "stuck_purchases",
            f"{count} purchase(s) pending for over {minutes} min (₦{held} held). See docs/runbooks/stuck-purchases.md.",
        )

    def _failure_spike(self, min_purchases=5, failure_share=0.5):
        since = timezone.now() - timedelta(hours=1)
        recent = [
            model.objects.filter(timestamp__gte=since, status__in=["successful", "failed"]) for model in PURCHASES
        ]
        total = sum(queryset.count() for queryset in recent)
        failed = [txn for queryset in recent for txn in queryset.filter(status="failed")]
        if total < min_purchases or len(failed) / total < failure_share:
            return None
        codes = Counter(str((txn.raw_response or {}).get("code") or "none") for txn in failed)
        code, times = codes.most_common(1)[0]
        return (
            "failure_spike",
            f"{len(failed)} of {total} purchases failed in the last hour (most common VTpass code: {code}, "
            f"{times}x). See docs/runbooks/vtpass-outage.md.",
        )

    def _flagged_accounts(self):
        flagged = User.objects.filter(flagged_at__isnull=False).order_by("-flagged_at")
        count = flagged.count()
        if not count:
            return None
        newest = flagged.first()
        return (
            "flagged_accounts",
            f"{count} account(s) flagged by the fraud checks are waiting for review, newest {newest.email}: "
            f"{newest.flag_reason}. Admin → Users → filter by Flagged; clear the flag once checked.",
        )

    def _topups_in_review(self):
        references = list(PaymentIntent.objects.filter(status="review").values_list("reference", flat=True)[:10])
        if not references:
            return None
        return (
            "topups_in_review",
            f"Top-up(s) paid with the wrong amount or currency, waiting for staff: {', '.join(references)}. "
            "See docs/runbooks/money-mismatch.md.",
        )

    def _vtpass_balance(self, threshold):
        balance = get_balance()
        if balance is None:
            return ("vtpass_balance", "Couldn't read the VTpass wallet balance. See docs/runbooks/vtpass-outage.md.")
        if balance < threshold:
            return (
                "vtpass_balance",
                f"VTpass wallet balance is ₦{balance}, below ₦{threshold}. Top it up before purchases fail (code 018).",
            )
        return None

    def _ledger_mismatches(self):
        try:
            call_command("check_wallets", stdout=io.StringIO())
        except CommandError as e:
            return ("ledger_mismatch", f"{e} Run `manage.py check_wallets`. See docs/runbooks/money-mismatch.md.")
        return None

    def _bad_webhook_signatures(self, threshold=10):
        count = event_count(BAD_WEBHOOK_SIGNATURES)
        if count < threshold:
            return None
        return (
            "bad_webhook_signatures",
            f"{count} Paystack webhooks rejected for a bad signature in the last hour: PAYSTACK_SECRET_KEY "
            "may not match Paystack (e.g. after rotating keys), or someone is forging them. "
            "See docs/runbooks/paystack-outage.md.",
        )
