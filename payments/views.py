# payments/views.py
import logging
import uuid
from decimal import Decimal

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.alerts import BAD_WEBHOOK_SIGNATURES, count_event
from core.net import client_ip
from core.throttling import SafeAnonRateThrottle, SafeUserRateThrottle, TopupRateThrottle
from users import limits

from .models import PaymentIntent
from .paystack import initialize as ps_initialize
from .paystack import valid_webhook
from .paystack import verify as ps_verify
from .serializers import PaymentInitRequestSerializer
from .services import (
    FINAL_STATUSES,
    apply_verification,
    credit_wallet_once,
    log_paystack,
    payer_fingerprint,
    payment_matches,
    set_status_unless_final,
    trim_payload,
)

logger = logging.getLogger(__name__)

# ---- endpoints ---------------------------------------------------------------
PaymentInitResponseSchema = inline_serializer(
    name="PaymentInitResponse",
    fields={
        "reference": drf_serializers.CharField(),
        "authorization_url": drf_serializers.CharField(allow_blank=True, allow_null=True),
        "status": drf_serializers.CharField(),
    },
)

PaymentVerifyResponseSchema = inline_serializer(
    name="PaymentVerifyResponse",
    fields={
        "reference": drf_serializers.CharField(),
        "status": drf_serializers.CharField(),
        "credited": drf_serializers.BooleanField(required=False),
    },
)

WebhookAckSchema = inline_serializer(
    name="PaystackWebhookAck",
    fields={"ok": drf_serializers.BooleanField()},
)

WebhookRequestSchema = inline_serializer(
    name="PaystackWebhookRequest",
    fields={
        "event": drf_serializers.CharField(required=False),
        "data": drf_serializers.DictField(required=False),
    },
)

DetailSchema = inline_serializer(
    name="DetailResponse",
    fields={"detail": drf_serializers.CharField()},
)


