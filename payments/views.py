# payments/views.py
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status

from wallets.models import Wallet
from services.models import ProviderLog
from .models import PaymentIntent
from .serializers import PaymentInitRequestSerializer, PaymentIntentSerializer
from .paystack import initialize as ps_initialize, verify as ps_verify, valid_webhook

# ---- helpers ----------------------------------------------------------------

def _log_provider(user, client_reference, endpoint, req, resp, status_code, provider="paystack"):
    try:
        ProviderLog.objects.create(
            user=user,
            service_type="paystack",
            client_reference=client_reference,
            request_payload=req or {},
            response_payload=resp or {},
            status_code=str(status_code),
            endpoint=endpoint,
            provider=provider,
        )
    except Exception:
        pass

def _credit_wallet_once(user, amount: Decimal, reference: str):
    """
    Idempotent credit. If we've already marked the intent success, do nothing.
    """
    with transaction.atomic():
        intent = PaymentIntent.objects.select_for_update().get(reference=reference, user=user)
        if intent.status == "success":
            return False  # already credited

        wallet = Wallet.objects.select_for_update().get(user=user)
        wallet.balance += amount
        wallet.save(update_fields=["balance"])

        intent.mark_success(when=timezone.now())
    return True

# ---- endpoints ---------------------------------------------------------------

class PaymentInitView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = PaymentInitRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        amount = Decimal(str(ser.validated_data["amount"]))
        metadata = ser.validated_data.get("metadata") or {}

        # Generate stable reference per attempt (client can send one too if you prefer)
        reference = f"QS-{request.user.id}-{timezone.now().strftime('%Y%m%d%H%M%S%f')[-10:]}"
        intent = PaymentIntent.objects.create(
            user=request.user,
            amount=amount,
            reference=reference,
            status="initialized",
        )

        code, body = ps_initialize(
            email=(getattr(request.user, "email", None) or "user@example.com"),
            amount_naira=amount,
            reference=reference,
            metadata=metadata,
            callback_url=None,  # optionally set a frontend callback
        )
        _log_provider(request.user, reference, "/transaction/initialize", {"amount": str(amount)}, body, code)

        # Defensive parse
        data = (body or {}).get("data") or {}
        intent.authorization_url = data.get("authorization_url")
        intent.access_code = data.get("access_code")
        intent.init_response = body
        intent.status = "pending" if code == 200 and body.get("status") else "pending"
        intent.save(update_fields=["authorization_url", "access_code", "init_response", "status", "updated"])

        return Response({
            "reference": intent.reference,
            "authorization_url": intent.authorization_url,
            "status": intent.status,
        }, status=status.HTTP_201_CREATED)

class PaymentVerifyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, reference: str):
        try:
            intent = PaymentIntent.objects.get(reference=reference, user=request.user)
        except PaymentIntent.DoesNotExist:
            return Response({"detail": "Unknown reference"}, status=404)

        code, body = ps_verify(reference)
        _log_provider(request.user, reference, "/transaction/verify", None, body, code)

        intent.verify_response = body
        intent.save(update_fields=["verify_response", "updated"])

        # Success condition based on Paystack verify payload
        status_text = (body.get("data") or {}).get("status")
        amount_kobo = (body.get("data") or {}).get("amount")
        currency = (body.get("data") or {}).get("currency")

        if code == 200 and body.get("status") and status_text == "success" and currency == "NGN":
            # Idempotent credit
            credited = _credit_wallet_once(request.user, intent.amount, intent.reference)
            return Response({
                "reference": reference,
                "status": "success",
                "credited": credited,
            })

        if status_text in {"failed", "abandoned"}:
            intent.status = "failed"
            intent.save(update_fields=["status", "updated"])
            return Response({"reference": reference, "status": "failed"})

        # still pending
        intent.status = "pending"
        intent.save(update_fields=["status", "updated"])
        return Response({"reference": reference, "status": "pending"})

@method_decorator(csrf_exempt, name="dispatch")
class PaystackWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        sig = request.headers.get("X-Paystack-Signature") or request.headers.get("x-paystack-signature")
        raw = request.body
        if not valid_webhook(sig, raw):
            return Response({"detail": "Invalid signature"}, status=401)

        try:
            payload = request.data  # DRF parses JSON
            event = payload.get("event")
            data = payload.get("data") or {}
            reference = data.get("reference")
        except Exception:
            return Response({"detail": "Malformed payload"}, status=400)

        # Find intent
        try:
            intent = PaymentIntent.objects.select_related("user").get(reference=reference)
        except PaymentIntent.DoesNotExist:
            # Still log for forensic purposes
            _log_provider(None, reference or "-", "webhook", {"event": event}, payload, 200)
            return Response({"detail": "Reference not found"}, status=200)

        # Attach webhook (append behaviour)
        try:
            events = (intent.webhook_events or [])
            events.append(payload)
            intent.webhook_events = events
            intent.save(update_fields=["webhook_events", "updated"])
        except Exception:
            pass

        # Process success
        if event == "charge.success" and (data.get("status") == "success"):
            _log_provider(intent.user, reference, "webhook:charge.success", None, payload, 200)
            _credit_wallet_once(intent.user, intent.amount, intent.reference)
            return Response({"ok": True})

        if event == "charge.failed":
            intent.status = "failed"
            intent.save(update_fields=["status", "updated"])
            _log_provider(intent.user, reference, "webhook:charge.failed", None, payload, 200)
            return Response({"ok": True})

        # Ignore other events
        _log_provider(intent.user, reference, f"webhook:{event}", None, payload, 200)
        return Response({"ok": True})
