# users/views.py
import hashlib

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from core.models import LoginActivity
from core.net import client_ip
from core.throttling import LoginEmailRateThrottle, SafeAnonRateThrottle, SafeScopedRateThrottle, SafeUserRateThrottle

from .models import OneTimeCode, TransactionPin
from .security import end_all_sessions, issue_code, redeem_code, send_code, set_pin
from .serializers import (
    CodeSerializer,
    LoginRequestSchema,
    LogoutSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    PinSerializer,
    TokenPairSchema,
    UserSerializer,
)

User = get_user_model()

DEFAULT_THROTTLES = [SafeUserRateThrottle, SafeAnonRateThrottle]
SCOPED_THROTTLES = [*DEFAULT_THROTTLES, SafeScopedRateThrottle]  # plus the view's throttle_scope
VERIFY = OneTimeCode.PURPOSE_VERIFY_EMAIL
RESET = OneTimeCode.PURPOSE_RESET_PASSWORD


# ---------------------------
# Helpers
# ---------------------------
def _stored_email(email) -> str | None:
    """The stored email of the account matching case-insensitively (older accounts kept their case)."""
    return (User.objects.filter(email__iexact=str(email or "").strip())
            .order_by("id").values_list("email", flat=True).first())


def _device_fingerprint(request) -> str:
    # Client may send a stable device id; else derive from UA+IP (bounded length)
    explicit = request.headers.get("X-Device-Id")
    if explicit:
        return explicit[:128]
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:1024]
    ip = client_ip(request) or ""
    return hashlib.sha256(f"{ua}|{ip}".encode("utf-8")).hexdigest()[:32]


def _record_login_activity(user, request) -> None:
    try:
        LoginActivity.objects.create(
            user=user,
            ip=client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:1024],
            device_fingerprint=_device_fingerprint(request),
        )
    except Exception:
        # Never block auth if logging fails
        pass


def _tokens(user) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"refresh": str(refresh), "access": str(refresh.access_token)}


INVALID_CODE = {"code": ["Invalid or expired code."]}


# ---------------------------
# JWT (logs IP/UA/Device)
# ---------------------------
class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    """simplejwt's login, matching the email case-insensitively."""

    def validate(self, attrs):
        attrs[self.username_field] = _stored_email(attrs.get(self.username_field)) or attrs.get(self.username_field)
        return super().validate(attrs)


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
    serializer_class = EmailTokenObtainPairSerializer
    throttle_classes = [*SCOPED_THROTTLES, LoginEmailRateThrottle]
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])
        _record_login_activity(serializer.user, request)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


