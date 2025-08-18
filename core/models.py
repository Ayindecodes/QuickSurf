# core/models.py
from django.db import models
from django.conf import settings

class LoginActivity(models.Model):
    user = models.ForeignKey('users.User', on_delete=models.CASCADE)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    device_fingerprint = models.CharField(max_length=128, blank=True)
    created = models.DateTimeField(auto_now_add=True)
