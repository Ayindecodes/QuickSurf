# services/models.py
from django.db import models
from django.core.validators import MinValueValidator, RegexValidator
from django.utils import timezone
from users.models import User

PHONE_VALIDATOR = RegexValidator(
    regex=r"^\+?\d{7,15}$",
    message="Enter a valid phone number (7–15 digits, optional leading +).",
)

STATUS_CHOICES = [
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


class AirtimeTransaction(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="airtime_txns")
    network = models.CharField(max_length=10, choices=NETWORK_CHOICES)
    phone = models.CharField(max_length=15, validators=[PHONE_VALIDATOR])
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(1)])
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    client_reference = models.CharField(max_length=100, unique=True, db_index=True)

    # Optional provider metadata (helps reconciliation / admin)
    provider_request_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    provider_status = models.CharField(max_length=64, blank=True, null=True)

    timestamp = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-timestamp",)
        indexes = [
            models.Index(fields=["status", "timestamp"]),
            models.Index(fields=["network", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.user.email} | {self.network} | ₦{self.amount}"


class DataTransaction(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="data_txns")
    network = models.CharField(max_length=10, choices=NETWORK_CHOICES)
    phone = models.CharField(max_length=15, validators=[PHONE_VALIDATOR])
    plan = models.CharField(max_length=50)  # e.g. 1GB, 2GB, etc
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(1)])
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    # You already had this:
    external_id = models.CharField(max_length=100, blank=True, null=True)

    client_reference = models.CharField(max_length=100, unique=True, db_index=True)

    # Optional provider metadata (parallel to airtime)
    provider_request_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    provider_status = models.CharField(max_length=64, blank=True, null=True)

    timestamp = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-timestamp",)
        indexes = [
            models.Index(fields=["status", "timestamp"]),
            models.Index(fields=["network", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.user.email} | {self.network} | {self.plan} | ₦{self.amount}"


class ProviderLog(models.Model):
    """
    General provider I/O log. Kept backward-compatible with your fields,
    and extended with optional fields used by the new vtpass helper + admin.
    """
    SERVICE_CHOICES = [
        ("airtime", "Airtime"),
        ("data", "Data"),
        ("vtpass", "VTpass"),
        ("paystack", "Paystack"),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    service_type = models.CharField(max_length=10, choices=SERVICE_CHOICES, default="vtpass")

    # Your existing correlation field:
    client_reference = models.CharField(max_length=100, blank=True, null=True, db_index=True)

    # ➕ Optional: more correlation/diagnostics (non-breaking additions)
    request_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)  # e.g. VTpass request_id
    endpoint = models.CharField(max_length=128, blank=True, null=True)                   # e.g. "/pay", "/requery"
    provider = models.CharField(max_length=32, blank=True, null=True)                    # e.g. "vtpass", "paystack"
    response_time_ms = models.IntegerField(blank=True, null=True)
    error_message = models.CharField(max_length=255, blank=True, null=True)

    # Payloads (store masked if possible)
    request_payload = models.JSONField()
    response_payload = models.JSONField()

    # Keep as CharField to avoid migration pain; store numeric strings like "200"
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

