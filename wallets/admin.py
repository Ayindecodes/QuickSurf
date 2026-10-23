# wallets/admin.py
from django.contrib import admin

from .models import Transaction, Wallet


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    """Balances are read-only here: change them through a staff adjustment (POST /api/wallet/adjust/)."""
    list_display = ("user", "balance", "locked", "updated_at")
    search_fields = ("user__email",)
    readonly_fields = ("user", "balance", "locked", "created_at", "updated_at")
    ordering = ("-updated_at",)

    def has_add_permission(self, request):
        return False  # every user gets a wallet automatically

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """The ledger is append-only: viewable here, written only by wallets.services."""
    list_display = ("timestamp", "wallet", "type", "amount", "balance_after", "locked_after", "reference", "created_by")
    list_filter = ("type", "timestamp")
    search_fields = ("reference", "wallet__user__email", "note")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp", "-id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
