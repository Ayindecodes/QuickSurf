# payments/models.py
from django.db import models
from django.conf import settings
from django.utils import timezone
from users.models import User

class PaymentIntent(models.Model):
    STATUS = [
        ("initialized", "Initialized"),
        ("pending", "Pending"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("abandoned", "Abandoned"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payment_intents")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="NGN")
    reference = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.CharField(max_length=16, choices=STATUS, default="initialized")

    # Paystack fields
    authorization_url = models.URLField(blank=True, null=True)
    access_code = models.CharField(max_length=64, blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)

    # Raw provider payloads for audit
    init_response = models.JSONField(blank=True, null=True)
    verify_response = models.JSONField(blank=True, null=True)
    webhook_events = models.JSONField(blank=True, null=True)  # optional list-append

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created",)

    def mark_success(self, when=None):
        self.status = "success"
        if when and not self.paid_at:
            self.paid_at = when
        self.save(update_fields=["status", "paid_at", "updated"])
