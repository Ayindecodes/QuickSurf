# services/urls.py
from django.urls import path
from .views import (
    AirtimePurchaseView,
    DataPlansView,
    DataPurchaseView,
    PurchaseStatusView,       # 🔹 New — check status by client_reference
    ServiceVariationsView,    # 🔹 New — list available data plans for a network
    VTPassCallbackView,
)

urlpatterns = [
    path("airtime/", AirtimePurchaseView.as_view(), name="airtime-purchase"),
    path("data/", DataPurchaseView.as_view(), name="data-purchase"),
    path("plans/<str:network>/", DataPlansView.as_view(), name="data-plans"),
    path("status/<str:client_reference>/", PurchaseStatusView.as_view(), name="purchase-status"),
    path("variations/<str:service_id>/", ServiceVariationsView.as_view(), name="service-variations"),
    path("vtpass/callback/", VTPassCallbackView.as_view(), name="vtpass-callback"),
]
