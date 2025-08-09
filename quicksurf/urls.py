from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponse

# JWT Auth
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

# DRF Spectacular (API docs)
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    # --- Admin ---
    path("admin/", admin.site.urls),

    # --- Allauth (email verification / social login) ---
    path("accounts/", include("allauth.urls")),

    # --- JWT Authentication ---
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),

    # --- Users & Wallets ---
    path("api/users/", include("users.urls")),
    path("api/wallets/", include("wallets.urls")),

    # --- Services (airtime & data) ---
    path("api/services/", include("services.urls")),

    # --- Payments ---
    path("api/payments/", include("payments.urls")),

    # --- Dashboard (optional) ---
    path("dashboard/", lambda request: HttpResponse("🎉 Welcome to your dashboard!")),

    # --- API Schema + Swagger Docs ---
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]
