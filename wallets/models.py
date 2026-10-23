# wallets/models.py
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver

TWO_PLACES = Decimal("0.01")


def q(amount: Decimal | int | float | str) -> Decimal:
    """
    Quantize any numeric input to 2dp Decimal (naira).
    """
    if isinstance(amount, Decimal):
        d = amount
    else:
        d = Decimal(str(amount))
    # Use ROUND_DOWN to avoid accidental over-spend from rounding up.
    return d.quantize(TWO_PLACES, rounding=ROUND_DOWN)


class Wallet(models.Model):
    """
    A user's naira wallet (see docs/adr/0001-wallet-ledger.md).
      balance: money the user can spend now.
      locked:  money held for purchases still waiting on VTpass.
    Only wallets.services may change these, and every change writes a Transaction row.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    locked = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user"]),
        ]

    def __str__(self) -> str:
        return f"Wallet<{self.user_id}> bal={self.balance} locked={self.locked}"


class Transaction(models.Model):
    """
    Wallet ledger: one row per money movement, never edited or deleted.
    balance_delta / locked_delta are the signed changes and balance_after / locked_after the
    wallet's values right after, so a wallet always equals the sum of its deltas.
    'amount' is always positive.
    """

    TYPE_OPENING = "opening"  # balance carried over when the ledger started
    TYPE_TOPUP = "topup"  # Paystack payment credited:            balance +
    TYPE_HOLD = "hold"  # purchase started, money set aside:    balance -, locked +
    TYPE_CAPTURE = "capture"  # purchase delivered, held money spent: locked -
    TYPE_RELEASE = "release"  # purchase failed, held money returned: locked -, balance +
    TYPE_REVERSAL = "reversal"  # delivered purchase later reversed by VTpass: balance +
    TYPE_ADJUSTMENT = "adjustment"  # staff correction in either direction, with a note

    # Written before the ledger existed; these carry no deltas.
    TYPE_CREDIT = "credit"
    TYPE_DEBIT = "debit"
    TYPE_LOCK = "lock"
    TYPE_UNLOCK = "unlock"
    TYPE_ADJUST = "adjust"

    TYPE_CHOICES = [
        (TYPE_OPENING, "Opening balance"),
        (TYPE_TOPUP, "Top-up"),
        (TYPE_HOLD, "Hold"),
        (TYPE_CAPTURE, "Capture"),
        (TYPE_RELEASE, "Release"),
        (TYPE_REVERSAL, "Reversal"),
        (TYPE_ADJUSTMENT, "Adjustment"),
        (TYPE_CREDIT, "Credit (legacy)"),
        (TYPE_DEBIT, "Debit (legacy)"),
        (TYPE_LOCK, "Lock (legacy)"),
        (TYPE_UNLOCK, "Unlock (legacy)"),
        (TYPE_ADJUST, "Adjust (legacy)"),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    timestamp = models.DateTimeField(auto_now_add=True)
    reference = models.CharField(max_length=100, blank=True, db_index=True)

    balance_delta = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    locked_delta = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    locked_after = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        indexes = [
            models.Index(fields=["wallet", "timestamp"]),
            models.Index(fields=["reference"]),
        ]
        ordering = ["-timestamp", "-id"]
        permissions = [("adjust_wallet", "Can adjust wallet balances")]
        constraints = [
            # A Paystack reference is credited once; a purchase is held, captured, released or reversed once.
            models.UniqueConstraint(
                fields=["type", "reference"],
                condition=Q(type__in=["opening", "topup", "hold", "capture", "release", "reversal"]) & ~Q(reference=""),
                name="wallets_ledger_unique_type_reference",
            ),
        ]

    def __str__(self) -> str:
        return f"Txn<{self.type} ₦{self.amount} w={self.wallet_id} ref={self.reference or '-'}>"


# --------------------------- convenience signals --------------------------- #


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_wallet(sender, instance, created, **kwargs):
    """
    Auto-create a wallet for every user (idempotent).
    """
    if not instance:
        return
    Wallet.objects.get_or_create(user=instance)
