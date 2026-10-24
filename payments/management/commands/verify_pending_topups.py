# payments/management/commands/verify_pending_topups.py
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from payments.models import PaymentIntent
from payments.paystack import verify as ps_verify
from payments.services import apply_verification, log_paystack, set_status_unless_final, trim_payload


class Command(BaseCommand):
    help = (
        "Re-verify top-ups that are still pending with Paystack: credit the paid ones (once) and "
        "mark ones still unpaid after --abandon-after-hours as abandoned. Safe to run on a schedule."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-age-mins", type=int, default=10, help="Only check top-ups older than N minutes (default: 10)"
        )
        parser.add_argument(
            "--abandon-after-hours",
            type=int,
            default=24,
            help="Mark top-ups still unpaid after N hours as abandoned (default: 24)",
        )
        parser.add_argument("--max", type=int, default=200, help="Max top-ups to check (default: 200)")

    def handle(self, *args, **opts):
        now = timezone.now()
        abandon_before = now - timedelta(hours=opts["abandon_after_hours"])
        intents = list(
            PaymentIntent.objects.select_related("user")
            .filter(status__in=["initialized", "pending"], created__lte=now - timedelta(minutes=opts["min_age_mins"]))
            .order_by("created")[: opts["max"]]
        )

        counts = Counter()
        for intent in intents:
            try:
                code, body = ps_verify(intent.reference)
            except Exception as e:
                self.stderr.write(f"{intent.reference}: {e}")
                counts["error"] += 1
                continue

            log_paystack(
                intent.user,
                intent.reference,
                "/transaction/verify",
                {"source": "verify_pending_topups"},
                trim_payload(body),
                code,
            )
            result, credited = apply_verification(intent, code, body)  # the full data: it identifies the payer
            if result == "pending" and intent.created < abandon_before:
                set_status_unless_final(intent, "abandoned")
                result = "abandoned"
            counts["credited" if credited else result] += 1

        summary = ", ".join(f"{name} {count}" for name, count in sorted(counts.items())) or "nothing to do"
        self.stdout.write(self.style.SUCCESS(f"Checked {len(intents)} top-up(s): {summary}."))
