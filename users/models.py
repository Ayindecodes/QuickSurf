from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

from wallets.models import Wallet


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    # Set when the user enters a code we emailed them. Needed before topping up.
    email_verified_at = models.DateTimeField(null=True, blank=True)
    phone = models.CharField(max_length=15, blank=True)  # the user's own number, normalised (0803...)
    # Set when the user enters a code we texted to `phone`. A verified number belongs to one account only.
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    # Set by the fraud checks (users/risk.py), e.g. one card funding many accounts. A flagged account
    # keeps the basic limits until staff clear the flag in the admin.
    flagged_at = models.DateTimeField(null=True, blank=True)
    flag_reason = models.CharField(max_length=200, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["phone"],
                condition=models.Q(phone_verified_at__isnull=False),
                name="users_verified_phone_unique",
            )
        ]

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def phone_verified(self) -> bool:
        return self.phone_verified_at is not None


class TransactionPin(models.Model):
    """
    4-digit PIN needed to buy airtime/data. Stored as a keyed hash (see users/security.py), and
    locked for LOCK_MINUTES after MAX_ATTEMPTS wrong tries in a row.
    """

    MAX_ATTEMPTS = 5
    LOCK_MINUTES = 30

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transaction_pin")
    pin_hash = models.CharField(max_length=64)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class OneTimeCode(models.Model):
    """A short-lived 6-digit code emailed or texted to a user. Only a keyed hash of the code is stored."""

    PURPOSE_VERIFY_EMAIL = "verify_email"
    PURPOSE_RESET_PASSWORD = "reset_password"
    PURPOSE_VERIFY_PHONE = "verify_phone"
    PURPOSES = [
        (PURPOSE_VERIFY_EMAIL, "Verify email"),
        (PURPOSE_RESET_PASSWORD, "Reset password"),
        (PURPOSE_VERIFY_PHONE, "Verify phone"),
    ]
    MAX_ATTEMPTS = 5

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="one_time_codes")
    purpose = models.CharField(max_length=20, choices=PURPOSES)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    # What the code was sent to, when it confirms something specific (the phone number being verified)
    target = models.CharField(max_length=32, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)  # also set when a newer code replaces it
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "purpose", "created_at"])]


# ✅ Signal: Auto-create wallet for every new user
@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_wallet_for_user(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.get_or_create(user=instance)
