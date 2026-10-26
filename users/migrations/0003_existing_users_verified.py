from django.db import migrations
from django.db.models import F


def treat_existing_users_as_verified(apps, schema_editor):
    """Accounts created before email verification existed aren't blocked from topping up."""
    User = apps.get_model("users", "User")
    User.objects.filter(email_verified_at__isnull=True).update(email_verified_at=F("date_joined"))


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_verification_pin_codes"),
    ]

    operations = [
        migrations.RunPython(treat_existing_users_as_verified, migrations.RunPython.noop),
    ]
