from django.urls import path
from .views import AirtimePurchaseView, DataPurchaseView

urlpatterns = [
    path('airtime/', AirtimePurchaseView.as_view(), name='airtime-purchase'),
    path('data/', DataPurchaseView.as_view(), name='data-purchase'),
]
