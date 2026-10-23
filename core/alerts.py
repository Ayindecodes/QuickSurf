# core/alerts.py
"""Counting suspicious events and emailing the people on call (OPS_ALERT_EMAILS)."""

import logging

from django.conf import settings
from django.core.cache import cache

from notifications.utils import send_email

logger = logging.getLogger(__name__)

BAD_WEBHOOK_SIGNATURES = "ops:bad-webhook-signatures"


def count_event(key: str, window_seconds: int = 3600) -> None:
    """Count an event in a rolling-ish window (starts at the first event). Never raises."""
    try:
        if not cache.add(key, 1, window_seconds):
            cache.incr(key)
    except Exception:
        logger.warning("Could not count %s (cache unavailable)", key)


def event_count(key: str) -> int:
    try:
        return int(cache.get(key) or 0)
    except Exception:
        return 0


def notify_ops(subject: str, lines: list[str], kinds: list[str] | None = None, quiet_seconds: int = 3600) -> bool:
    """
    Email OPS_ALERT_EMAILS. With `kinds`, the same set of problems is emailed at most once per
    quiet_seconds, so a cron job running every 15 minutes doesn't flood the inbox. Returns whether sent.
    """
    recipients = [email for email in settings.OPS_ALERT_EMAILS if email]
    if not recipients:
        return False
    if kinds:
        try:
            if not cache.add("ops:notified:" + ",".join(sorted(kinds)), 1, quiet_seconds):
                return False
        except Exception:
            pass  # cache down: better a repeated email than a missed alert
    body = "\n".join(lines) + "\n\nRunbooks: docs/runbooks/ in the QuickSurf repository."
    return all([send_email(email, subject, body) for email in recipients])
