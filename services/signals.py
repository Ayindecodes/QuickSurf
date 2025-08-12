# services/signals.py
from decimal import Decimal, ROUND_DOWN
from django.conf import settings
from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from rewards.models import LoyaltyLedger
from notifications.utils import send_receipt_email
from .models import AirtimeTransaction, DataTransaction


# Configurable via settings.py (fallbacks keep current behavior)
POINTS_PER_NAIRA = Decimal(getattr(settings, "POINTS_PER_NAIRA", "0.01"))  # ₦100 -> 1 point
REWARDS_ENABLED = getattr(settings, "REWARDS_ENABLED", True)
RECEIPT_EMAILS_ENABLED = getattr(settings, "RECEIPT_EMAILS_ENABLED", True)

SUCCESS_VALUES = {"successful", "success"}  # be lenient with status spelling


# --- Helpers -----------------------------------------------------------------

def _calc_points(amount) -> int:
    """
    Convert Naira amount to integer points, floor (no rounding up).
    """
    try:
        naira = Decimal(str(amount))
    except Exception:
        return 0
    pts = (naira * POINTS_PER_NAIRA).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return int(pts)


def _mask_msisdn(msisdn: str) -> str:
    s = str(msisdn or "")
    return f"{s[:3]}***{s[-4:]}" if len(s) >= 7 else "***"


def _receipt_text(user, txn, label):
    first = getattr(user, "first_name", "") or user.email
    lines = [
        f"Hi {first},",
        "",
        "Payment received.",
        "",
        f"Service: {label}",
        f"Network: {txn.network}",
        f"Phone: {_mask_msisdn(txn.phone)}",
        f"Amount: ₦{txn.amount}",
        f"Status: {txn.status}",
        f"Time: {txn.timestamp}",
        "",
        "Thanks for using Quicksurf.",
    ]
    return "\n".join(lines)


def _award_points(user, amount, reason, txn_type, txn_id):
    if not REWARDS_ENABLED:
        return
    pts = _calc_points(amount)
    if pts > 0:
        LoyaltyLedger.objects.create(
            user=user,
            points=pts,
            reason=reason,
            txn_type=txn_type,
            txn_id=str(txn_id),
        )


def _send_receipt(user, txn, label, subject):
    if not RECEIPT_EMAILS_ENABLED:
        return
    try:
        send_receipt_email(
            to_email=user.email,
            subject=subject,
            body=_receipt_text(user, txn, label),
        )
    except Exception:
        # Swallow email failures; never block DB writes
        pass


# --- Track previous status so we only act on transitions ---------------------

def _attach_old_status(instance):
    """
    Attach the previous status to instance._old_status for transition checks.
    """
    if not instance.pk:
        instance._old_status = None
        return
    try:
        old = instance.__class__.objects.only("status").get(pk=instance.pk)
        instance._old_status = old.status
    except instance.__class__.DoesNotExist:
        instance._old_status = None


@receiver(pre_save, sender=AirtimeTransaction)
def _airtime_presave(sender, instance: AirtimeTransaction, **kwargs):
    _attach_old_status(instance)


@receiver(pre_save, sender=DataTransaction)
def _data_presave(sender, instance: DataTransaction, **kwargs):
    _attach_old_status(instance)


# --- Post-save: fire on transition to success --------------------------------

@receiver(post_save, sender=AirtimeTransaction)
def on_airtime_success(sender, instance: AirtimeTransaction, created, **kwargs):
    new_ok = str(instance.status).lower() in SUCCESS_VALUES
    old_ok = str(getattr(instance, "_old_status", "")).lower() in SUCCESS_VALUES
    if new_ok and not old_ok:
        _award_points(instance.user, instance.amount, "Airtime purchase", "airtime", instance.id)
        _send_receipt(instance.user, instance, "Airtime", "Receipt - Airtime Purchase")


@receiver(post_save, sender=DataTransaction)
def on_data_success(sender, instance: DataTransaction, created, **kwargs):
    new_ok = str(instance.status).lower() in SUCCESS_VALUES
    old_ok = str(getattr(instance, "_old_status", "")).lower() in SUCCESS_VALUES
    if new_ok and not old_ok:
        _award_points(instance.user, instance.amount, "Data purchase", "data", instance.id)
        _send_receipt(instance.user, instance, "Data", "Receipt - Data Purchase")
