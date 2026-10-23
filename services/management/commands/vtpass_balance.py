# services/management/commands/vtpass_balance.py
import logging
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from services.vtpass import get_balance

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Show our VTpass wallet balance. Exits with an error (and logs one) when it's below "
        "--warn-below, so a scheduled run can alert before purchases start failing with code 018."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--warn-below", type=Decimal, default=Decimal("10000"), help="Alert threshold in naira (default: 10000)"
        )

    def handle(self, *args, **opts):
        balance = get_balance()
        if balance is None:
            logger.error("Could not read the VTpass wallet balance")
            raise CommandError("Could not read the VTpass wallet balance.")
        if balance < opts["warn_below"]:
            logger.error("VTpass wallet balance is low: %s (alert below %s)", balance, opts["warn_below"])
            raise CommandError(f"VTpass balance ₦{balance} is below ₦{opts['warn_below']}. Top up the VTpass wallet.")
        self.stdout.write(self.style.SUCCESS(f"VTpass balance: ₦{balance}"))
