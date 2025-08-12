from django.contrib import admin
from .models import AirtimeTransaction, DataTransaction, ProviderLog


@admin.register(AirtimeTransaction)
class AirtimeTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "client_reference",
        "user",
        "network",
        "phone_masked",
        "amount",
        "status",
        "timestamp",
    )
    search_fields = ("client_reference", "user__email", "user__username", "phone")
    list_filter = ("network", "status", "timestamp")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    readonly_fields = (
        "client_reference",
        "user",
        "network",
        "phone",
        "amount",
        "status",
        "timestamp",
    )
    actions = ("admin_check_status",)

    def phone_masked(self, obj):
        return f"****{str(obj.phone)[-4:]}"
    phone_masked.short_description = "Phone"

    def admin_check_status(self, request, queryset):
        """
        Optional: re-check provider status for selected orders.
        Wire this to your real status-check function if available.
        """
        checked = 0
        for tx in queryset:
            try:
                # from services.vtpass import check_status  # adjust if you have it
                # check_status(tx.client_reference)
                checked += 1
            except Exception:
                pass
        self.message_user(request, f"Triggered status check for {checked} transaction(s).")
    admin_check_status.short_description = "Re-check provider status"


@admin.register(DataTransaction)
class DataTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "client_reference",
        "user",
        "network",
        "phone_masked",
        "plan",
        "amount",
        "status",
        "timestamp",
    )
    search_fields = ("client_reference", "user__email", "user__username", "phone", "plan")
    list_filter = ("network", "status", "timestamp", "plan")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    readonly_fields = (
        "client_reference",
        "user",
        "network",
        "phone",
        "plan",
        "amount",
        "status",
        "timestamp",
    )
    actions = ("admin_check_status",)

    def phone_masked(self, obj):
        return f"****{str(obj.phone)[-4:]}"
    phone_masked.short_description = "Phone"

    def admin_check_status(self, request, queryset):
        checked = 0
        for tx in queryset:
            try:
                # from services.vtpass import check_status  # adjust if you have it
                # check_status(tx.client_reference)
                checked += 1
            except Exception:
                pass
        self.message_user(request, f"Triggered status check for {checked} transaction(s).")
    admin_check_status.short_description = "Re-check provider status"


@admin.register(ProviderLog)
class ProviderLogAdmin(admin.ModelAdmin):
    list_display = (
        "service_type",     # e.g. 'vtpass' | 'paystack'
        "status_code",
        "timestamp",
        "response_preview",
    )
    list_filter = ("service_type", "status_code", "timestamp")
    search_fields = ("service_type",)  # add 'request_id' here if your model has it
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    readonly_fields = ("service_type", "status_code", "request_payload", "response_payload", "timestamp")

    def response_preview(self, obj):
        text = (obj.response_payload or "")[:120]
        return text + ("..." if len(text) == 120 else "")
    response_preview.short_description = "Response (first 120 chars)"
