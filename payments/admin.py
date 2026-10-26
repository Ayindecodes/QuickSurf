from django.contrib import admin, messages

from .models import PaymentIntent
from .paystack import verify as ps_verify
from .services import apply_verification, log_paystack, trim_payload


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    """Read-only: intents change only through Paystack verify/webhooks (or the action below)."""

    list_display = ("reference", "user", "amount", "currency", "status", "created", "paid_at")
    list_filter = ("status", "created")
    search_fields = ("reference", "user__email")
    date_hierarchy = "created"
    ordering = ("-created",)
    actions = ("reverify",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Re-verify with Paystack (credits the wallet if paid)")
    def reverify(self, request, queryset):
        results = []
        for intent in queryset.select_related("user"):
            try:
                code, body = ps_verify(intent.reference)
            except Exception as e:
                self.message_user(request, f"{intent.reference}: Paystack unavailable ({e})", level=messages.ERROR)
                continue
            body = trim_payload(body)
            log_paystack(intent.user, intent.reference, "/transaction/verify", {"source": "admin"}, body, code)
            status_text, credited = apply_verification(intent, code, body)
            results.append(f"{intent.reference}: {status_text}{' (credited)' if credited else ''}")
        if results:
            self.message_user(request, "; ".join(results), level=messages.INFO)
