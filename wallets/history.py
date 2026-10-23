# wallets/history.py
"""
The customer's view of their wallet ledger: one line per top-up, purchase, refund, reward or
correction, with the purchase details looked up. Captures are left out: the purchase line already
showed the money leaving.
"""

from services.models import AirtimeTransaction, DataTransaction

from .models import Transaction

KINDS = {
    Transaction.TYPE_TOPUP: "topup",
    Transaction.TYPE_HOLD: "purchase",
    Transaction.TYPE_RELEASE: "refund",
    Transaction.TYPE_REVERSAL: "refund",
    Transaction.TYPE_REWARD: "reward",
    Transaction.TYPE_ADJUSTMENT: "adjustment",
    Transaction.TYPE_OPENING: "opening",
}
TYPES_BY_KIND = {}
for _type, _kind in KINDS.items():
    TYPES_BY_KIND.setdefault(_kind, []).append(_type)


def history(wallet, kind=None, date_from=None, date_to=None):
    """Ledger rows to show, newest first. kind: topup, purchase, refund, reward, adjustment, opening."""
    rows = wallet.transactions.filter(type__in=TYPES_BY_KIND.get(kind, list(KINDS)))
    if date_from:
        rows = rows.filter(timestamp__date__gte=date_from)
    if date_to:
        rows = rows.filter(timestamp__date__lte=date_to)
    return rows.order_by("-timestamp", "-id")


def describe(rows) -> list[dict]:
    rows = list(rows)
    purchases = _purchases_for(rows)
    return [_line(row, purchases.get(row.reference)) for row in rows]


def _purchases_for(rows) -> dict:
    refs = {"airtime": set(), "data": set()}
    for row in rows:
        kind, _, client_reference = row.reference.partition(":")
        if kind in refs and client_reference:
            refs[kind].add(client_reference)
    found = {}
    for kind, model in (("airtime", AirtimeTransaction), ("data", DataTransaction)):
        for txn in model.objects.filter(client_reference__in=refs[kind]):
            found[f"{kind}:{txn.client_reference}"] = txn
    return found


def _line(row, purchase) -> dict:
    line = {
        "id": row.id,
        "date": row.timestamp,
        "kind": KINDS[row.type],
        "description": _description(row, purchase),
        "amount": str(row.balance_delta),
        "balance_after": str(row.balance_after) if row.balance_after is not None else None,
        "reference": purchase.client_reference if purchase else row.reference,
    }
    if purchase is not None:
        line.update(
            status=purchase.status,
            network=purchase.network,
            phone=purchase.phone,
            plan=getattr(purchase, "plan", None),
        )
    return line


def _description(row, purchase) -> str:
    if row.type == Transaction.TYPE_TOPUP:
        return "Wallet top-up"
    if row.type == Transaction.TYPE_REWARD:
        return "Rewards redeemed"
    if row.type == Transaction.TYPE_ADJUSTMENT:
        return "Balance correction"
    if row.type == Transaction.TYPE_OPENING:
        return "Opening balance"
    if purchase is None:
        return "Purchase"
    item = purchase.item if isinstance(purchase, DataTransaction) else "Airtime"
    what = f"{item} for {purchase.phone} ({purchase.network.upper()})"
    if row.type == Transaction.TYPE_RELEASE:
        return f"Refund: {what}"
    if row.type == Transaction.TYPE_REVERSAL:
        return f"Refund (reversed by the network): {what}"
    return what
