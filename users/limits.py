# users/limits.py
"""
Verification levels and the daily limits that go with them.

  basic     email confirmed                     BASIC_DAILY_PURCHASE_LIMIT, BASIC_DAILY_TOPUP_LIMIT
  verified  phone confirmed by SMS (one account  DAILY_PURCHASE_LIMIT, DAILY_TOPUP_LIMIT
            per number), and not flagged

A flagged account (users/risk.py) stays at basic. Accounts under a day old are also capped at
NEW_ACCOUNT_DAILY_LIMIT for purchases. A limit of 0 means no limit. Days are Lagos days.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

BASIC = "basic"
VERIFIED = "verified"

ZERO = Decimal("0")


def level(user) -> str:
    return VERIFIED if user.phone_verified and not user.flagged_at else BASIC


def _smallest(*limits) -> int:
    set_limits = [limit for limit in limits if limit]
    return min(set_limits) if set_limits else 0


def daily_purchase_limit(user) -> int:
    limits = [settings.DAILY_PURCHASE_LIMIT]
    if level(user) == BASIC:
        limits.append(settings.BASIC_DAILY_PURCHASE_LIMIT)
    if user.date_joined > timezone.now() - timedelta(days=1):
        limits.append(settings.NEW_ACCOUNT_DAILY_LIMIT)
    return _smallest(*limits)


def daily_topup_limit(user) -> int:
    limits = [settings.DAILY_TOPUP_LIMIT]
    if level(user) == BASIC:
        limits.append(settings.BASIC_DAILY_TOPUP_LIMIT)
    return _smallest(*limits)


def _start_of_day():
    return timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)


def spent_today(user) -> Decimal:
    """Purchases today that are pending or delivered."""
    from services.models import AirtimeTransaction, DataTransaction

    total = ZERO
    for model in (AirtimeTransaction, DataTransaction):
        total += (
            model.objects.filter(
                user=user, status__in=["pending", "successful"], timestamp__gte=_start_of_day()
            ).aggregate(total=Sum("amount"))["total"]
            or ZERO
        )
    return total


def topped_up_today(user) -> Decimal:
    """Top-ups today that are paid or may still be paid (checkouts not yet abandoned)."""
    from payments.models import PaymentIntent

    return (
        PaymentIntent.objects.filter(
            user=user, status__in=["initialized", "pending", "success"], created__gte=_start_of_day()
        ).aggregate(total=Sum("amount"))["total"]
        or ZERO
    )


def summary(user) -> dict:
    """What the profile shows: the level, today's limits and how much of them is used."""
    return {
        "level": level(user),
        "daily_purchase_limit": str(daily_purchase_limit(user)),
        "daily_topup_limit": str(daily_topup_limit(user)),
        "spent_today": str(spent_today(user)),
        "topped_up_today": str(topped_up_today(user)),
    }
