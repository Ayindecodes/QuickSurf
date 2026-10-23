# users/security.py
"""
Transaction PINs, emailed one-time codes and signing out everywhere.

PINs and codes are short, so their safety comes from attempt limits, not hash strength. They are
stored as an HMAC keyed with SECRET_KEY: a leaked database alone can't be used to recover them
(rotating SECRET_KEY means users set their PIN again).
"""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from notifications.utils import send_email
from .models import OneTimeCode, TransactionPin

logger = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=15)
CODE_RESEND_INTERVAL = timedelta(seconds=60)
WEAK_PINS = {d * 4 for d in "0123456789"} | {
    "0123", "1234", "2345", "3456", "4567", "5678", "6789", "9876", "8765", "7654", "6543", "5432", "4321", "3210",
}


def _digest(*parts) -> str:
    message = ":".join(str(part) for part in parts).encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


# ---------- Transaction PIN ----------
class PinError(Exception):
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


def validate_new_pin(pin: str) -> None:
    if not (isinstance(pin, str) and len(pin) == 4 and pin.isdigit()):
        raise ValueError("The PIN must be exactly 4 digits.")
    if pin in WEAK_PINS:
        raise ValueError("That PIN is too easy to guess. Avoid repeated or sequential digits.")


def set_pin(user, pin: str) -> None:
    validate_new_pin(pin)
    TransactionPin.objects.update_or_create(
        user=user, defaults={"pin_hash": _digest("pin", user.pk, pin), "failed_attempts": 0, "locked_until": None}
    )


def check_pin(user, pin: str) -> None:
    """Return if the PIN is right; otherwise raise PinError (pin_not_set, pin_locked or pin_invalid)."""
    now = timezone.now()
    with transaction.atomic():
        record = TransactionPin.objects.select_for_update().filter(user=user).first()
        if record is None:
            error = PinError("pin_not_set", "Set a transaction PIN before buying.")
        elif record.locked_until and record.locked_until > now:
            error = PinError("pin_locked", "Too many wrong PINs. Try again later.", locked_until=record.locked_until)
        elif pin and hmac.compare_digest(record.pin_hash, _digest("pin", user.pk, pin)):
            if record.failed_attempts or record.locked_until:
                record.failed_attempts, record.locked_until = 0, None
                record.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
            return
        else:
            record.failed_attempts += 1
            if record.failed_attempts >= TransactionPin.MAX_ATTEMPTS:
                record.failed_attempts = 0
                record.locked_until = now + timedelta(minutes=TransactionPin.LOCK_MINUTES)
                logger.warning("Transaction PIN locked for user %s after %s wrong attempts",
                               user.pk, TransactionPin.MAX_ATTEMPTS)
                error = PinError("pin_locked", "Too many wrong PINs. Try again later.", locked_until=record.locked_until)
            else:
                error = PinError("pin_invalid", "Wrong PIN.",
                                 attempts_left=TransactionPin.MAX_ATTEMPTS - record.failed_attempts)
            record.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
    raise error  # outside the atomic block, so the failed attempt is kept


# ---------- Emailed one-time codes ----------
def issue_code(user, purpose: str) -> str | None:
    """A new 6-digit code (older ones stop working), or None if one was sent less than a minute ago."""
    now = timezone.now()
    if OneTimeCode.objects.filter(user=user, purpose=purpose, created_at__gt=now - CODE_RESEND_INTERVAL).exists():
        return None
    OneTimeCode.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(used_at=now)
    code = f"{secrets.randbelow(1_000_000):06d}"
    OneTimeCode.objects.create(user=user, purpose=purpose, code_hash=_digest("code", user.pk, purpose, code),
                               expires_at=now + CODE_TTL)
    return code


def redeem_code(user, purpose: str, code: str) -> bool:
    """True if code is the user's current unexpired code for purpose (it is then used up). Wrong tries count."""
    now = timezone.now()
    with transaction.atomic():
        record = (OneTimeCode.objects.select_for_update()
                  .filter(user=user, purpose=purpose, used_at__isnull=True, expires_at__gt=now)
                  .order_by("-created_at").first())
        if record is None or record.attempts >= OneTimeCode.MAX_ATTEMPTS:
            return False
        if hmac.compare_digest(record.code_hash, _digest("code", user.pk, purpose, str(code or "").strip())):
            record.used_at = now
            record.save(update_fields=["used_at"])
            return True
        record.attempts += 1
        record.save(update_fields=["attempts"])
        return False


def send_code(user, purpose: str, code: str) -> bool:
    minutes = int(CODE_TTL.total_seconds() // 60)
    if purpose == OneTimeCode.PURPOSE_VERIFY_EMAIL:
        subject, action = "Verify your QuickSurf email", "verify your email"
    else:
        subject, action = "Reset your QuickSurf password", "reset your password"
    body = (f"Your code to {action} is {code}.\n\nIt expires in {minutes} minutes. "
            "If you didn't ask for it, you can ignore this email.\n\nQuickSurf")
    return send_email(user.email, subject, body, log_body=body.replace(code, "******"))


# ---------- Sessions ----------
def end_all_sessions(user) -> None:
    """Revoke every refresh token the user holds. Access tokens expire on their own (JWT_ACCESS_MINUTES)."""
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)
