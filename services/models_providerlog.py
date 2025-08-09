from django.db import models
from users.models import User

class ProviderLog(models.Model):
    SERVICE_TYPE_CHOICES = [
        ('airtime', 'Airtime'),
        ('data', 'Data'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    service_type = models.CharField(max_length=10, choices=SERVICE_TYPE_CHOICES)
    client_reference = models.CharField(max_length=100, blank=True, null=True)
    request_payload = models.JSONField()
    response_payload = models.JSONField()
    success = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} | {self.service_type} | {self.client_reference or 'no-ref'}"
