import logging

from django.core.mail import send_mail

from .models import EmailLog

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body: str, log_body: str | None = None) -> bool:
    """
    Send an email now and record it in EmailLog. Pass log_body to store different text than was
    sent (e.g. with a one-time code hidden). Returns whether it was sent.
    """
    log = EmailLog.objects.create(
        to=to_email, subject=subject, body=body if log_body is None else log_body, status="queued"
    )
    try:
        send_mail(subject, body, None, [to_email], fail_silently=False)
        log.status = "sent"
    except Exception as e:
        logger.exception("Email to %s failed", to_email)
        log.status = "failed"
        log.error = str(e)[:1000]
    log.save(update_fields=["status", "error"])
    return log.status == "sent"


def send_receipt_email(to_email: str, subject: str, body: str) -> EmailLog:
    """Queue a receipt; `manage.py send_queued_emails` sends it, so purchases never wait on the mail server."""
    return EmailLog.objects.create(to=to_email, subject=subject, body=body, status="queued")
