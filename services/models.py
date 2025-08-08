from django.db import models
from users.models import User


class AirtimeTransaction(models.Model):
    NETWORK_CHOICES = [
        ('mtn', 'MTN'),
        ('glo', 'Glo'),
        ('airtel', 'Airtel'),
        ('9mobile', '9mobile'),
    ]

    user      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='airtime_txns')
    network   = models.CharField(max_length=10, choices=NETWORK_CHOICES)
    phone     = models.CharField(max_length=15)
    amount    = models.DecimalField(max_digits=10, decimal_places=2)
    status    = models.CharField(max_length=20, default='pending')  # pending, successful, failed
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} | {self.network} | ₦{self.amount}"


class DataTransaction(models.Model):
    NETWORK_CHOICES = [
        ('mtn', 'MTN'),
        ('glo', 'Glo'),
        ('airtel', 'Airtel'),
        ('9mobile', '9mobile'),
    ]

    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='data_txns')
    network     = models.CharField(max_length=10, choices=NETWORK_CHOICES)
    phone       = models.CharField(max_length=15)
    plan        = models.CharField(max_length=50)  # e.g. 1GB, 2GB, etc
    amount      = models.DecimalField(max_digits=10, decimal_places=2)
    status      = models.CharField(max_length=20, default='pending')  # pending, successful, failed
    external_id = models.CharField(max_length=100, blank=True, null=True)
    timestamp   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} | {self.network} | {self.plan} | ₦{self.amount}"
