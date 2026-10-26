# wallets/services.py
"""
The only code allowed to change wallet balances (see docs/adr/0001-wallet-ledger.md).

Each function locks the wallet row, applies one change and writes one ledger row, all in a
single database transaction:
  credit_topup  Paystack payment credited   balance +
  hold          purchase started            balance -, locked +
  capture       purchase delivered          locked -
  release       purchase failed (refund)    locked -, balance +
  refund_capture  delivered purchase reversed by VTpass   balance +
  adjust        staff correction            balance +/-
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from .models import TWO_PLACES, Transaction, Wallet

ZERO = Decimal("0.00")


class InsufficientFunds(Exception):
    """The change would take the balance (or locked funds) below zero."""


def _money(amount) -> Decimal:
    value = Decimal(str(amount)).quantize(TWO_PLACES)
    if not value.is_finite() or value <= 0:
        raise ValueError(f"Amount must be greater than zero, got {amount!r}.")
    return value


def _locked_wallet(user) -> Wallet:
    return Wallet.objects.select_for_update().get(user=user)


def _record(wallet, entry_type, amount, *, balance_delta=ZERO, locked_delta=ZERO,
            reference="", note="", created_by=None) -> Transaction:
    new_balance = wallet.balance + balance_delta
    new_locked = wallet.locked + locked_delta
    if new_balance < 0 or new_locked < 0:
        raise InsufficientFunds(f"wallet {wallet.pk}: balance {wallet.balance}, locked {wallet.locked}")

    wallet.balance, wallet.locked = new_balance, new_locked
    wallet.save(update_fields=["balance", "locked", "updated_at"])
    return Transaction.objects.create(
        wallet=wallet,
        type=entry_type,
        amount=amount,
        reference=reference,
        balance_delta=balance_delta,
        locked_delta=locked_delta,
        balance_after=new_balance,
        locked_after=new_locked,
        note=note,
        created_by=created_by,
    )


def _open_hold(wallet, reference: str) -> Transaction | None:
    """The hold for this purchase, unless it was already captured or released."""
    entries = wallet.transactions.filter(reference=reference)
    if entries.filter(type__in=[Transaction.TYPE_CAPTURE, Transaction.TYPE_RELEASE]).exists():
        return None
    return entries.filter(type=Transaction.TYPE_HOLD).first()


@transaction.atomic
def credit_topup(user, amount, reference: str) -> bool:
    """Credit a Paystack payment. Returns False, changing nothing, if this reference was already credited."""
    wallet = _locked_wallet(user)
    if Transaction.objects.filter(type=Transaction.TYPE_TOPUP, reference=reference).exists():
        return False
    amount = _money(amount)
    _record(wallet, Transaction.TYPE_TOPUP, amount, balance_delta=amount, reference=reference)
    return True


@transaction.atomic
def hold(user, amount, reference: str) -> Transaction:
    """Set money aside for a purchase. Raises InsufficientFunds if the balance is too low."""
    wallet = _locked_wallet(user)
    amount = _money(amount)
    return _record(wallet, Transaction.TYPE_HOLD, amount,
                   balance_delta=-amount, locked_delta=amount, reference=reference)


@transaction.atomic
def capture(user, reference: str) -> bool:
    """Spend the money held for a delivered purchase. Returns False if there's no open hold for it."""
    wallet = _locked_wallet(user)
    held = _open_hold(wallet, reference)
    if held is None:
        return False
    _record(wallet, Transaction.TYPE_CAPTURE, held.amount, locked_delta=-held.amount, reference=reference)
    return True


@transaction.atomic
def release(user, reference: str) -> bool:
    """Return the money held for a failed purchase. Returns False if there's no open hold for it."""
    wallet = _locked_wallet(user)
    held = _open_hold(wallet, reference)
    if held is None:
        return False
    _record(wallet, Transaction.TYPE_RELEASE, held.amount,
            balance_delta=held.amount, locked_delta=-held.amount, reference=reference)
    return True


@transaction.atomic
def refund_capture(user, reference: str) -> bool:
    """
    Give back money already spent on a purchase that VTpass later reversed. Returns False if the
    purchase was never captured or was already refunded.
    """
    wallet = _locked_wallet(user)
    entries = wallet.transactions.filter(reference=reference)
    captured = entries.filter(type=Transaction.TYPE_CAPTURE).first()
    if captured is None or entries.filter(type=Transaction.TYPE_REVERSAL).exists():
        return False
    _record(wallet, Transaction.TYPE_REVERSAL, captured.amount, balance_delta=captured.amount, reference=reference)
    return True


def stale_locked(wallet) -> Decimal:
    """Locked money that no open purchase hold accounts for."""
    closed = set(wallet.transactions.filter(type__in=[Transaction.TYPE_CAPTURE, Transaction.TYPE_RELEASE])
                 .values_list("reference", flat=True))
    held = sum((h.amount for h in wallet.transactions.filter(type=Transaction.TYPE_HOLD) if h.reference not in closed),
               ZERO)
    return wallet.locked - held


@transaction.atomic
def clear_stale_locked(user, note: str, created_by=None) -> Transaction | None:
    """
    Remove locked money that no open purchase hold accounts for. The old /wallet/lock endpoint and
    the old requery refund both added to `locked` without taking from `balance`, so this money was
    never real: it is removed, not refunded. Returns None if there is nothing to clear.
    """
    note = (note or "").strip()
    if not note:
        raise ValueError("Clearing locked money needs a note explaining why.")
    wallet = _locked_wallet(user)
    stale = stale_locked(wallet)
    if stale <= 0:
        return None
    return _record(wallet, Transaction.TYPE_ADJUSTMENT, stale, locked_delta=-stale, note=note, created_by=created_by)


@transaction.atomic
def adjust(user, amount, note: str, created_by=None, reference: str = "") -> Transaction:
    """
    Staff correction: a positive amount adds to the balance, a negative one takes from it.
    A note explaining why is required. Raises InsufficientFunds if the balance would go below zero.
    """
    note = (note or "").strip()
    if not note:
        raise ValueError("An adjustment needs a note explaining why.")
    delta = Decimal(str(amount)).quantize(TWO_PLACES)
    if not delta.is_finite() or delta == 0:
        raise ValueError("Adjustment amount can't be zero.")

    wallet = _locked_wallet(user)
    return _record(wallet, Transaction.TYPE_ADJUSTMENT, abs(delta), balance_delta=delta,
                   reference=reference, note=note, created_by=created_by)
