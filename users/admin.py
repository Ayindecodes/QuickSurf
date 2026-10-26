# users/admin.py
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm
from django.contrib.auth.forms import UserChangeForm as DjangoUserChangeForm
from django.utils import timezone

from wallets.models import Wallet

from .models import TransactionPin, User
from .security import end_all_sessions


class UserCreationForm(BaseUserCreationForm):
    class Meta:
        model = User
        fields = ("email",)


class UserChangeForm(DjangoUserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


class WalletInline(admin.StackedInline):
    """The customer's wallet, read-only (balances only change through the ledger)."""

    model = Wallet
    fields = readonly_fields = ("balance", "locked", "updated_at")
    can_delete = False
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Look customers up by email. Support actions are recorded in each user's admin history."""

    form = UserChangeForm
    add_form = UserCreationForm
    ordering = ("-date_joined",)
    list_display = ("email", "date_joined", "email_verified_at", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    search_fields = ("email",)
    readonly_fields = ("last_login", "date_joined", "email_verified_at")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Status", {"fields": ("is_active", "email_verified_at", "last_login", "date_joined")}),
        ("Staff access", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
    inlines = (WalletInline,)
    actions = ("unlock_pin", "sign_out_everywhere", "mark_email_verified")

    @admin.action(description="Unlock transaction PIN (after too many wrong tries)", permissions=("view",))
    def unlock_pin(self, request, queryset):
        for user in queryset:
            if TransactionPin.objects.filter(user=user).update(failed_attempts=0, locked_until=None):
                self.log_change(request, user, "Unlocked transaction PIN")
        self.message_user(request, "Transaction PINs unlocked.", level=messages.INFO)

    @admin.action(description="Sign out on all devices", permissions=("view",))
    def sign_out_everywhere(self, request, queryset):
        for user in queryset:
            end_all_sessions(user)
            self.log_change(request, user, "Signed out on all devices")
        self.message_user(request, "Users signed out on all devices.", level=messages.INFO)

    @admin.action(description="Mark email as verified (after checking it by other means)", permissions=("view",))
    def mark_email_verified(self, request, queryset):
        for user in queryset.filter(email_verified_at__isnull=True):
            User.objects.filter(pk=user.pk).update(email_verified_at=timezone.now())
            self.log_change(request, user, "Marked email as verified")
        self.message_user(request, "Emails marked as verified.", level=messages.INFO)