# ---------------------------
# Register / Login (classic)
# ---------------------------
@extend_schema(
    description="Register a new user and return JWT tokens. A 6-digit code is emailed to verify the address; "
                "it must be verified before the first top-up.",
    request=UserSerializer,
    responses={
        201: TokenPairSchema,
        400: OpenApiResponse(description="Validation error"),
    },
)
class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = SCOPED_THROTTLES
    throttle_scope = "register"

    def post(self, request):
        serializer = UserSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.save()

        # Record login activity for first login after signup (optional but useful)
        _record_login_activity(user, request)

        code = issue_code(user, VERIFY)
        sent = bool(code) and send_code(user, VERIFY, code)
        return Response({**_tokens(user), "email_verification_sent": sent}, status=status.HTTP_201_CREATED)


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
    throttle_classes = [*SCOPED_THROTTLES, LoginEmailRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            return Response({"detail": "email and password are required"}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(request, username=_stored_email(email) or email, password=password)
        if not user:
            return Response({"detail": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)

        _record_login_activity(user, request)

        return Response(_tokens(user), status=status.HTTP_200_OK)


@extend_schema(
    description="Sign out: revoke the given refresh token, or every one of the user's refresh tokens with "
                "`everywhere: true`. Access tokens stop working when they expire.",
    request=LogoutSerializer,
    responses={200: OpenApiResponse(description="Signed out"), 400: OpenApiResponse(description="Bad token")},
)
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data["everywhere"]:
            end_all_sessions(request.user)
            return Response({"detail": "Signed out on all devices."})

        raw = serializer.validated_data.get("refresh")
        if not raw:
            return Response({"refresh": ["This field is required unless everywhere is true."]},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            token = RefreshToken(raw)
            if str(token.get("user_id")) != str(request.user.pk):
                raise TokenError("Token belongs to another user")
            token.blacklist()
        except TokenError:
            return Response({"refresh": ["Invalid or expired refresh token."]}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "Signed out."})


# ---------------------------
# Email verification & password reset (6-digit codes by email)
# ---------------------------
@extend_schema(description="Email the user a new 6-digit verification code (at most one a minute).",
               request=None, responses={200: OpenApiResponse(description="Sent")})
class VerifyEmailRequestView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = SCOPED_THROTTLES
    throttle_scope = "verify_email"

    def post(self, request):
        user = request.user
        if user.email_verified:
            return Response({"detail": "Your email is already verified."})
        code = issue_code(user, VERIFY)
        if code is None:
            return Response({"detail": "A code was sent less than a minute ago. Please wait before asking again."},
                            status=status.HTTP_429_TOO_MANY_REQUESTS)
        if not send_code(user, VERIFY, code):
            return Response({"detail": "We couldn't send the email. Please try again shortly."},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"detail": f"We sent a code to {user.email}."})


@extend_schema(description="Confirm the email with the 6-digit code.", request=CodeSerializer,
               responses={200: OpenApiResponse(description="Verified"), 400: OpenApiResponse(description="Bad code")})
class VerifyEmailConfirmView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = SCOPED_THROTTLES
    throttle_scope = "verify_email"

    def post(self, request):
        serializer = CodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.email_verified:
            if not redeem_code(user, VERIFY, serializer.validated_data["code"]):
                return Response(INVALID_CODE, status=status.HTTP_400_BAD_REQUEST)
            User.objects.filter(pk=user.pk).update(email_verified_at=timezone.now())
        return Response({"detail": "Email verified.", "email_verified": True})


@extend_schema(description="Email a 6-digit password reset code. Always succeeds, so it can't reveal "
                           "which emails have accounts.",
               request=PasswordResetRequestSerializer, responses={200: OpenApiResponse(description="Requested")})
class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = SCOPED_THROTTLES
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stored = _stored_email(serializer.validated_data["email"])
        user = User.objects.filter(email=stored, is_active=True).first() if stored else None
        if user is not None:
            code = issue_code(user, RESET)
            if code:
                send_code(user, RESET, code)
        return Response({"detail": "If that email has an account, we've sent a code to it."})


@extend_schema(description="Set a new password with the emailed code. Signs the user out everywhere.",
               request=PasswordResetConfirmSerializer,
               responses={200: OpenApiResponse(description="Changed"), 400: OpenApiResponse(description="Bad code")})
class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = SCOPED_THROTTLES
    throttle_scope = "password_reset"

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Check the new password first (the same way for every email), so a weak one doesn't use up the code.
        try:
            validate_password(data["new_password"], user=User(email=data["email"]))
        except DjangoValidationError as e:
            return Response({"new_password": list(e.messages)}, status=status.HTTP_400_BAD_REQUEST)

        stored = _stored_email(data["email"])
        user = User.objects.filter(email=stored, is_active=True).first() if stored else None
        if user is None or not redeem_code(user, RESET, data["code"]):
            return Response(INVALID_CODE, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(data["new_password"])
        user.email_verified_at = user.email_verified_at or timezone.now()  # the code proved they own the email
        user.save(update_fields=["password", "email_verified_at"])
        end_all_sessions(user)
        return Response({"detail": "Password changed. Please log in again."})


# ---------------------------
# Transaction PIN
# ---------------------------
@extend_schema(description="GET: whether a transaction PIN is set. POST {pin, password}: set or change it "
                           "(4 digits, not repeated/sequential).",
               request=PinSerializer, responses={200: OpenApiResponse(description="PIN status")})
class PinView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "pin_change"

    def get_throttles(self):
        throttles = SCOPED_THROTTLES if self.request.method == "POST" else DEFAULT_THROTTLES
        return [throttle() for throttle in throttles]

    def get(self, request):
        record = TransactionPin.objects.filter(user=request.user).first()
        locked_until = record.locked_until if record and record.locked_until and record.locked_until > timezone.now() else None
        return Response({"has_pin": record is not None, "locked_until": locked_until})

    def post(self, request):
        serializer = PinSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["password"]):
            return Response({"password": ["Wrong password."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            set_pin(request.user, serializer.validated_data["pin"])
        except ValueError as e:
            return Response({"pin": [str(e)]}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "Transaction PIN saved.", "has_pin": True})


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
                "email_verified": user.email_verified,
                "has_pin": TransactionPin.objects.filter(user=user).exists(),
            },
            status=status.HTTP_200_OK,
        )