class PaymentInitView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [SafeUserRateThrottle, SafeAnonRateThrottle, TopupRateThrottle]

    @extend_schema(
        request=PaymentInitRequestSerializer,
        responses={201: PaymentInitResponseSchema, 502: DetailSchema, 503: DetailSchema},
        description="Initialize a Paystack top-up intent and return authorization URL.",
    )
    def post(self, request):
        if not settings.TOPUPS_ENABLED:
            return Response(
                {"detail": "Top-ups are temporarily unavailable. Please try again later.", "code": "topups_paused"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not request.user.email_verified:
            return Response(
                {"detail": "Verify your email before topping up.", "code": "email_unverified"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if settings.TOPUP_REQUIRES_PHONE and not request.user.phone_verified:
            return Response(
                {"detail": "Verify your phone number before topping up.", "code": "phone_unverified"},
                status=status.HTTP_403_FORBIDDEN,
            )

        ser = PaymentInitRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        amount = Decimal(str(ser.validated_data["amount"]))

        daily = limits.daily_topup_limit(request.user)
        if daily:
            remaining = max(Decimal(daily) - limits.topped_up_today(request.user), Decimal("0"))
            if amount > remaining:
                return Response(
                    {
                        "error": f"You can top up ₦{remaining:,.2f} more today (daily limit ₦{daily:,}).",
                        "code": "daily_topup_limit",
                        "remaining": str(remaining),
                        "level": limits.level(request.user),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        metadata = ser.validated_data.get("metadata") or {}

        # Random, so nobody can guess or pre-empt another user's reference
        reference = f"QS-{uuid.uuid4().hex}"
        intent = PaymentIntent.objects.create(
            user=request.user,
            amount=amount,
            reference=reference,
            status="initialized",
        )

        try:
            code, body = ps_initialize(
                email=(getattr(request.user, "email", None) or "user@example.com"),
                amount_naira=amount,
                reference=reference,
                metadata=metadata,
                callback_url=settings.PAYSTACK_CALLBACK_URL or None,  # None: Paystack dashboard default
            )
        except Exception:
            err_body = {"status": False, "message": "Provider initialize request failed"}
            log_paystack(request.user, reference, "/transaction/initialize", {"amount": str(amount)}, err_body, 502)
            intent.init_response = err_body
            intent.status = "failed"
            intent.save(update_fields=["init_response", "status", "updated"])
            return Response(
                {"detail": "Payment provider unavailable. Please try again."}, status=status.HTTP_502_BAD_GATEWAY
            )
        log_paystack(request.user, reference, "/transaction/initialize", {"amount": str(amount)}, body, code)

        # Defensive parse
        data = (body or {}).get("data") or {}
        intent.authorization_url = data.get("authorization_url")
        intent.access_code = data.get("access_code")
        intent.init_response = body
        initialized = code == 200 and bool((body or {}).get("status"))
        intent.status = "pending" if initialized else "failed"
        intent.save(update_fields=["authorization_url", "access_code", "init_response", "status", "updated"])

        if not initialized:
            return Response(
                {"detail": "Unable to initialize payment at this time."}, status=status.HTTP_502_BAD_GATEWAY
            )

        return Response(
            {
                "reference": intent.reference,
                "authorization_url": intent.authorization_url,
                "status": intent.status,
            },
            status=status.HTTP_201_CREATED,
        )


class PaymentVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={200: PaymentVerifyResponseSchema, 404: DetailSchema, 502: DetailSchema},
        description=(
            "Verify a Paystack top-up reference and credit the wallet once. Status is success, "
            "review (paid a different amount/currency: not credited, support will follow up), "
            "failed, abandoned or pending."
        ),
    )
    def get(self, request, reference: str):
        try:
            intent = PaymentIntent.objects.get(reference=reference, user=request.user)
        except PaymentIntent.DoesNotExist:
            return Response({"detail": "Unknown reference"}, status=404)

        # Credited or under review: nothing Paystack says now can change it, so don't ask again.
        if intent.status in FINAL_STATUSES:
            return Response({"reference": reference, "status": intent.status, "credited": False})

        try:
            code, body = ps_verify(reference)
        except Exception:
            return Response(
                {"detail": "Payment verification unavailable. Please retry."}, status=status.HTTP_502_BAD_GATEWAY
            )
        stored = trim_payload(body)  # what we keep: no card or customer details
        log_paystack(request.user, reference, "/transaction/verify", None, stored, code)

        intent.verify_response = stored
        intent.save(update_fields=["verify_response", "updated"])

        result, credited = apply_verification(intent, code, body)
        response = {"reference": reference, "status": result}
        if result == "success":
            response["credited"] = credited
        return Response(response)


@method_decorator(csrf_exempt, name="dispatch")
class PaystackWebhookView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []  # Paystack sends every webhook from a few IPs; never rate-limit them away

    @extend_schema(
        request=WebhookRequestSchema,
        responses={200: WebhookAckSchema, 400: DetailSchema, 401: DetailSchema},
        description="Handle signed Paystack webhooks. Endpoint is idempotent for repeated events.",
    )
    def post(self, request):
        sig = request.headers.get("X-Paystack-Signature") or request.headers.get("x-paystack-signature")
        raw = request.body
        if not valid_webhook(sig, raw):
            # A burst of these means a wrong PAYSTACK_SECRET_KEY (e.g. after rotating keys) or forgery.
            count_event(BAD_WEBHOOK_SIGNATURES)
            logger.warning("Rejected a Paystack webhook with a bad signature from %s", client_ip(request))
            return Response({"detail": "Invalid signature"}, status=401)

        try:
            payload = request.data  # DRF parses JSON
            event = payload.get("event")
            data = payload.get("data") or {}
            reference = data.get("reference")
        except Exception:
            return Response({"detail": "Malformed payload"}, status=400)
        stored = trim_payload(payload)

        # Find intent
        try:
            intent = PaymentIntent.objects.select_related("user").get(reference=reference)
        except PaymentIntent.DoesNotExist:
            # Still log for forensic purposes
            log_paystack(None, reference or "-", "webhook", {"event": event}, stored, 200)
            return Response({"detail": "Reference not found"}, status=200)

        # Attach webhook (append behaviour)
        try:
            events = intent.webhook_events or []
            events.append(stored)
            intent.webhook_events = events
            intent.save(update_fields=["webhook_events", "updated"])
        except Exception:
            pass

        # Process success: credit only if Paystack charged exactly what we asked for
        if event == "charge.success" and (data.get("status") == "success"):
            log_paystack(intent.user, reference, "webhook:charge.success", None, stored, 200)
            if payment_matches(intent, data):
                credit_wallet_once(intent.user, intent.amount, intent.reference, payer_fingerprint(data))
            else:
                set_status_unless_final(intent, "review")
            return Response({"ok": True})

        if event == "charge.failed":
            set_status_unless_final(intent, "failed")
            log_paystack(intent.user, reference, "webhook:charge.failed", None, stored, 200)
            return Response({"ok": True})

        # Ignore other events
        log_paystack(intent.user, reference, f"webhook:{event}", None, stored, 200)
        return Response({"ok": True})
