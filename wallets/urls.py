from django.urls import path
from .views import (
    WalletBalanceView,
    TransactionListView,
    CreditView,
    LockFundsView,
    UnlockFundsView,
)

urlpatterns = [
    path('balance/', WalletBalanceView.as_view(), name='wallet-balance'),
    path('transactions/', TransactionListView.as_view(), name='wallet-transactions'),
    path('credit/', CreditView.as_view(), name='credit-wallet'),
    path('lock/', LockFundsView.as_view(), name='lock-funds'),
    path('unlock/', UnlockFundsView.as_view(), name='unlock-funds'),
]
