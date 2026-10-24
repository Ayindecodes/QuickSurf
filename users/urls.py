# users/urls.py

from django.urls import path

from .views import (
    ChangePasswordView,
    DashboardView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    PhoneConfirmView,
    PhoneRequestView,
    PinView,
    RegisterView,
    VerifyEmailConfirmView,
    VerifyEmailRequestView,
)

urlpatterns = [
    path("register/", RegisterView.as_view(), name="user-register"),
    path("register", RegisterView.as_view()),
    path("login/", LoginView.as_view(), name="user-login"),
    path("login", LoginView.as_view()),
    path("logout/", LogoutView.as_view(), name="user-logout"),
    path("dashboard/", DashboardView.as_view(), name="user-dashboard"),
    path("dashboard", DashboardView.as_view()),
    path("verify-email/request/", VerifyEmailRequestView.as_view(), name="verify-email-request"),
    path("verify-email/confirm/", VerifyEmailConfirmView.as_view(), name="verify-email-confirm"),
    path("password-reset/request/", PasswordResetRequestView.as_view(), name="password-reset-request"),
    path("password-reset/confirm/", PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("pin/", PinView.as_view(), name="user-pin"),
    path("phone/request/", PhoneRequestView.as_view(), name="phone-request"),
    path("phone/confirm/", PhoneConfirmView.as_view(), name="phone-confirm"),
    path("me/", MeView.as_view(), name="user-me"),
    path("me/password/", ChangePasswordView.as_view(), name="user-change-password"),
]
