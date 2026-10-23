# wallets/management/commands/check_wallets.py
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from wallets.models import Transaction, Wallet

ZERO = Decimal("0.00")


class Command(BaseCommand):
    help = "Check that every wallet equals the sum of its ledger entries. Exits with an error if any don't."

    def handle(self, *args, **opts):
        sums = {
            row["wallet_id"]: row
            for row in Transaction.objects.order_by()
            .values("wallet_id")
            .annotate(balance=Sum("balance_delta"), locked=Sum("locked_delta"))
        }

        checked = mismatched = 0
        for wallet in Wallet.objects.select_related("user").order_by("id"):
            checked += 1
            row = sums.get(wallet.id, {})
            ledger_balance = row.get("balance") or ZERO
            ledger_locked = row.get("locked") or ZERO
            if wallet.balance != ledger_balance or wallet.locked != ledger_locked:
                mismatched += 1
                self.stdout.write(
                    f"wallet {wallet.id} ({wallet.user.email}): "
                    f"balance {wallet.balance} vs ledger {ledger_balance}, "
                    f"locked {wallet.locked} vs ledger {ledger_locked}"
                )

        if mismatched:
            raise CommandError(f"{mismatched} of {checked} wallet(s) don't match their ledger.")
        self.stdout.write(self.style.SUCCESS(f"All {checked} wallet(s) match their ledger."))
