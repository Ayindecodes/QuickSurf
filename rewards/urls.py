# rewards/urls.py
from django.urls import path

from .views import RedeemView, RewardsView

urlpatterns = [
    path("", RewardsView.as_view(), name="rewards"),
    path("redeem/", RedeemView.as_view(), name="rewards-redeem"),
]
