# services/signals
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from notifications.utils import send_receipt_email
from rewards.models import LoyaltyLedger

from .models import AirtimeTransaction, DataTransaction

# ---------------------------------------------------------------------------
# Config (overridable in settings.py)
# ---------------------------------------------------------------------------
POINTS_PER_NAIRA = Decimal(getattr(settings, "POINTS_PER_NAIRA", "0.01"))  # ₦100 => 1 pt by default
REWARDS_ENABLED = bool(getattr(settings, "REWARDS_ENABLED", True))
RECEIPT_EMAILS_ENABLED = bool(getattr(settings, "RECEIPT_EMAILS_ENABLED", True))

SUCCESS_VALUES = {"successful", "success"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _calc_points(amount: Decimal | int | float | str) -> int:
    """Convert Naira amount to integer points (floor)."""
    try:
        naira = Decimal(str(amount))
    except Exception:
        return 0
    pts = (naira * POINTS_PER_NAIRA).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return int(pts)


def _mask_msisdn(msisdn: Optional[str]) -> str:
    s = str(msisdn or "")
    return f"{s[:3]}***{s[-4:]}" if len(s) >= 7 else "***"


def _receipt_text(user, txn, label: str) -> str:
    first = (getattr(user, "first_name", None) or "").strip() or getattr(user, "email", "Customer")
    lines = [
        f"Hi {first},",
        "",
        "Your purchase was successful.",
        "",
        f"Service: {label}",
        f"Network: {getattr(txn, 'network', '')}",
        f"Phone: {_mask_msisdn(getattr(txn, 'phone', ''))}",
        f"Amount: ₦{txn.amount}",
        f"Status: {txn.status}",
        f"Time: {txn.timestamp}",
        "",
        "Thanks for using Quicksurf.",
    ]
    return "\n".join(lines)


def _award_points_once(user, amount, reason: str, txn_type: str, txn_id: str | int) -> None:
    """Create a loyalty entry once per txn (idempotent)."""
    if not REWARDS_ENABLED:
        return
    pts = _calc_points(amount)
    if pts <= 0:
        return
    try:
        # Idempotency: avoid duplicates if signal fires again
        LoyaltyLedger.objects.get_or_create(
            user=user,
            txn_type=txn_type,
            txn_id=str(txn_id),
            defaults={"points": pts, "reason": reason},
        )
    except Exception:
        # Never break purchase flow due to rewards storage issues
        pass


def _send_receipt_safe(user, txn, label: str, subject: str) -> None:
    if not RECEIPT_EMAILS_ENABLED:
        return
    try:
        send_receipt_email(
            to_email=getattr(user, "email", None),
            subject=subject,
            body=_receipt_text(user, txn, label),
        )
    except Exception:
        # Do not raise: email is best-effort only
        pass


# ---------------------------------------------------------------------------
# Capture previous status so we only act on transitions
# ---------------------------------------------------------------------------


def _attach_old_status(instance) -> None:
    if not getattr(instance, "pk", None):
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


# ---------------------------------------------------------------------------
# Post-save: act on status transitions (AFTER COMMIT)
# ---------------------------------------------------------------------------


def _take_back_points(user, txn_type: str, txn_id) -> None:
    """A reversed purchase loses the points it earned (once)."""
    earned = LoyaltyLedger.objects.filter(user=user, txn_type=txn_type, txn_id=str(txn_id), points__gt=0).first()
    if earned is None:
        return
    LoyaltyLedger.objects.get_or_create(
        user=user,
        txn_type="reversal",
        txn_id=f"{txn_type}:{txn_id}",
        defaults={"points": -earned.points, "reason": "Purchase reversed"},
    )


def _on_status_change(instance, txn_type: str, label: str) -> None:
    new, old = str(instance.status).lower(), str(getattr(instance, "_old_status", "")).lower()
    if new in SUCCESS_VALUES and old not in SUCCESS_VALUES:

        def _after_commit():
            _award_points_once(instance.user, instance.amount, f"{label} purchase", txn_type, instance.id)
            _send_receipt_safe(instance.user, instance, label, f"Receipt - {label} Purchase")

        transaction.on_commit(_after_commit)
    elif new == "reversed" and old != "reversed":
        transaction.on_commit(lambda: _take_back_points(instance.user, txn_type, instance.id))


@receiver(post_save, sender=AirtimeTransaction)
def on_airtime_saved(sender, instance: AirtimeTransaction, created, **kwargs):
    _on_status_change(instance, "airtime", "Airtime")


@receiver(post_save, sender=DataTransaction)
def on_data_saved(sender, instance: DataTransaction, created, **kwargs):
    _on_status_change(instance, "data", "Data")
