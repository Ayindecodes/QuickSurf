import logging

from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

logger = logging.getLogger(__name__)


class SafeUserRateThrottle(UserRateThrottle):
    """
    Fail-open throttle wrapper.
    If cache/Redis is down, do not crash auth endpoints with 500.
    """

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            logger.exception("User throttle backend failed; allowing request")
            return True


class SafeAnonRateThrottle(AnonRateThrottle):
    """
    Fail-open throttle wrapper for anonymous requests.
    """

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            logger.exception("Anon throttle backend failed; allowing request")
            return True

