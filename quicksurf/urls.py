from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from django.http import HttpResponse

urlpatterns = [
    # Admin Panel
    path('admin/', admin.site.urls),

    # Allauth (for email verification / social login etc.)
    path('accounts/', include('allauth.urls')),

    # JWT Auth
    path('api/token/',         TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(),    name='token_refresh'),

    # Users and Wallets
    path('api/users/',   include('users.urls')),
    path('api/wallets/', include('wallets.urls')),

    # Services (airtime & data)
    path('api/services/', include('services.urls')),

    # Dashboard (optional)
    path('dashboard/', lambda request: HttpResponse("🎉 Welcome to your dashboard!")),
]

