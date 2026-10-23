import os

from django.contrib import admin
from django.core.cache import cache
from django.db import connection
from django.urls import path, include
from django.http import JsonResponse, HttpResponse

# JWT Auth (custom obtain that logs IP/UA/device)
from rest_framework_simplejwt.views import TokenRefreshView
from users.views import LoggingTokenObtainPairView

# DRF Spectacular (API docs)
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView


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
            "version": os.environ.get("RENDER_GIT_COMMIT", "dev")[:12],
            "checks": checks,
        },
        status=200 if healthy else 503,
    )


urlpatterns = [
    # --- Admin ---
    path("admin/", admin.site.urls),

    # --- Allauth (email verification / social login) ---
    path("accounts/", include("allauth.urls")),

    # --- JWT Authentication ---
    path("api/token/", LoggingTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),

    # --- Users & Wallets ---
    path("api/users/", include("users.urls")),
    path("api/", include("wallets.urls")),

    # --- Services (airtime & data) ---
    path("api/services/", include("services.urls")),

    # --- Payments (Paystack init/webhook) ---
    path("api/payments/", include("payments.urls")),

    # --- Health ---
    path("api/health/", health_view, name="health"),
    path("dashboard/", lambda request: HttpResponse("🎉 Welcome to your dashboard!")),

    # --- API Schema + Docs ---
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]


