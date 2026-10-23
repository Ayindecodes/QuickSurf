# core/staff_mfa.py
from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class StaffMFAMiddleware:
    """
    When STAFF_MFA_REQUIRED is on, staff must set up an authenticator app (TOTP) before they can use
    the admin. They're sent to allauth's setup page and come back to the admin afterwards.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (settings.STAFF_MFA_REQUIRED and request.path.startswith(f"/{settings.ADMIN_URL}")
                and user is not None and user.is_authenticated and user.is_staff and not _has_totp(user)):
            return redirect(f"{reverse('mfa_activate_totp')}?next={request.path}")
        return self.get_response(request)


def _has_totp(user) -> bool:
    from allauth.mfa.models import Authenticator

    return Authenticator.objects.filter(user=user, type=Authenticator.Type.TOTP).exists()
