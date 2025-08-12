from django.contrib import admin
from .models import Wallet, Transaction  # adjust if your app labels differ


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "balance",
        "locked_amount",
        "updated",
        "created",
    )
    search_fields = ("user__email", "user__username", "user__id")
    list_filter = ("created", "updated")
    date_hierarchy = "created"
    ordering = ("-updated",)
    readonly_fields = ("user", "created", "updated")  # keep server-owned fields read-only


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "ref",          # unique reference
        "user",
        "type",         # e.g. 'fund' | 'airtime' | 'data' | 'refund'
        "amount",
        "status",       # e.g. 'pending' | 'success' | 'failed' | 'refunded'
        "created",
    )
    search_fields = ("ref", "user__email", "user__username")
    list_filter = ("type", "status", "created")
    date_hierarchy = "created"
    ordering = ("-created",)
    readonly_fields = (
        "ref",
        "user",
        "type",
        "amount",
        "status",
        "provider_request_id",   # include only if present on your model
        "provider_reference",    # include only if present
        "created",
        "updated",
    )
    actions = ("mark_refunded",)

    def mark_refunded(self, request, queryset):
        updated = queryset.update(status="refunded")
        self.message_user(request, f"{updated} transaction(s) marked refunded.")
    mark_refunded.short_description = "Mark selected as refunded"
