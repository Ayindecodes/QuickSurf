# services/settlement.py
"""
Settles purchases against the wallet. A purchase holds its amount when it starts (see the
purchase views) and is then settled exactly once:
  successful -> the held money is captured (spent)
  failed     -> the held money is released back to the balance
  reversed   -> refunded: released if still held, or given back if already captured
  pending    -> the money stays held
Successful, failed and reversed are final. The one exception is successful -> reversed, because
VTpass can reverse a purchase after reporting it delivered.
"""
from django.db import transaction

from wallets import services as wallet_services
from .models import AirtimeTransaction

FINAL_STATUSES = {"successful", "failed", "reversed"}


def ledger_reference(kind: str, client_reference: str) -> str:
    """Wallet ledger reference for a purchase. Airtime and data references can overlap, so prefix them."""
    return f"{kind}:{client_reference}"


def purchase_kind(txn) -> str:
    return "airtime" if isinstance(txn, AirtimeTransaction) else "data"


def settle_purchase(txn, outcome: str, **fields):
    """
    Record a provider outcome on a purchase and settle its wallet hold once. `fields`
    (provider_status, raw_response, ...) are saved with it. A final purchase is returned
    unchanged, unless a successful one is now reversed.
    """
    with transaction.atomic():
        txn = type(txn).objects.select_for_update().get(pk=txn.pk)
        reversing_delivered = txn.status == "successful" and outcome == "reversed"
        if txn.status in FINAL_STATUSES and not reversing_delivered:
            return txn

        for name, value in fields.items():
            setattr(txn, name, value)
        if outcome in FINAL_STATUSES:
            reference = ledger_reference(purchase_kind(txn), txn.client_reference)
            if outcome == "successful":
                wallet_services.capture(txn.user, reference)
            elif reversing_delivered:
                wallet_services.refund_capture(txn.user, reference)
            else:
                wallet_services.release(txn.user, reference)
            txn.status = outcome
        txn.save()
    return txn
