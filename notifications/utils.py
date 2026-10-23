import logging

from django.conf import settings
from django.core.mail import send_mail

from .models import EmailLog

logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body: str, log_body: str | None = None) -> bool:
    """
    Send an email now and record it in EmailLog. Pass log_body to store different text than was
    sent (e.g. with a one-time code hidden). Returns whether it was sent.
    """
    log = EmailLog.objects.create(to=to_email, subject=subject, body=body if log_body is None else log_body,
                                  status="queued")
    try:
        send_mail(subject, body, None, [to_email], fail_silently=False)
        log.status = "sent"
    except Exception as e:
        logger.exception("Email to %s failed", to_email)
        log.status = "failed"
        log.error = str(e)[:1000]
    log.save(update_fields=["status", "error"])
    return log.status == "sent"


def send_receipt_email(to_email: str, subject: str, body: str):
    log = EmailLog.objects.create(to=to_email, subject=subject, body=body, status="queued")
    try:
        # Only actually send when PROVIDER_MODE=LIVE
        if settings.PROVIDER_MODE == "LIVE":
            send_mail(subject, body, None, [to_email], fail_silently=False)
            log.status = "sent"
        else:
            # In MOCK/SANDBOX, just log as "sent" without sending
            log.status = "sent"
    except Exception as e:
        log.status = "failed"
        log.error = str(e)
    finally:
        log.save()
