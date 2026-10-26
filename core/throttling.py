import hashlib
import logging

from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, SimpleRateThrottle, UserRateThrottle

from .net import client_ip

logger = logging.getLogger(__name__)


class SafeThrottleMixin:
    """
    Fail open if the cache/Redis is down (don't turn every request into a 500), and identify
    anonymous callers by their real IP (core.net.client_ip) rather than a header they control.
    """

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            logger.exception("%s backend failed; allowing request", type(self).__name__)
            return True

    def get_ident(self, request):
        return client_ip(request)


class SafeUserRateThrottle(SafeThrottleMixin, UserRateThrottle):
    pass


class SafeAnonRateThrottle(SafeThrottleMixin, AnonRateThrottle):
    pass


class SafeScopedRateThrottle(SafeThrottleMixin, ScopedRateThrottle):
    """Per-IP (or per-user) limit for a view's `throttle_scope`: login, register, password_reset, ..."""


class LoginEmailRateThrottle(SafeThrottleMixin, SimpleRateThrottle):
    """Limits login attempts per email address, so one account can't be brute-forced from many IPs."""

    scope = "login_email"

    def get_cache_key(self, request, view):
        email = str(request.data.get("email") or "").strip().lower()
        if not email:
            return None
        return self.cache_format % {"scope": self.scope, "ident": hashlib.sha256(email.encode()).hexdigest()}


class PostOnlyUserRateThrottle(SafeThrottleMixin, UserRateThrottle):
    """A per-user limit that only counts POSTs (buying, topping up), not reads."""

    def allow_request(self, request, view):
        if request.method != "POST":
            return True
        return super().allow_request(request, view)


class PurchaseRateThrottle(PostOnlyUserRateThrottle):
    scope = "purchase"


class TopupRateThrottle(PostOnlyUserRateThrottle):
    scope = "topup"
