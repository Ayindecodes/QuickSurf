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
    "0123",
    "1234",
    "2345",
    "3456",
    "4567",
    "5678",
    "6789",
    "9876",
    "8765",
    "7654",
    "6543",
    "5432",
    "4321",
    "3210",
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
                logger.warning(
                    "Transaction PIN locked for user %s after %s wrong attempts", user.pk, TransactionPin.MAX_ATTEMPTS
                )
                error = PinError(
                    "pin_locked", "Too many wrong PINs. Try again later.", locked_until=record.locked_until
                )
            else:
                error = PinError(
                    "pin_invalid", "Wrong PIN.", attempts_left=TransactionPin.MAX_ATTEMPTS - record.failed_attempts
                )
            record.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
    raise error  # outside the atomic block, so the failed attempt is kept


# ---------- One-time codes (emailed or texted) ----------
def _code_digest(user, purpose: str, target: str, code: str) -> str:
    # A code for a phone number only confirms that number.
    return _digest("code", user.pk, purpose, target, code) if target else _digest("code", user.pk, purpose, code)


def issue_code(user, purpose: str, target: str = "") -> str | None:
    """A new 6-digit code (older ones stop working), or None if one was sent less than a minute ago."""
    now = timezone.now()
    if OneTimeCode.objects.filter(user=user, purpose=purpose, created_at__gt=now - CODE_RESEND_INTERVAL).exists():
        return None
    OneTimeCode.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(used_at=now)
    code = f"{secrets.randbelow(1_000_000):06d}"
    OneTimeCode.objects.create(
        user=user,
        purpose=purpose,
        target=target,
        code_hash=_code_digest(user, purpose, target, code),
        expires_at=now + CODE_TTL,
    )
    return code


def redeem_code(user, purpose: str, code: str) -> OneTimeCode | None:
    """
    The user's current unexpired code for purpose if `code` matches it (it is then used up), else None.
    Wrong tries count towards OneTimeCode.MAX_ATTEMPTS.
    """
    now = timezone.now()
    with transaction.atomic():
        record = (
            OneTimeCode.objects.select_for_update()
            .filter(user=user, purpose=purpose, used_at__isnull=True, expires_at__gt=now)
            .order_by("-created_at")
            .first()
        )
        if record is None or record.attempts >= OneTimeCode.MAX_ATTEMPTS:
            return None
        if hmac.compare_digest(record.code_hash, _code_digest(user, purpose, record.target, str(code or "").strip())):
            record.used_at = now
            record.save(update_fields=["used_at"])
            return record
        record.attempts += 1
        record.save(update_fields=["attempts"])
        return None


def send_code(user, purpose: str, code: str) -> bool:
    minutes = int(CODE_TTL.total_seconds() // 60)
    if purpose == OneTimeCode.PURPOSE_VERIFY_EMAIL:
        subject, action = "Verify your QuickSurf email", "verify your email"
    else:
        subject, action = "Reset your QuickSurf password", "reset your password"
    body = (
        f"Your code to {action} is {code}.\n\nIt expires in {minutes} minutes. "
        "If you didn't ask for it, you can ignore this email.\n\nQuickSurf"
    )
    return send_email(user.email, subject, body, log_body=body.replace(code, "******"))


def send_phone_code(phone: str, code: str) -> bool:
    from notifications.sms import send_sms

    minutes = int(CODE_TTL.total_seconds() // 60)
    return send_sms(
        phone, f"Your QuickSurf code is {code}. It expires in {minutes} minutes. Never share it with anyone."
    )


class PhoneTaken(Exception):
    """The number is already verified on another account."""


def verify_phone(user, phone: str) -> None:
    """Mark `phone` as the user's verified number. Raises PhoneTaken if another account has it."""
    from django.db import IntegrityError

    from users.models import User

    if User.objects.filter(phone=phone, phone_verified_at__isnull=False).exclude(pk=user.pk).exists():
        raise PhoneTaken
    user.phone, user.phone_verified_at = phone, timezone.now()
    try:
        with transaction.atomic():
            user.save(update_fields=["phone", "phone_verified_at"])
    except IntegrityError as e:  # verified on another account at the same moment
        raise PhoneTaken from e


# ---------- Sessions ----------
def end_all_sessions(user) -> None:
    """Revoke every refresh token the user holds. Access tokens expire on their own (JWT_ACCESS_MINUTES)."""
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


# ---------- Closing an account ----------
class AccountNotEmpty(Exception):
    pass


def close_account(user) -> None:
    """
    Close an account at the user's request: personal details are removed and sign-in disabled. Purchases,
    payments and the wallet ledger stay, anonymised, as financial records. Refused while the wallet holds
    money or a purchase is pending, because there is no way to pay it out.
    """
    from services.models import AirtimeTransaction, Beneficiary, DataTransaction
    from wallets.models import Wallet

    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(user=user)
        if wallet.balance or wallet.locked:
            raise AccountNotEmpty(f"Your wallet still holds ₦{wallet.balance + wallet.locked}. Spend it first.")
        for model in (AirtimeTransaction, DataTransaction):
            if model.objects.filter(user=user, status__in=["initiated", "pending"]).exists():
                raise AccountNotEmpty("A purchase is still pending. Try again once it has finished.")

        user.email = f"closed-{user.pk}@closed.invalid"
        user.first_name = user.last_name = user.phone = ""
        user.email_verified_at = user.phone_verified_at = None
        user.is_active = False
        user.set_unusable_password()
        user.save()
        TransactionPin.objects.filter(user=user).delete()
        OneTimeCode.objects.filter(user=user).delete()
        Beneficiary.objects.filter(user=user).delete()
    end_all_sessions(user)
