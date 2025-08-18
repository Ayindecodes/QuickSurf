from __future__ import annotations

from django.db import models
from django.core.validators import MinValueValidator, RegexValidator
from django.utils import timezone
from users.models import User

# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------
PHONE_VALIDATOR = RegexValidator(
    regex=r"^\+?\d{7,15}$",
    message="Enter a valid phone number (7–15 digits, optional leading +).",
)

# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------
STATUS_CHOICES = [
    ("initiated", "Initiated"),
    ("pending", "Pending"),
    ("successful", "Successful"),
    ("failed", "Failed"),
]

NETWORK_CHOICES = [
    ("mtn", "MTN"),
    ("glo", "Glo"),
    ("airtel", "Airtel"),
    ("9mobile", "9mobile"),
]


# ---------------------------------------------------------------------------
# Airtime
# ---------------------------------------------------------------------------
class AirtimeTransaction(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="airtime_txns")
    network = models.CharField(max_length=10, choices=NETWORK_CHOICES)
    phone = models.CharField(max_length=15, validators=[PHONE_VALIDATOR])
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(1)])

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="initiated")
    client_reference = models.CharField(max_length=100, unique=True, db_index=True)

    # Provider metadata
    provider_request_id = models.CharField(
        max_length=100, blank=True, null=True, db_index=True,
        help_text="Provider-side request/reference id (e.g., VTpass request_id)",
    )
    provider_reference = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="Provider transactionId or equivalent",
    )
    provider_status = models.CharField(max_length=64, blank=True, null=True)

    # Optional raw response for audit/debug
    raw_response = models.JSONField(blank=True, null=True)

    timestamp = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-timestamp",)
        indexes = [
            models.Index(fields=["status", "timestamp"]),
            models.Index(fields=["network", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.user.email} | {self.network} | ₦{self.amount} | {self.status}"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
class DataTransaction(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="data_txns")
    network = models.CharField(max_length=10, choices=NETWORK_CHOICES)
    phone = models.CharField(max_length=15, validators=[PHONE_VALIDATOR])
    plan = models.CharField(max_length=50)  # e.g. 1GB, 2GB, etc
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(1)])

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="initiated")
    client_reference = models.CharField(max_length=100, unique=True, db_index=True)

    # Optional external mapping (kept from your previous model)
    external_id = models.CharField(max_length=100, blank=True, null=True)

    # Provider metadata
    provider_request_id = models.CharField(
        max_length=100, blank=True, null=True, db_index=True,
        help_text="Provider-side request/reference id (e.g., VTpass request_id)",
    )
    provider_reference = models.CharField(
        max_length=100, blank=True, null=True,
        help_text="Provider transactionId or equivalent",
    )
    provider_status = models.CharField(max_length=64, blank=True, null=True)

    raw_response = models.JSONField(blank=True, null=True)

    timestamp = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-timestamp",)
        indexes = [
            models.Index(fields=["status", "timestamp"]),
            models.Index(fields=["network", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.user.email} | {self.network} | {self.plan} | ₦{self.amount} | {self.status}"


# ---------------------------------------------------------------------------
# Provider I/O log (for VTpass/Paystack etc.)
# ---------------------------------------------------------------------------
class ProviderLog(models.Model):
    """General provider I/O log with masked payloads where possible."""

    SERVICE_CHOICES = [
        ("airtime", "Airtime"),
        ("data", "Data"),
        ("vtpass", "VTpass"),
        ("paystack", "Paystack"),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    service_type = models.CharField(max_length=10, choices=SERVICE_CHOICES, default="vtpass")

    # Correlation fields
    client_reference = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    request_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)

    endpoint = models.CharField(max_length=128, blank=True, null=True)
    provider = models.CharField(max_length=32, blank=True, null=True)
    response_time_ms = models.IntegerField(blank=True, null=True)
    error_message = models.CharField(max_length=255, blank=True, null=True)

    request_payload = models.JSONField()
    response_payload = models.JSONField()

    status_code = models.CharField(max_length=10)

    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-timestamp",)
        indexes = [
            models.Index(fields=["service_type", "timestamp"]),
            models.Index(fields=["status_code", "timestamp"]),
            models.Index(fields=["request_id"]),
        ]

    def __str__(self):
        ref = self.client_reference or self.request_id or "-"
        return f"{self.service_type} | {ref} | {self.status_code}"

