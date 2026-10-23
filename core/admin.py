from django.contrib import admin

from .models import LoginActivity


@admin.register(LoginActivity)
class LoginActivityAdmin(admin.ModelAdmin):
    """Sign-ins (IP, device): read-only, for checking suspicious account activity."""

    list_display = ("created", "user", "ip", "device_fingerprint")
    list_filter = ("created",)
    search_fields = ("user__email", "ip", "device_fingerprint")
    date_hierarchy = "created"
    ordering = ("-created",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
