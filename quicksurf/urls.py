from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse, HttpResponse

# JWT Auth (custom obtain that logs IP/UA/device)
from rest_framework_simplejwt.views import TokenRefreshView
from users.views import LoggingTokenObtainPairView

# DRF Spectacular (API docs)
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView


def health_view(_request):
    return JsonResponse({"status": "ok", "app": "Quicksurf", "version": "1.0"})


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


