# users/management/commands/setup_staff_roles.py
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

VIEWABLE = [
    ("users", "user"), ("wallets", "wallet"), ("wallets", "transaction"), ("payments", "paymentintent"),
    ("services", "airtimetransaction"), ("services", "datatransaction"), ("services", "providerlog"),
    ("notifications", "emaillog"), ("rewards", "loyaltyledger"),
]
SUPPORT = [f"{app}.view_{model}" for app, model in VIEWABLE]
ROLES = {
    "Support": SUPPORT,                                # look things up, re-query VTpass / Paystack
    "Finance": [*SUPPORT, "wallets.adjust_wallet"],    # ... and correct wallet balances
}


class Command(BaseCommand):
    help = (
        "Create or update the staff roles: Support (read-only users, wallets, purchases, payments, logs) and "
        "Finance (Support plus wallet adjustments). Put staff in these groups instead of making them superusers."
    )

    def handle(self, *args, **opts):
        for name, permissions in ROLES.items():
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set([_permission(dotted) for dotted in permissions])
            self.stdout.write(f"{name}: {len(permissions)} permissions")


def _permission(dotted: str) -> Permission:
    app_label, codename = dotted.split(".")
    return Permission.objects.get(content_type__app_label=app_label, codename=codename)
