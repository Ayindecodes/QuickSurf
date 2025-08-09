from django.db import models
from django.conf import settings

class LoyaltyLedger(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="loyalty_entries")
    points = models.PositiveIntegerField()
    reason = models.CharField(max_length=128)
    txn_type = models.CharField(max_length=16, choices=[("airtime","Airtime"),("data","Data")])
    txn_id = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["txn_type", "txn_id"]),
        ]

    def __str__(self):
        return f"{self.user_id} +{self.points} {self.txn_type} {self.txn_id}"
