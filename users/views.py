# users/views.py
from typing import Optional
import hashlib

from django.contrib.auth import get_user_model, authenticate
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated

from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from drf_spectacular.utils import extend_schema, OpenApiResponse

from core.models import LoginActivity

from .serializers import (
    UserSerializer,
    LoginRequestSchema,   # must exist in users/serializers.py
    TokenPairSchema,      # must exist in users/serializers.py
)

User = get_user_model()


# ---------------------------
# Helpers
# ---------------------------
def _client_ip(request) -> Optional[str]:
    # Prefer first IP from X-Forwarded-For when behind a proxy
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _device_fingerprint(request) -> str:
    # Client may send a stable device id; else derive from UA+IP (bounded length)
    explicit = request.headers.get("X-Device-Id")
    if explicit:
        return explicit[:128]
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:1024]
    ip = _client_ip(request) or ""
    return hashlib.sha256(f"{ua}|{ip}".encode("utf-8")).hexdigest()[:32]


def _record_login_activity(user, request) -> None:
    try:
        LoginActivity.objects.create(
            user=user,
            ip=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:1024],
            device_fingerprint=_device_fingerprint(request),
        )
    except Exception:
        # Never block auth if logging fails
        pass


# ---------------------------
# JWT (logs IP/UA/Device)
# ---------------------------
@extend_schema(
    description="Obtain JWT access/refresh tokens (logs IP, user agent, and device fingerprint). "
                "Optionally send `X-Device-Id` header for a stable device identifier.",
    request=LoginRequestSchema,
    responses={
        200: TokenPairSchema,
        401: OpenApiResponse(description="Invalid credentials"),
        400: OpenApiResponse(description="Bad request"),
    },
)
class LoggingTokenObtainPairView(TokenObtainPairView):
    """
    Use this in urls.py at /api/token/ to replace the default.
    """
    serializer_class = TokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        # The base view has validated credentials; serializer.user is available via get_serializer
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.user
            _record_login_activity(user, request)
        return response


# ---------------------------
# Register / Login (classic)
# ---------------------------
@extend_schema(
    description="Register a new user and return JWT tokens.",
    request=UserSerializer,
    responses={
        201: TokenPairSchema,
        400: OpenApiResponse(description="Validation error"),
    },
)
class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.save()
        refresh = RefreshToken.for_user(user)

        # Record login activity for first login after signup (optional but useful)
        _record_login_activity(user, request)

        return Response(
            {"refresh": str(refresh), "access": str(refresh.access_token)},
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    description="Login with email & password, return JWT tokens (also logs IP/UA/device).",
    request=LoginRequestSchema,
    responses={
        200: TokenPairSchema,
        401: OpenApiResponse(description="Invalid credentials"),
        400: OpenApiResponse(description="Bad request"),
    },
)
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            return Response({"detail": "email and password are required"}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(request, username=email, password=password)
        if not user:
            return Response({"detail": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)

        refresh = RefreshToken.for_user(user)

        _record_login_activity(user, request)

        return Response(
            {"refresh": str(refresh), "access": str(refresh.access_token)},
            status=status.HTTP_200_OK,
        )


# ---------------------------
# Me / Dashboard
# ---------------------------
@extend_schema(
    description="Authenticated user dashboard summary.",
    request=None,
    responses={200: UserSerializer},  # simplified payload
)
class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response(
            {
                "id": user.id,
                "email": user.email,
                "date_joined": user.date_joined,
            },
            status=status.HTTP_200_OK,
        )

