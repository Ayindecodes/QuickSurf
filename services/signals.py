# services/signals.py
import os
from django.db.models.signals import post_save
from django.dispatch import receiver

from rewards.models import LoyaltyLedger
from notifications.utils import send_receipt_email
from .models import AirtimeTransaction, DataTransaction

# Keep the old rate (your decision): ₦100 -> 1 point
POINTS_PER_NAIRA = 0.01

def _award_points(user, amount, reason, txn_type, txn_id):
    try:
        pts = int(float(amount) * POINTS_PER_NAIRA)
    except Exception:
        pts = 0
    if pts > 0:
        LoyaltyLedger.objects.create(
            user=user, points=pts, reason=reason,
            txn_type=txn_type, txn_id=str(txn_id)
        )

def _receipt_text(user, txn, label):
    return (
        f"Hi {getattr(user, 'first_name', '') or user.email},\n\n"
        f"Payment received.\n\n"
        f"Service: {label}\n"
        f"Network: {txn.network}\n"
        f"Phone: {txn.phone}\n"
        f"Amount: ₦{txn.amount}\n"
        f"Status: {txn.status}\n"
        f"Time: {txn.timestamp}\n\n"
        f"Thanks for using Quicksurf."
    )

@receiver(post_save, sender=AirtimeTransaction)
def on_airtime_success(sender, instance: AirtimeTransaction, created, **kwargs):
    if instance.status == "successful":
        _award_points(instance.user, instance.amount, "Airtime purchase", "airtime", instance.id)
        send_receipt_email(
            to_email=instance.user.email,
            subject="Receipt - Airtime Purchase",
            body=_receipt_text(instance.user, instance, "Airtime"),
        )

@receiver(post_save, sender=DataTransaction)
def on_data_success(sender, instance: DataTransaction, created, **kwargs):
    if instance.status == "successful":
        _award_points(instance.user, instance.amount, "Data purchase", "data", instance.id)
        send_receipt_email(
            to_email=instance.user.email,
            subject="Receipt - Data Purchase",
            body=_receipt_text(instance.user, instance, "Data"),
        )
