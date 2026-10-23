# services/urls.py
from django.urls import path

from .views import (
    AirtimePurchaseView,
    BeneficiaryDetailView,
    BeneficiaryListView,
    DataPlansView,
    DataPurchaseView,
    DetectNetworkView,
    PurchaseStatusView,  # 🔹 New — check status by client_reference
    ReceiptView,
    ServiceVariationsView,  # 🔹 New — list available data plans for a network
    VTPassCallbackView,
)

urlpatterns = [
    path("airtime/", AirtimePurchaseView.as_view(), name="airtime-purchase"),
    path("data/", DataPurchaseView.as_view(), name="data-purchase"),
    path("plans/<str:network>/", DataPlansView.as_view(), name="data-plans"),
    path("status/<str:client_reference>/", PurchaseStatusView.as_view(), name="purchase-status"),
    path("variations/<str:service_id>/", ServiceVariationsView.as_view(), name="service-variations"),
    path("vtpass/callback/", VTPassCallbackView.as_view(), name="vtpass-callback"),
    path("receipts/<str:client_reference>/", ReceiptView.as_view(), name="purchase-receipt"),
    path("beneficiaries/", BeneficiaryListView.as_view(), name="beneficiaries"),
    path("beneficiaries/<int:pk>/", BeneficiaryDetailView.as_view(), name="beneficiary-detail"),
    path("detect-network/", DetectNetworkView.as_view(), name="detect-network"),
]
