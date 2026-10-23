import io
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.test import TestCase

from notifications.utils import send_receipt_email


class EmailQueueTests(TestCase):
    def _run(self):
        call_command("send_queued_emails", stdout=io.StringIO())

    def test_receipts_are_queued_then_sent_by_the_command(self):
        log = send_receipt_email("ade@example.com", "Receipt - Airtime Purchase", "Thanks")

        self.assertEqual((log.status, len(mail.outbox)), ("queued", 0))
        self._run()
        self._run()

        log.refresh_from_db()
        self.assertEqual((log.status, log.attempts), ("sent", 1))
        self.assertEqual([m.to for m in mail.outbox], [["ade@example.com"]])

    def test_a_failed_send_is_retried_then_given_up_on(self):
        log = send_receipt_email("ade@example.com", "Receipt", "Thanks")

        with patch("notifications.management.commands.send_queued_emails.send_mail", side_effect=OSError("SMTP down")):
            self._run()
            log.refresh_from_db()
            self.assertEqual((log.status, log.attempts, log.error), ("queued", 1, "SMTP down"))
            self._run()
            self._run()
            self._run()

        log.refresh_from_db()
        self.assertEqual((log.status, log.attempts), ("failed", 3))
