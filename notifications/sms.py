# notifications/sms.py
"""
Text messages (phone verification codes). SMS_BACKEND picks how they go out:
  termii   Termii's SMS API (production): TERMII_API_KEY, TERMII_SENDER_ID, TERMII_BASE_URL, TERMII_CHANNEL
  console  printed to the logs, for local development; never prints anything in production
  locmem   kept in `outbox`, for tests
"""

import logging

import requests
from django.conf import settings

from core.privacy import mask_value

logger = logging.getLogger(__name__)

outbox: list[tuple[str, str]] = []  # (phone, message) when SMS_BACKEND=locmem


def international(phone: str) -> str:
    """0803... -> 234803... (Termii wants the country code, no +)."""
    return "234" + phone[1:] if phone.startswith("0") else phone


def send_sms(phone: str, message: str) -> bool:
    """Send `message` to a Nigerian number in local form (0803...). True if the provider accepted it."""
    backend = settings.SMS_BACKEND
    if backend == "locmem":
        outbox.append((phone, message))
        return True
    if backend == "console":
        if settings.IS_PRODUCTION:
            logger.error("SMS not sent: SMS_BACKEND is 'console' in production. Set it to 'termii'.")
            return False
        print(f"--- SMS to {phone} ---\n{message}\n---", flush=True)
        return True
    if backend == "termii":
        return _send_termii(phone, message)
    logger.error("SMS not sent: unknown SMS_BACKEND %r", backend)
    return False


def _send_termii(phone: str, message: str) -> bool:
    if not settings.TERMII_API_KEY or not settings.TERMII_SENDER_ID:
        logger.error("SMS not sent: TERMII_API_KEY and TERMII_SENDER_ID are required")
        return False
    try:
        response = requests.post(
            f"{settings.TERMII_BASE_URL}/api/sms/send",
            json={
                "api_key": settings.TERMII_API_KEY,
                "to": international(phone),
                "from": settings.TERMII_SENDER_ID,
                "sms": message,
                "type": "plain",
                "channel": settings.TERMII_CHANNEL,
            },
            timeout=(5, 15),
        )
        body = response.json() if response.headers.get("Content-Type", "").startswith("application/json") else {}
    except (requests.RequestException, ValueError) as e:
        logger.error("SMS to %s failed: %s", mask_value(phone), type(e).__name__)
        return False
    if response.status_code == 200 and (body.get("message_id") or str(body.get("code", "")).lower() == "ok"):
        return True
    # The message holds the code, so only the provider's status is logged.
    logger.error(
        "Termii refused an SMS to %s: HTTP %s %s", mask_value(phone), response.status_code, body.get("message")
    )
    return False
