import os
from django.core.mail import send_mail
from .models import EmailLog

def send_receipt_email(to_email: str, subject: str, body: str):
    log = EmailLog.objects.create(to=to_email, subject=subject, body=body, status="queued")
    try:
        # Only actually send when PROVIDER_MODE=LIVE
        if os.getenv("PROVIDER_MODE") == "LIVE":
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
