# wallets/urls.py
from django.urls import path
from .views import (
    WalletBalanceView,
    WalletSummaryView,
    TransactionListView,
    AdjustView,
)

urlpatterns = [
    path("wallet/", WalletBalanceView.as_view(), name="wallet-balance"),
    path("wallet/summary/", WalletSummaryView.as_view(), name="wallet-summary"),
    path("wallet/transactions/", TransactionListView.as_view(), name="wallet-transactions"),
    path("wallet/adjust/", AdjustView.as_view(), name="wallet-adjust"),
]
