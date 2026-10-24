import os

from allauth.account.decorators import secure_admin_login
from django.conf import settings
from django.contrib import admin
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path

# DRF Spectacular (API docs)
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

# JWT Auth (custom obtain that logs IP/UA/device)
from rest_framework_simplejwt.views import TokenRefreshView

from users.views import LoggingTokenObtainPairView


def health_view(_request):
    """For uptime monitors: 200 when the database answers, 503 when it doesn't. The cache is reported too."""
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"
    try:
        cache.set("health-check", "ok", 10)
        checks["cache"] = "ok" if cache.get("health-check") == "ok" else "error"
    except Exception:
        checks["cache"] = "error"

    healthy = checks["database"] == "ok"
    return JsonResponse(
        {
            "status": "ok" if healthy else "error",
            "app": "Quicksurf",
            "version": (os.environ.get("APP_VERSION") or os.environ.get("RENDER_GIT_COMMIT") or "dev")[:12],
            "checks": checks,
        },
        status=200 if healthy else 503,
    )


# Staff sign in to the admin through allauth, which asks for their authenticator code.
admin.site.login = secure_admin_login(admin.site.login)

# The customer API. Documented and versioned at /api/v1/; also served unchanged at /api/ so existing
# clients and the Paystack/VTpass callback URLs set in their dashboards keep working.
api_patterns = [
    path("token/", LoggingTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("users/", include("users.urls")),
    path("", include("wallets.urls")),
    path("services/", include("services.urls")),
    path("payments/", include("payments.urls")),
    path("rewards/", include("rewards.urls")),
    path("health/", health_view, name="health"),
]

urlpatterns = [
    # --- Admin (path set by ADMIN_URL) ---
    path(settings.ADMIN_URL, admin.site.urls),
    # --- Allauth: staff sign-in and 2FA setup for the admin (public signup is closed) ---
    path("accounts/", include("allauth.urls")),
    # --- API Schema + Docs (they describe /api/v1/) ---
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # --- API ---
    path("api/v1/", include((api_patterns, "v1"))),
    path("api/", include(api_patterns)),
]
