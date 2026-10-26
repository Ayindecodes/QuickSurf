# core/management/commands/purge_old_logs.py
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import LoginActivity
from notifications.models import EmailLog
from services.models import ProviderLog
from users.models import OneTimeCode


class Command(BaseCommand):
    help = (
        "Delete provider logs, email logs, login activity and one-time codes older than --days (default 180), "
        "and expired JWT records. Only lists counts unless --apply is given. Run on a schedule."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=180, help="Keep this many days of logs (default: 180)")
        parser.add_argument("--apply", action="store_true", help="Delete (default: only show what would go)")

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(days=opts["days"])
        targets = [
            ("provider logs", ProviderLog.objects.filter(timestamp__lt=cutoff)),
            ("email logs", EmailLog.objects.filter(created_at__lt=cutoff)),
            ("login activity", LoginActivity.objects.filter(created__lt=cutoff)),
            ("one-time codes", OneTimeCode.objects.filter(created_at__lt=cutoff)),
        ]
        for label, queryset in targets:
            count = queryset.count()
            if opts["apply"]:
                queryset.delete()
            self.stdout.write(f"{label}: {count} {'deleted' if opts['apply'] else 'older than the cutoff'}")

        if opts["apply"]:
            call_command("flushexpiredtokens", stdout=self.stdout)
        else:
            self.stdout.write("Nothing deleted. Run again with --apply.")
