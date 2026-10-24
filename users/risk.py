# users/risk.py
"""
Signs that one person runs several accounts (to get round limits or abuse rewards), or that a card
isn't theirs. A match flags the account rather than blocking it: families share cards and phones.
A flagged account keeps the basic limits (users/limits.py) until staff clear the flag in the admin,
and ops_alerts reports new flags.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

DEVICE_WINDOW = timedelta(days=30)


def flag(user, reason: str) -> None:
    """Flag the account (once; the first reason is kept)."""
    from users.models import User

    if User.objects.filter(pk=user.pk, flagged_at__isnull=True).update(
        flagged_at=timezone.now(), flag_reason=reason[:200]
    ):
        user.flagged_at, user.flag_reason = timezone.now(), reason[:200]
        logger.warning("Account %s flagged: %s", user.pk, reason)


def check_payer(user, fingerprint: str) -> None:
    """Flag the account if the card or bank account that just paid has funded too many accounts."""
    from payments.models import PaymentIntent

    if not fingerprint:
        return
    accounts = (
        PaymentIntent.objects.filter(payer_fingerprint=fingerprint, status="success").values("user").distinct().count()
    )
    if accounts > settings.CARD_MAX_ACCOUNTS:
        flag(user, f"The card or bank account that paid has funded {accounts} accounts")


def check_device(user, device_id: str) -> None:
    """Flag the account if its device has signed in to too many accounts lately. Only app-sent device IDs count."""
    from core.models import LoginActivity

    if not device_id.startswith("d:"):
        return
    accounts = (
        LoginActivity.objects.filter(device_fingerprint=device_id, created__gte=timezone.now() - DEVICE_WINDOW)
        .values("user")
        .distinct()
        .count()
    )
    if accounts > settings.DEVICE_MAX_ACCOUNTS:
        flag(user, f"This device signed in to {accounts} accounts in 30 days")
