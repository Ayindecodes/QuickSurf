from django.db import migrations
import uuid


def gen_ref():
    return f"MIG-{uuid.uuid4().hex[:12]}"


def fill_client_refs(apps, schema_editor):
    AirtimeTransaction = apps.get_model('services', 'AirtimeTransaction')
    DataTransaction = apps.get_model('services', 'DataTransaction')

    # Backfill Airtime
    qs = AirtimeTransaction.objects.filter(client_reference__isnull=True)
    for obj in qs.iterator():
        ref = gen_ref()
        # Ensure uniqueness (paranoid)
        while AirtimeTransaction.objects.filter(client_reference=ref).exists():
            ref = gen_ref()
        obj.client_reference = ref
        obj.save(update_fields=['client_reference'])

    # Backfill Data
    qs = DataTransaction.objects.filter(client_reference__isnull=True)
    for obj in qs.iterator():
        ref = gen_ref()
        while DataTransaction.objects.filter(client_reference=ref).exists():
            ref = gen_ref()
        obj.client_reference = ref
        obj.save(update_fields=['client_reference'])


def noop_reverse(apps, schema_editor):
    # We won't null them again on reverse
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('services', '0003_rename_created_at_providerlog_timestamp_and_more'),
    ]

    operations = [
        migrations.RunPython(fill_client_refs, noop_reverse),
    ]
