from django.db import models
from django.utils.translation import gettext_lazy as _
from users.models import User


class PaystackPayment(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_SUCCESS, _("Success")),
        (STATUS_FAILED, _("Failed")),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="paystack_payments")
    reference = models.CharField(max_length=100, unique=True, help_text=_("Paystack reference for this payment"))
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text=_("Amount in Naira"))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    raw_response = models.JSONField(null=True, blank=True, help_text=_("Full Paystack API response"))
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = _("Paystack Payment")
        verbose_name_plural = _("Paystack Payments")

    def __str__(self):
        return f"{self.user.email} | ₦{self.amount} | {self.status}"
