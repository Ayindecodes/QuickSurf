# notifications/management/commands/send_queued_emails.py
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from notifications.models import EmailLog

MAX_ATTEMPTS = 3


class Command(BaseCommand):
    help = (
        "Send queued emails (purchase receipts). A failed send is retried on later runs, up to "
        f"{MAX_ATTEMPTS} attempts. Run every minute or two."
    )

    def add_arguments(self, parser):
        parser.add_argument("--max", type=int, default=100, help="Emails per run (default: 100)")

    def handle(self, *args, **opts):
        sent = failed = 0
        with transaction.atomic():
            queue = EmailLog.objects.filter(status="queued").order_by("created_at")
            if connection.features.has_select_for_update_skip_locked:
                queue = queue.select_for_update(skip_locked=True)  # two overlapping runs never send twice
            for log in queue[: opts["max"]]:
                log.attempts += 1
                try:
                    send_mail(log.subject, log.body, None, [log.to], fail_silently=False)
                    log.status, log.error = "sent", ""
                    sent += 1
                except Exception as e:
                    log.status = "failed" if log.attempts >= MAX_ATTEMPTS else "queued"
                    log.error = str(e)[:1000]
                    failed += 1
                log.save(update_fields=["status", "error", "attempts"])
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} email(s); {failed} failed this run."))
