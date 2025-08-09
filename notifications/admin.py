from django.contrib import admin
from .models import EmailLog

@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("id", "to", "subject", "status", "created_at")
    search_fields = ("to", "subject")
    list_filter = ("status", "created_at")
    date_hierarchy = "created_at"
