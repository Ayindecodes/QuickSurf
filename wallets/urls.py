# wallets/urls.py
from django.urls import path
from .views import (
    WalletBalanceView,
    WalletSummaryView,
    TransactionListView,
    CreditView,
    LockFundsView,
    UnlockFundsView,
)

urlpatterns = [
    path("wallet/", WalletBalanceView.as_view(), name="wallet-balance"),
    path("wallet/summary/", WalletSummaryView.as_view(), name="wallet-summary"),
    path("wallet/transactions/", TransactionListView.as_view(), name="wallet-transactions"),
    path("wallet/credit/", CreditView.as_view(), name="wallet-credit"),
    path("wallet/lock/", LockFundsView.as_view(), name="wallet-lock"),
    path("wallet/unlock/", UnlockFundsView.as_view(), name="wallet-unlock"),
]
