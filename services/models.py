from django.db import models
from users.models import User


class AirtimeTransaction(models.Model):
    NETWORK_CHOICES = [
        ('mtn', 'MTN'),
        ('glo', 'Glo'),
        ('airtel', 'Airtel'),
        ('9mobile', '9mobile'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='airtime_txns')
    network = models.CharField(max_length=10, choices=NETWORK_CHOICES)
    phone = models.CharField(max_length=15)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, default='pending')  # pending, successful, failed
    client_reference = models.CharField(max_length=100, unique=True)  # 🔹 Always required & unique
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

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='data_txns')
    network = models.CharField(max_length=10, choices=NETWORK_CHOICES)
    phone = models.CharField(max_length=15)
    plan = models.CharField(max_length=50)  # e.g. 1GB, 2GB, etc
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, default='pending')  # pending, successful, failed
    external_id = models.CharField(max_length=100, blank=True, null=True)
    client_reference = models.CharField(max_length=100, unique=True)  # 🔹 Always required & unique
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} | {self.network} | {self.plan} | ₦{self.amount}"


class ProviderLog(models.Model):
    SERVICE_CHOICES = [
        ('airtime', 'Airtime'),
        ('data', 'Data'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    service_type = models.CharField(max_length=10, choices=SERVICE_CHOICES)
    client_reference = models.CharField(max_length=100, blank=True, null=True)
    request_payload = models.JSONField()
    response_payload = models.JSONField()
    status_code = models.CharField(max_length=10)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.service_type} | {self.client_reference} | {self.status_code}"
