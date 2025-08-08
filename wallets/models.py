from django.conf import settings
from django.db import models

class Wallet(models.Model):
    user        = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    balance     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    locked      = models.DecimalField(max_digits=12, decimal_places=2, default=0)

class Transaction(models.Model):
    wallet      = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    amount      = models.DecimalField(max_digits=12, decimal_places=2)
    type        = models.CharField(max_length=10)  # e.g. "credit", "lock", "unlock"
    timestamp   = models.DateTimeField(auto_now_add=True)
    reference   = models.CharField(max_length=50, blank=True)  # optional external ID
