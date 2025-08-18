from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from .models import AirtimeTransaction, DataTransaction, ProviderLog

try:
    # Safe import; admin remains usable even if vtpass client isn't wired
    from .vtpass import requery_status, strict_map_outcome  # type: ignore
except Exception:  # pragma: no cover
    requery_status = None
    strict_map_outcome = None


# -----------------------------
# Helpers
# -----------------------------

def _mask_phone(s: str | None) -> str:
    s = str(s or "")
    return f"****{s[-4:]}" if len(s) >= 4 else "****"


def _short(s, n=120):
    if s is None:
        return ""
    s = str(s)
    return s[:n] + ("..." if len(s) == n else "")


# -----------------------------
# Airtime
# -----------------------------
@admin.register(AirtimeTransaction)
class AirtimeTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "client_reference",
        "user",
        "network",
        "phone_masked",
        "amount",
        "status",
        "provider_reference",
        "provider_status",
        "timestamp",
    )
    search_fields = (
        "client_reference",
        "provider_reference",
        "provider_request_id",
        "provider_status",
        "user__email",
        "user__username",
        "phone",
    )
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
        "provider_request_id",
        "provider_reference",
        "provider_status",
        "raw_response",
        "timestamp",
        "updated",
    )

    actions = ("admin_requery_status",)

    @admin.display(description="Phone")
    def phone_masked(self, obj: AirtimeTransaction) -> str:
        return _mask_phone(obj.phone)

    @admin.action(description="Re-query provider status")
    def admin_requery_status(self, request, queryset):
        if requery_status is None or strict_map_outcome is None:
            self.message_user(request, "Requery not available in this build.", level=messages.WARNING)
            return
        updated = 0
        for tx in queryset:
            try:
                res = requery_status(tx.client_reference)
                body = res.get("provider", {}) if isinstance(res, dict) else {}
                outcome = strict_map_outcome(body)
                if outcome != tx.status:
                    tx.status = outcome
                    tx.provider_status = body.get("response_description") or tx.provider_status
                    tx.save(update_fields=["status", "provider_status", "updated"])
                    updated += 1
            except Exception as e:  # pragma: no cover
                self.message_user(request, f"Error on {tx.client_reference}: {e}", level=messages.ERROR)
        self.message_user(request, f"Requery complete. Updated {updated} transaction(s).", level=messages.INFO)


# -----------------------------
# Data
# -----------------------------
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
        "provider_reference",
        "provider_status",
        "timestamp",
    )
    search_fields = (
        "client_reference",
        "provider_reference",
        "provider_request_id",
        "provider_status",
        "user__email",
        "user__username",
        "phone",
        "plan",
    )
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
        "provider_request_id",
        "provider_reference",
        "provider_status",
        "raw_response",
        "timestamp",
        "updated",
    )

    actions = ("admin_requery_status",)

    @admin.display(description="Phone")
    def phone_masked(self, obj: DataTransaction) -> str:
        return _mask_phone(obj.phone)

    @admin.action(description="Re-query provider status")
    def admin_requery_status(self, request, queryset):
        if requery_status is None or strict_map_outcome is None:
            self.message_user(request, "Requery not available in this build.", level=messages.WARNING)
            return
        updated = 0
        for tx in queryset:
            try:
                res = requery_status(tx.client_reference)
                body = res.get("provider", {}) if isinstance(res, dict) else {}
                outcome = strict_map_outcome(body)
                if outcome != tx.status:
                    tx.status = outcome
                    tx.provider_status = body.get("response_description") or tx.provider_status
                    tx.save(update_fields=["status", "provider_status", "updated"])
                    updated += 1
            except Exception as e:  # pragma: no cover
                self.message_user(request, f"Error on {tx.client_reference}: {e}", level=messages.ERROR)
        self.message_user(request, f"Requery complete. Updated {updated} transaction(s).", level=messages.INFO)


# -----------------------------
# Provider logs
# -----------------------------
@admin.register(ProviderLog)
class ProviderLogAdmin(admin.ModelAdmin):
    list_display = (
        "service_type",
        "client_reference",
        "request_id",
        "status_code",
        "timestamp",
        "response_preview",
    )
    list_filter = ("service_type", "status_code", "timestamp")
    search_fields = ("service_type", "client_reference", "request_id", "status_code")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)

    readonly_fields = (
        "service_type",
        "client_reference",
        "request_id",
        "endpoint",
        "provider",
        "status_code",
        "request_payload",
        "response_payload",
        "timestamp",
    )

    @admin.display(description="Response (first 120 chars)")
    def response_preview(self, obj: ProviderLog):
        return _short(obj.response_payload, 120)

