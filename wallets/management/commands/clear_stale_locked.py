# wallets/management/commands/clear_stale_locked.py
from django.core.management.base import BaseCommand

from wallets import services as wallet_services
from wallets.models import Wallet

DEFAULT_NOTE = "Clear locked money left by the old lock endpoint / requery refund (not real money)"


class Command(BaseCommand):
    help = (
        "List locked money that no pending purchase accounts for. With --apply, remove it "
        "(recorded as a ledger adjustment). It was never real money, so it is not refunded."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Remove the stale amounts (default: only list them).")
        parser.add_argument("--note", default=DEFAULT_NOTE, help="Reason stored on each ledger adjustment.")

    def handle(self, *args, **opts):
        found = 0
        for wallet in Wallet.objects.select_related("user").filter(locked__gt=0).order_by("id"):
            stale = wallet_services.stale_locked(wallet)
            if stale <= 0:
                continue
            found += 1
            if opts["apply"]:
                wallet_services.clear_stale_locked(wallet.user, opts["note"])
                self.stdout.write(f"cleared {stale} from wallet {wallet.id} ({wallet.user.email})")
            else:
                self.stdout.write(f"wallet {wallet.id} ({wallet.user.email}): {stale} locked with no pending purchase")

        if not found:
            self.stdout.write(self.style.SUCCESS("No stale locked money."))
        elif not opts["apply"]:
            self.stdout.write(f"{found} wallet(s). Run again with --apply to remove it.")
