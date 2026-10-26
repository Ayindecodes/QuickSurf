# core/management/commands/requery_pending.py
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from services.models import AirtimeTransaction, DataTransaction
from services.settlement import FINAL_STATUSES, settle_purchase
from services.vtpass import requery_status, strict_map_outcome, transaction_id

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Re-query VTpass for initiated/pending transactions and settle them against the wallet. "
        "Logs an error for any purchase still pending after --alert-after-mins. Run on a schedule."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--age-mins", type=int, default=2, help="Only requery txns older than N minutes (default: 2)"
        )
        parser.add_argument("--max", type=int, default=200, help="Max transactions to process (default: 200)")
        parser.add_argument(
            "--alert-after-mins",
            type=int,
            default=30,
            help="Log an error for purchases still pending after N minutes (default: 30)",
        )

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(minutes=opts["age_mins"])

        airtime_qs = AirtimeTransaction.objects.filter(
            status__in=["initiated", "pending"], timestamp__lte=cutoff
        ).order_by("timestamp")[: opts["max"]]

        remaining = max(0, opts["max"] - airtime_qs.count())
        data_qs = DataTransaction.objects.filter(status__in=["initiated", "pending"], timestamp__lte=cutoff).order_by(
            "timestamp"
        )[:remaining]

        txns = list(airtime_qs) + list(data_qs)

        updated = 0
        refunded = 0

        for tx in txns:
            try:
                res = requery_status(tx.provider_request_id or tx.client_reference)
                body = res.get("provider", {}) if isinstance(res, dict) else {}
                new_status = strict_map_outcome(body)

                if new_status in FINAL_STATUSES:
                    # Capture on success, release (refund) on failure/reversal; the wallet ledger stops repeats.
                    tx = settle_purchase(
                        tx,
                        new_status,
                        provider_status=body.get("response_description") or tx.provider_status,
                        provider_reference=transaction_id(body) or tx.provider_reference,
                    )
                    updated += 1
                    if tx.status in ("failed", "reversed"):
                        refunded += 1

            except Exception as e:
                self.stderr.write(f"{tx.client_reference}: {e}")

        stuck = 0
        alert_cutoff = timezone.now() - timedelta(minutes=opts["alert_after_mins"])
        for model in (AirtimeTransaction, DataTransaction):
            for tx in model.objects.filter(status__in=["initiated", "pending"], timestamp__lte=alert_cutoff):
                stuck += 1
                logger.error(
                    "Purchase %s (VTpass request %s, ₦%s) still pending since %s",
                    tx.client_reference,
                    tx.provider_request_id,
                    tx.amount,
                    tx.timestamp,
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Queried {len(txns)} txn(s). Updated {updated}, refunded {refunded}. "
                f"Still pending after {opts['alert_after_mins']} min: {stuck}."
            )
        )
