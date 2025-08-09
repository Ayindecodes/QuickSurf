from django.urls import path
from .views import paystack_webhook

urlpatterns = [
    path("webhook/", paystack_webhook, name="paystack-webhook"),
]
