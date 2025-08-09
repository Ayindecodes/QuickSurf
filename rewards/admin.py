from django.contrib import admin
from .models import LoyaltyLedger

@admin.register(LoyaltyLedger)
class LoyaltyLedgerAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "points", "reason", "txn_type", "txn_id", "created_at")
    search_fields = ("user__email", "txn_id", "reason")
    list_filter = ("txn_type", "created_at")
    date_hierarchy = "created_at"
