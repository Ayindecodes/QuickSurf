# wallets/models.py
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Optional

from django.conf import settings
from django.db import models, transaction as db_transaction
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
    Simple naira wallet with optional locked funds.
    balance and locked are stored in naira (Decimal, 2dp).
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet"
    )
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

    # --------------------------- computed amounts --------------------------- #

    @property
    def available(self) -> Decimal:
        """
        Spendable balance = balance - locked (never negative).
        """
        a = q(self.balance) - q(self.locked)
        return a if a >= 0 else Decimal("0.00")

    def can_spend(self, amount: Decimal | int | float | str) -> bool:
        return self.available >= q(amount)

    # -------------------------- mutation operations ------------------------ #
    # All ops are concurrency-safe via SELECT ... FOR UPDATE inside a DB txn.

    @db_transaction.atomic
    def credit(self, amount: Decimal | int | float | str, reference: str = "") -> "Transaction":
        """
        Increase wallet balance by amount.
        """
        amt = q(amount)
        if amt <= 0:
            raise ValueError("Amount must be > 0")

        # Lock row
        w = Wallet.objects.select_for_update().get(pk=self.pk)
        w.balance = q(w.balance + amt)
        w.save(update_fields=["balance", "updated_at"])

        return Transaction.objects.create(
            wallet=w, amount=amt, type=Transaction.TYPE_CREDIT, reference=reference
        )

    @db_transaction.atomic
    def lock_funds(self, amount: Decimal | int | float | str, reference: str = "") -> "Transaction":
        """
        Reserve funds from available into locked. Does NOT reduce balance.
        """
        amt = q(amount)
        if amt <= 0:
            raise ValueError("Amount must be > 0")

        w = Wallet.objects.select_for_update().get(pk=self.pk)
        if (q(w.balance) - q(w.locked)) < amt:
            raise ValueError("INSUFFICIENT_AVAILABLE_FUNDS")

        w.locked = q(w.locked + amt)
        w.save(update_fields=["locked", "updated_at"])

        return Transaction.objects.create(
            wallet=w, amount=amt, type=Transaction.TYPE_LOCK, reference=reference
        )

    @db_transaction.atomic
    def unlock_funds(self, amount: Decimal | int | float | str, reference: str = "") -> "Transaction":
        """
        Release previously locked funds back to available. Does NOT change total balance.
        """
        amt = q(amount)
        if amt <= 0:
            raise ValueError("Amount must be > 0")

        w = Wallet.objects.select_for_update().get(pk=self.pk)
        if q(w.locked) < amt:
            raise ValueError("UNLOCK_EXCEEDS_LOCKED")

        w.locked = q(w.locked - amt)
        w.save(update_fields=["locked", "updated_at"])

        return Transaction.objects.create(
            wallet=w, amount=amt, type=Transaction.TYPE_UNLOCK, reference=reference
        )

    @db_transaction.atomic
    def debit(
        self,
        amount: Decimal | int | float | str,
        reference: str = "",
        use_locked_first: bool = True,
    ) -> "Transaction":
        """
        Charge the wallet by 'amount'.
        By default, reduces 'locked' first (up to amount) and always reduces balance by amount.

        Invariants after debit:
            - balance >= 0
            - locked >= 0
            - available = balance - locked >= 0
        """
        amt = q(amount)
        if amt <= 0:
            raise ValueError("Amount must be > 0")

        w = Wallet.objects.select_for_update().get(pk=self.pk)

        # Ensure total funds are enough
        if q(w.balance) < amt:
            raise ValueError("INSUFFICIENT_FUNDS")

        if use_locked_first and q(w.locked) > 0:
            from_locked = min(q(w.locked), amt)
            w.locked = q(w.locked - from_locked)

        # Deduct from total balance
        w.balance = q(w.balance - amt)

        # Safety: never let values drift negative
        if w.locked < 0:
            w.locked = Decimal("0.00")
        if w.balance < 0:
            # This shouldn't happen due to check above, but keep strict.
            raise ValueError("NEGATIVE_BALANCE_GUARD")

        w.save(update_fields=["balance", "locked", "updated_at"])

        return Transaction.objects.create(
            wallet=w, amount=amt, type=Transaction.TYPE_DEBIT, reference=reference
        )


class Transaction(models.Model):
    """
    Ledger entry for wallet movements. 'amount' is always positive;
    the semantic (credit/debit/lock/unlock) is captured in 'type'.
    """

    TYPE_CREDIT = "credit"
    TYPE_DEBIT = "debit"
    TYPE_LOCK = "lock"
    TYPE_UNLOCK = "unlock"
    TYPE_ADJUST = "adjust"

    TYPE_CHOICES = [
        (TYPE_CREDIT, "Credit"),
        (TYPE_DEBIT, "Debit"),
        (TYPE_LOCK, "Lock"),
        (TYPE_UNLOCK, "Unlock"),
        (TYPE_ADJUST, "Adjust"),
    ]

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    timestamp = models.DateTimeField(auto_now_add=True)
    reference = models.CharField(max_length=100, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["wallet", "timestamp"]),
            models.Index(fields=["reference"]),
        ]
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        return f"Txn<{self.type} ₦{self.amount} w={self.wallet_id} ref={self.reference or '-'}>"

    def clean(self) -> None:
        # Normalize to positive, 2dp
        self.amount = q(self.amount)
        if self.amount <= 0:
            raise ValueError("Transaction.amount must be > 0")


# --------------------------- convenience signals --------------------------- #

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def ensure_user_wallet(sender, instance, created, **kwargs):
    """
    Auto-create a wallet for every user (idempotent).
    """
    if not instance:
        return
    Wallet.objects.get_or_create(user=instance)

