# rewards/services.py
"""Loyalty points: earned on successful purchases (services/signals.py), redeemed into the wallet here."""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum

from wallets import services as wallet_services
from wallets.models import Wallet

from .models import LoyaltyLedger


class RedemptionError(Exception):
    pass


def points_balance(user) -> int:
    return LoyaltyLedger.objects.filter(user=user).aggregate(total=Sum("points"))["total"] or 0


def points_value(points: int) -> Decimal:
    return (Decimal(points) * settings.REWARD_POINT_VALUE).quantize(Decimal("0.01"))


def redeem(user, points: int) -> Decimal:
    """Turn `points` into wallet money (recorded in both the points and wallet ledgers). Returns the naira credited."""
    if not settings.REWARDS_ENABLED:
        raise RedemptionError("Rewards are switched off.")
    if points < settings.REWARDS_MIN_REDEMPTION:
        raise RedemptionError(f"Redeem at least {settings.REWARDS_MIN_REDEMPTION} points.")

    with transaction.atomic():
        Wallet.objects.select_for_update().get(user=user)  # one redemption at a time per user
        available = points_balance(user)
        if points > available:
            raise RedemptionError(f"You have {available} points.")
        naira = points_value(points)
        reference = f"reward:{uuid.uuid4().hex}"
        LoyaltyLedger.objects.create(
            user=user, points=-points, reason=f"Redeemed for ₦{naira}", txn_type="redemption", txn_id=reference
        )
        wallet_services.credit_reward(user, naira, reference)
    return naira
