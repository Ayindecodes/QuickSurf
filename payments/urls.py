# payments/urls.py
from django.urls import path
from .views import PaymentInitView, PaymentVerifyView, PaystackWebhookView

urlpatterns = [
    path("init/", PaymentInitView.as_view(), name="payments_init"),
    path("verify/<str:reference>/", PaymentVerifyView.as_view(), name="payments_verify"),
    path("webhook/", PaystackWebhookView.as_view(), name="payments_webhook"),
]

