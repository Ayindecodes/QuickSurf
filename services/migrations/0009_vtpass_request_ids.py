from django.db import migrations


def use_sent_request_ids(apps, schema_editor):
    """
    Until now each purchase's client_reference was sent to VTpass as its request ID, while
    provider_request_id held whatever VTpass echoed back. Store the request ID that was actually
    sent, so requery and VTpass callbacks can find the purchase, and keep VTpass's transactionId
    (from the stored response) in provider_reference.
    """
    for name in ("AirtimeTransaction", "DataTransaction"):
        model = apps.get_model("services", name)
        for txn in model.objects.only("id", "client_reference", "provider_reference", "raw_response"):
            raw = txn.raw_response if isinstance(txn.raw_response, dict) else {}
            content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
            tx = content.get("transactions") if isinstance(content.get("transactions"), dict) else {}
            tx_id = tx.get("transactionId") or raw.get("transactionId")

            txn.provider_request_id = txn.client_reference
            txn.provider_reference = txn.provider_reference or (str(tx_id) if tx_id else None)
            txn.save(update_fields=["provider_request_id", "provider_reference"])


class Migration(migrations.Migration):

    dependencies = [
        ("services", "0008_reversed_status"),
    ]

    operations = [
        migrations.RunPython(use_sent_request_ids, migrations.RunPython.noop),
    ]
