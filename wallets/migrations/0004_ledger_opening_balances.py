from decimal import Decimal

from django.db import migrations

ZERO = Decimal("0.00")
OPENING_NOTE = "Balance carried over when the ledger started"
HOLD_NOTE = "Pending purchase carried over when the ledger started"


def open_ledger(apps, schema_editor):
    """
    Start the ledger from current balances: one opening row per wallet, plus a hold row for each
    pending purchase so it can still be captured or released. Afterwards every wallet equals the
    sum of its ledger deltas, and no balance changes.
    """
    Wallet = apps.get_model("wallets", "Wallet")
    Transaction = apps.get_model("wallets", "Transaction")
    AirtimeTransaction = apps.get_model("services", "AirtimeTransaction")
    DataTransaction = apps.get_model("services", "DataTransaction")

    pending = {}
    for model, kind in ((AirtimeTransaction, "airtime"), (DataTransaction, "data")):
        for txn in model.objects.filter(status="pending").order_by("timestamp", "id"):
            pending.setdefault(txn.user_id, []).append((f"{kind}:{txn.client_reference}", txn.amount))

    for wallet in Wallet.objects.order_by("id"):
        holds = pending.get(wallet.user_id, [])
        held = sum((amount for _, amount in holds), ZERO)
        balance = wallet.balance + held
        locked = wallet.locked - held

        if balance or locked:
            Transaction.objects.create(
                wallet=wallet, type="opening", amount=wallet.balance + wallet.locked,
                reference=f"opening:{wallet.id}", balance_delta=balance, locked_delta=locked,
                balance_after=balance, locked_after=locked, note=OPENING_NOTE,
            )
        for reference, amount in holds:
            balance -= amount
            locked += amount
            Transaction.objects.create(
                wallet=wallet, type="hold", amount=amount, reference=reference,
                balance_delta=-amount, locked_delta=amount,
                balance_after=balance, locked_after=locked, note=HOLD_NOTE,
            )


def close_ledger(apps, schema_editor):
    Transaction = apps.get_model("wallets", "Transaction")
    Transaction.objects.filter(type="opening", note=OPENING_NOTE).delete()
    Transaction.objects.filter(type="hold", note=HOLD_NOTE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("wallets", "0003_ledger"),
        ("services", "0007_airtimetransaction_provider_reference_and_more"),
    ]

    operations = [
        migrations.RunPython(open_ledger, close_ledger),
    ]
