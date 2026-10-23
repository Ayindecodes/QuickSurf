# core/management/commands/requery_pending.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from services.models import AirtimeTransaction, DataTransaction
from services.settlement import FINAL_STATUSES, settle_purchase
from services.vtpass import requery_status, strict_map_outcome


class Command(BaseCommand):
    help = "Re-query VTpass for initiated/pending transactions and settle them against the wallet."

    def add_arguments(self, parser):
        parser.add_argument("--age-mins", type=int, default=2,
                            help="Only requery txns older than N minutes (default: 2)")
        parser.add_argument("--max", type=int, default=200,
                            help="Max transactions to process (default: 200)")

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(minutes=opts["age_mins"])

        airtime_qs = AirtimeTransaction.objects.filter(
            status__in=["initiated", "pending"], timestamp__lte=cutoff
        ).order_by("timestamp")[: opts["max"]]

        remaining = max(0, opts["max"] - airtime_qs.count())
        data_qs = DataTransaction.objects.filter(
            status__in=["initiated", "pending"], timestamp__lte=cutoff
        ).order_by("timestamp")[: remaining]

        txns = list(airtime_qs) + list(data_qs)

        updated = 0
        refunded = 0

        for tx in txns:
            try:
                res = requery_status(tx.client_reference)
                body = res.get("provider", {}) if isinstance(res, dict) else {}
                new_status = strict_map_outcome(body)

                if new_status in FINAL_STATUSES:
                    # Capture on success, release (refund) on failure; the wallet ledger stops repeats.
                    tx = settle_purchase(
                        tx, new_status,
                        provider_status=body.get("response_description") or tx.provider_status,
                    )
                    updated += 1
                    if tx.status == "failed":
                        refunded += 1

            except Exception as e:
                self.stderr.write(f"{tx.client_reference}: {e}")

        self.stdout.write(self.style.SUCCESS(
            f"Done. Queried {len(txns)} txn(s). Updated {updated}, refunded {refunded}."
        ))
