# services/views.py
import logging
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import generics, status
from rest_framework import serializers as drf_serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.privacy import mask_pii
from core.request_id import current_request_id
from core.throttling import PurchaseRateThrottle, SafeAnonRateThrottle, SafeUserRateThrottle
from users.security import PinError, check_pin
from wallets import services as wallet_services
from wallets.models import Wallet

from .models import AirtimeTransaction, Beneficiary, DataTransaction, ProviderLog
from .networks import detect_network
from .serializers import (
    AirtimePurchaseRequestSerializer,
    AirtimeTransactionSerializer,
    BeneficiarySerializer,
    DataPurchaseRequestSerializer,
    DataTransactionSerializer,
    normalize_ng_phone,
)
from .settlement import FINAL_STATUSES, ledger_reference, purchase_kind, settle_purchase
from .vtpass import (
    DATA_SERVICE_IDS,
    extract_variations,
    generate_request_id,
    purchase_airtime,
    purchase_data,
    strict_map_outcome,
    transaction_id,
)

try:
    from .vtpass import get_service_variations, requery_status
except ImportError:
    requery_status = None
    get_service_variations = None

logger = logging.getLogger(__name__)
TWO_PLACES = Decimal("0.01")


# ===================== Helpers =====================
class SafePaginator(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def _to_plain_dict(data: Any) -> Dict:
    """request.data as a dict (JSON bodies and form posts are both dicts; anything else is empty)."""
    return data if isinstance(data, dict) else {}


def _to_plain_json(provider_result: Any) -> Dict:
    """The VTpass client's result as a dict (it always returns one; anything else is kept as text)."""
    return provider_result if isinstance(provider_result, dict) else {"raw": str(provider_result)}


def _provider_http_status(maybe_resp: Any) -> int:
    code = getattr(maybe_resp, "status_code", None)
    if isinstance(code, int):
        return code
    if isinstance(maybe_resp, dict) and "http_status" in maybe_resp:
        try:
            return int(maybe_resp["http_status"])
        except Exception:
            return 200
    return 200


def apply_txn_filters(qs, request):
    status_val = request.query_params.get("status")
    network = request.query_params.get("network")
    search = request.query_params.get("search")
    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")

    if status_val:
        qs = qs.filter(status=status_val)
    if network:
        qs = qs.filter(network=network)
    if search:
        qs = qs.filter(Q(phone__icontains=search) | Q(client_reference__icontains=search))

    if date_from:
        d = parse_date(date_from)
        if d:
            qs = qs.filter(timestamp__date__gte=d)
    if date_to:
        d = parse_date(date_to)
        if d:
            qs = qs.filter(timestamp__date__lte=d)

    ordering = request.query_params.get("ordering")
    allowed = {"timestamp", "-timestamp", "amount", "-amount"}
    return qs.order_by(ordering) if ordering in allowed else qs.order_by("-timestamp")


# ---------- VTpass result mapping ----------
def _unwrap_provider(payload: Dict) -> Dict:
    """
    Our vtpass helpers often return a wrapper:
      {"ok": ..., "state": ..., "provider": {...}, "http_status": ...}
    Always unwrap to the inner provider dict for mapping.
    """
    if isinstance(payload, dict) and "provider" in payload and isinstance(payload["provider"], dict):
        return payload["provider"]
    return payload


def _provider_status_code(provider_body: Dict, http_status: int | None = None) -> str:
    code = provider_body.get("code")
    if code is not None:
        return str(code)
    if http_status is not None:
        return str(http_status)
    return str(provider_body.get("http_status", "unknown"))


def _log_provider(
    user,
    service_type: str,
    client_reference: str,
    request_payload: Dict,
    response_payload: Dict,
    http_status: int | None = None,
):
    try:
        ProviderLog.objects.create(
            user=user,
            service_type=service_type,
            client_reference=client_reference,
            request_payload=mask_pii(request_payload),  # no PINs, masked phone numbers
            response_payload=mask_pii(response_payload),
            status_code=_provider_status_code(response_payload, http_status),
        )
    except Exception:
        pass


# ---------- Single-flight lock & cooldown ----------
LOCK_TTL = 60  # seconds
COOLDOWN_SEC = 45  # short window to block same (user+phone+network)


def _lock_key_ref(user_id, service, client_ref):
    return f"lock:{service}:ref:{user_id}:{client_ref}"


def _lock_key_line(user_id, service, network, phone):
    return f"lock:{service}:line:{user_id}:{network}:{phone}"


def _acquire_purchase_locks(k_ref, k_line) -> bool:
    """
    Take the reference lock and the line cooldown, or neither. If the cache itself is down, carry
    on without them: the database (unique reference, wallet row lock, one hold per purchase) still
    stops double charges, and VTpass refuses repeats on a line within 30s (code 019).
    """
    try:
        if not cache.add(k_ref, "1", LOCK_TTL):
            return False
        if not cache.add(k_line, "1", COOLDOWN_SEC):
            cache.delete(k_ref)
            return False
        return True
    except Exception:
        logger.exception("Purchase locks unavailable (cache error); continuing without them")
        return True


def _release_purchase_locks(*keys):
    try:
        cache.delete_many(keys)
    except Exception:
        pass


# ---------- Data plan pricing ----------
class PlanPricingUnavailable(Exception):
    """VTpass's plan list couldn't be fetched or read, so the plan can't be priced safely."""


def _vtpass_plans(network: str) -> list:
    """VTpass's current data plans for a network, as [{"code", "name", "vtpass_price"}]."""
    if get_service_variations is None:
        raise PlanPricingUnavailable()
    res = get_service_variations(DATA_SERVICE_IDS[network]) or {}
    plans = []
    for v in extract_variations(res.get("content")):
        if not isinstance(v, dict) or not v.get("variation_code"):
            continue
        try:
            price = Decimal(str(v.get("variation_amount")))
        except InvalidOperation:
            continue
        if price.is_finite() and price > 0:
            code = str(v["variation_code"])
            plans.append({"code": code, "name": str(v.get("name") or code), "vtpass_price": price.quantize(TWO_PLACES)})
    if not plans:
        raise PlanPricingUnavailable()
    return plans


def _sell_price(vtpass_price: Decimal) -> Decimal:
    """What QuickSurf charges for a plan: VTpass's price plus DATA_PRICE_MARKUP_PERCENT."""
    markup = Decimal(str(settings.DATA_PRICE_MARKUP_PERCENT))
    return (vtpass_price * (1 + markup / 100)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _data_plan_price(network: str, variation_code: str) -> Decimal | None:
    """
    QuickSurf's price for a data plan, from VTpass's live plan list. Not cached: VTpass bills by
    variation_code, so this, not the client's amount, is what the wallet must be charged.
    Returns None when the plan doesn't exist for the network.
    """
    for plan in _vtpass_plans(network):
        if plan["code"] == variation_code:
            return _sell_price(plan["vtpass_price"])
    return None


# ---------- Limits & references ----------
def _daily_limit(user) -> int:
    """The user's daily purchase limit in naira (0 = none). Accounts under a day old get NEW_ACCOUNT_DAILY_LIMIT."""
    limits = [settings.DAILY_PURCHASE_LIMIT]
    if user.date_joined > timezone.now() - timedelta(days=1):
        limits.append(settings.NEW_ACCOUNT_DAILY_LIMIT)
    limits = [limit for limit in limits if limit]
    return min(limits) if limits else 0


def _daily_limit_exceeded(user, amount: Decimal) -> int:
    """The limit this purchase would take the user's purchases today (Lagos time) past, or 0 if it doesn't."""
    limit = _daily_limit(user)
    if not limit:
        return 0
    start_of_day = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    spent = Decimal("0")
    for model in (AirtimeTransaction, DataTransaction):
        total = model.objects.filter(
            user=user, status__in=["pending", "successful"], timestamp__gte=start_of_day
        ).aggregate(total=Sum("amount"))["total"]
        spent += total or 0
    return limit if spent + amount > limit else 0


def _daily_limit_response(limit: int):
    return Response(
        {"error": f"Daily purchase limit of ₦{limit:,} reached. Try again tomorrow."},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _pin_error_response(user, pin):
    """None if the transaction PIN is right (or not required); otherwise the 403 to return."""
    if not settings.TRANSACTION_PIN_REQUIRED:
        return None
    try:
        check_pin(user, str(pin or ""))
    except PinError as e:
        return Response({"error": e.message, "code": e.code, **e.extra}, status=status.HTTP_403_FORBIDDEN)
    return None


PURCHASE_THROTTLES = [SafeUserRateThrottle, SafeAnonRateThrottle, PurchaseRateThrottle]
PURCHASE_ERROR = "Something went wrong with this purchase. If money was held, it will be settled automatically."


def _reference_taken_response():
    return Response({"error": "This client_reference is already in use."}, status=status.HTTP_409_CONFLICT)


# ---------- Requery ----------
REQUERY_INTERVAL_SEC = 30


def _requery_allowed(txn) -> bool:
    """At most one VTpass requery per purchase every REQUERY_INTERVAL_SEC (if the cache is down, allow it)."""
    try:
        return cache.add(f"requery:{purchase_kind(txn)}:{txn.provider_request_id or txn.pk}", "1", REQUERY_INTERVAL_SEC)
    except Exception:
        return True


def _refresh_from_vtpass(txn):
    """Ask VTpass for a purchase's current state (requery) and settle it. Returns the updated purchase."""
    request_id = txn.provider_request_id or txn.client_reference
    wrap = _to_plain_json(requery_status(request_id))
    provider = _unwrap_provider(wrap)
    _log_provider(txn.user, "vtpass", txn.client_reference, {"action": "requery", "request_id": request_id}, wrap)
    return settle_purchase(
        txn,
        strict_map_outcome(provider),
        provider_status=str(provider.get("response_description", ""))[:64],
        provider_reference=transaction_id(provider) or txn.provider_reference,
    )


# ---------- Kill switch ----------
def _purchases_paused_response():
    return Response(
        {"error": "Purchases are temporarily unavailable. Please try again later."},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


DetailSchema = inline_serializer(
    name="ServiceDetailResponse",
    fields={"detail": drf_serializers.CharField()},
)


# ===================== Airtime =====================
@extend_schema(
    description="Purchase airtime via VTpass (sandbox/live) with strict single-flight + idempotency.",
    request=AirtimePurchaseRequestSerializer,
    responses={201: AirtimeTransactionSerializer, 200: AirtimeTransactionSerializer},
)
class AirtimePurchaseView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = PURCHASE_THROTTLES

    def get(self, request):
        qs = apply_txn_filters(AirtimeTransaction.objects.filter(user=request.user), request)
        paginator = SafePaginator()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(AirtimeTransactionSerializer(page, many=True).data)

    def post(self, request):
        if not settings.PURCHASES_ENABLED:
            return _purchases_paused_response()

        user = request.user
        payload = _to_plain_dict(request.data)

        serializer = AirtimePurchaseRequestSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        pin_error = _pin_error_response(user, payload.get("pin"))
        if pin_error:
            return pin_error

        amt = Decimal(str(serializer.validated_data["amount"]))
        network = serializer.validated_data["network"]
        phone = serializer.validated_data["phone"]
        client_ref = serializer.validated_data.get("client_reference") or f"AIRTIME-{uuid.uuid4().hex}"

        # Locks: (1) by client_ref, (2) by user+line for cooldown window
        k_ref = _lock_key_ref(user.id, "airtime", client_ref)
        k_line = _lock_key_line(user.id, "airtime", network, phone)
        if not _acquire_purchase_locks(k_ref, k_line):
            return Response(
                {"detail": "Another purchase is in progress for this reference/line."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            # True idempotency: if this reference exists, return it
            existing = AirtimeTransaction.objects.filter(user=user, client_reference=client_ref).first()
            if existing:
                return Response(AirtimeTransactionSerializer(existing).data, status=status.HTTP_200_OK)
            if AirtimeTransaction.objects.filter(client_reference=client_ref).exists():
                return _reference_taken_response()

            # Also avoid parallel different refs on the same line by returning the most recent pending
            cooloff_after = timezone.now() - timedelta(seconds=COOLDOWN_SEC)
            line_pending = (
                AirtimeTransaction.objects.filter(
                    user=user, phone=phone, network=network, status="pending", timestamp__gte=cooloff_after
                )
                .order_by("-timestamp")
                .first()
            )
            if line_pending:
                return Response(AirtimeTransactionSerializer(line_pending).data, status=status.HTTP_200_OK)

            # Hold funds + create pending txn (race-safe)
            with transaction.atomic():
                Wallet.objects.select_for_update().get(user=user)  # one purchase at a time per wallet

                # re-check idempotency inside the lock
                again = AirtimeTransaction.objects.filter(user=user, client_reference=client_ref).first()
                if again:
                    return Response(AirtimeTransactionSerializer(again).data, status=status.HTTP_200_OK)

                exceeded_limit = _daily_limit_exceeded(user, amt)
                if exceeded_limit:
                    return _daily_limit_response(exceeded_limit)
                try:
                    wallet_services.hold(user, amt, ledger_reference("airtime", client_ref))
                except wallet_services.InsufficientFunds:
                    return Response({"error": "Insufficient funds."}, status=status.HTTP_400_BAD_REQUEST)

                txn = AirtimeTransaction.objects.create(
                    user=user,
                    amount=amt,
                    network=network,
                    phone=phone,
                    status="pending",  # use only defined choices
                    client_reference=client_ref,
                    provider_request_id=generate_request_id(),  # the ID VTpass knows it by (requery, callbacks)
                )

            # Provider call (once)
            raw_resp = purchase_airtime(network, phone, float(amt), request_id=txn.provider_request_id)
            http_status = _provider_http_status(raw_resp)
            wrap = _to_plain_json(raw_resp)
            provider = _unwrap_provider(wrap)
            _log_provider(user, "airtime", client_ref, payload, wrap, http_status=http_status)

            # Settle: capture on success, release (refund) on failure, keep held while unknown
            txn = settle_purchase(
                txn,
                strict_map_outcome(provider),
                provider_status=str(provider.get("response_description", ""))[:64],
                provider_reference=transaction_id(provider),
                raw_response=provider,
            )

            return Response(AirtimeTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Purchase %s failed unexpectedly", client_ref)
            # If something blew up before finalize, keep it pending (funds reserved),
            # the requery job or manual requery can reconcile.
            try:
                with transaction.atomic():
                    txn = AirtimeTransaction.objects.filter(user=user, client_reference=client_ref).first()
                    if txn and txn.status == "pending":
                        if hasattr(txn, "provider_status"):
                            txn.provider_status = "ERROR: will be rechecked with VTpass"
                        txn.save(update_fields=["provider_status", "updated"])
            except Exception:
                pass
            return Response(
                {"error": PURCHASE_ERROR, "client_reference": client_ref, "request_id": current_request_id()},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        finally:
            _release_purchase_locks(k_ref, k_line)


# ===================== Data =====================
@extend_schema(
    description="Purchase data via VTpass (sandbox/live) with strict single-flight + idempotency.",
    request=DataPurchaseRequestSerializer,
    responses={201: DataTransactionSerializer, 200: DataTransactionSerializer},
)
class DataPurchaseView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = PURCHASE_THROTTLES

    def get(self, request):
        qs = apply_txn_filters(DataTransaction.objects.filter(user=request.user), request)
        paginator = SafePaginator()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(DataTransactionSerializer(page, many=True).data)

    def post(self, request):
        if not settings.PURCHASES_ENABLED:
            return _purchases_paused_response()

        user = request.user
        payload = _to_plain_dict(request.data)

        serializer = DataPurchaseRequestSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        pin_error = _pin_error_response(user, payload.get("pin"))
        if pin_error:
            return pin_error

        requested_amt = serializer.validated_data.get("amount")
        network = serializer.validated_data["network"]
        phone = serializer.validated_data["phone"]
        plan = serializer.validated_data["plan"]
        client_ref = serializer.validated_data.get("client_reference") or f"DATA-{uuid.uuid4().hex}"

        k_ref = _lock_key_ref(user.id, "data", client_ref)
        k_line = _lock_key_line(user.id, "data", network, phone)
        if not _acquire_purchase_locks(k_ref, k_line):
            return Response(
                {"detail": "Another purchase is in progress for this reference/line."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            existing = DataTransaction.objects.filter(user=user, client_reference=client_ref).first()
            if existing:
                return Response(DataTransactionSerializer(existing).data, status=status.HTTP_200_OK)
            if DataTransaction.objects.filter(client_reference=client_ref).exists():
                return _reference_taken_response()

            cooloff_after = timezone.now() - timedelta(seconds=COOLDOWN_SEC)
            line_pending = (
                DataTransaction.objects.filter(
                    user=user, phone=phone, network=network, status="pending", timestamp__gte=cooloff_after
                )
                .order_by("-timestamp")
                .first()
            )
            if line_pending:
                return Response(DataTransactionSerializer(line_pending).data, status=status.HTTP_200_OK)

            # Price comes from VTpass, never from the client; a client amount only confirms the price shown.
            try:
                amt = _data_plan_price(network, plan)
            except PlanPricingUnavailable:
                return Response(
                    {"error": "Unable to price this data plan right now. Please try again."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            if amt is None:
                return Response(
                    {"error": f"Unknown data plan '{plan}' for {network}."}, status=status.HTTP_400_BAD_REQUEST
                )
            if requested_amt is not None and Decimal(str(requested_amt)) != amt:
                return Response(
                    {"error": "Amount does not match the current price for this plan.", "price": str(amt)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            with transaction.atomic():
                Wallet.objects.select_for_update().get(user=user)  # one purchase at a time per wallet

                again = DataTransaction.objects.filter(user=user, client_reference=client_ref).first()
                if again:
                    return Response(DataTransactionSerializer(again).data, status=status.HTTP_200_OK)

                exceeded_limit = _daily_limit_exceeded(user, amt)
                if exceeded_limit:
                    return _daily_limit_response(exceeded_limit)
                try:
                    wallet_services.hold(user, amt, ledger_reference("data", client_ref))
                except wallet_services.InsufficientFunds:
                    return Response({"error": "Insufficient funds."}, status=status.HTTP_400_BAD_REQUEST)

                txn = DataTransaction.objects.create(
                    user=user,
                    amount=amt,
                    network=network,
                    phone=phone,
                    plan=plan,
                    status="pending",
                    client_reference=client_ref,
                    provider_request_id=generate_request_id(),  # the ID VTpass knows it by (requery, callbacks)
                )

            raw_resp = purchase_data(network, phone, plan, request_id=txn.provider_request_id)
            http_status = _provider_http_status(raw_resp)
            wrap = _to_plain_json(raw_resp)
            provider = _unwrap_provider(wrap)
            _log_provider(user, "data", client_ref, payload, wrap, http_status=http_status)

            txn = settle_purchase(
                txn,
                strict_map_outcome(provider),
                provider_status=str(provider.get("response_description", ""))[:64],
                provider_reference=transaction_id(provider),
                raw_response=provider,
            )

            return Response(DataTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Purchase %s failed unexpectedly", client_ref)
            try:
                with transaction.atomic():
                    txn = DataTransaction.objects.filter(user=user, client_reference=client_ref).first()
                    if txn and txn.status == "pending":
                        if hasattr(txn, "provider_status"):
                            txn.provider_status = "ERROR: will be rechecked with VTpass"
                        txn.save(update_fields=["provider_status", "updated"])
            except Exception:
                pass
            return Response(
                {"error": PURCHASE_ERROR, "client_reference": client_ref, "request_id": current_request_id()},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        finally:
            _release_purchase_locks(k_ref, k_line)


# ===================== Status Requery =====================
@extend_schema(
    description="Re-query provider status and reconcile the transaction.",
    responses={200: OpenApiTypes.OBJECT, 404: DetailSchema, 501: DetailSchema},
)
class PurchaseStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, client_reference: str):
        if requery_status is None:
            return Response({"detail": "Requery not available."}, status=501)

        txn = (
            AirtimeTransaction.objects.filter(user=request.user, client_reference=client_reference).first()
            or DataTransaction.objects.filter(user=request.user, client_reference=client_reference).first()
        )
        if not txn:
            return Response({"detail": "Transaction not found."}, status=404)

        serializer_cls = (
            AirtimeTransactionSerializer if isinstance(txn, AirtimeTransaction) else DataTransactionSerializer
        )
        if txn.status in FINAL_STATUSES or not _requery_allowed(txn):
            return Response(serializer_cls(txn).data)

        txn = _refresh_from_vtpass(txn)
        return Response(serializer_cls(txn).data)


# ===================== Service Variations =====================
@extend_schema(
    description="Get provider variations/plans for a given serviceID.",
    responses={200: OpenApiTypes.OBJECT, 400: OpenApiTypes.OBJECT, 501: DetailSchema},
)
class ServiceVariationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, service_id: str):
        if get_service_variations is None:
            return Response({"detail": "Variations not available."}, status=501)

        res = get_service_variations(service_id) or {}
        ok = bool(res.get("ok")) or str(res.get("code", "")) == "000"
        return Response(res, status=200 if ok else 400)


# ===================== Data plans (with QuickSurf's prices) =====================
PLANS_CACHE_SEC = 300


@extend_schema(
    description="Data plans for a network (mtn, glo, airtel, 9mobile) at QuickSurf's selling price. Cached for 5 minutes.",
    responses={200: OpenApiTypes.OBJECT, 404: DetailSchema, 503: DetailSchema},
)
class DataPlansView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, network: str):
        network = network.lower()
        if network not in DATA_SERVICE_IDS:
            return Response({"detail": f"Unknown network '{network}'."}, status=status.HTTP_404_NOT_FOUND)

        cache_key = f"data-plans:{network}"
        try:
            plans = cache.get(cache_key)
        except Exception:
            plans = None
        if plans is None:
            try:
                plans = [
                    {"code": plan["code"], "name": plan["name"], "price": str(_sell_price(plan["vtpass_price"]))}
                    for plan in _vtpass_plans(network)
                ]
            except PlanPricingUnavailable:
                return Response(
                    {"detail": "Data plans are unavailable right now. Please try again."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            try:
                cache.set(cache_key, plans, PLANS_CACHE_SEC)
            except Exception:
                pass
        return Response({"network": network, "plans": plans})


# ===================== VTpass callback =====================
def _purchase_by_request_id(request_id: str):
    return (
        AirtimeTransaction.objects.filter(provider_request_id=request_id).first()
        or DataTransaction.objects.filter(provider_request_id=request_id).first()
    )


@method_decorator(csrf_exempt, name="dispatch")
class VTPassCallbackView(APIView):
    """
    VTpass transaction-update webhook (set its URL in the VTpass dashboard). VTpass doesn't sign
    these, so the payload is only a hint: the purchase is found by its request ID and VTpass is
    asked directly (requery) before any money moves. Always acknowledged, because VTpass retries
    until it gets {"response": "success"}.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    @extend_schema(request=OpenApiTypes.OBJECT, responses={200: OpenApiTypes.OBJECT})
    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        request_id = str(data.get("requestId") or "")
        if body.get("type") == "transaction-update" and request_id:
            txn = _purchase_by_request_id(request_id)
            if txn is not None:
                try:
                    _refresh_from_vtpass(txn)
                except Exception:
                    logger.exception("VTpass callback: requery failed for request %s", request_id)
        return Response({"response": "success"})


# ===================== Receipts =====================
def _user_purchase(user, client_reference):
    return (
        AirtimeTransaction.objects.filter(user=user, client_reference=client_reference).first()
        or DataTransaction.objects.filter(user=user, client_reference=client_reference).first()
    )


@extend_schema(
    description="A receipt for one of the user's purchases.", responses={200: OpenApiTypes.OBJECT, 404: DetailSchema}
)
class ReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, client_reference: str):
        txn = _user_purchase(request.user, client_reference)
        if txn is None:
            return Response({"detail": "Purchase not found."}, status=status.HTTP_404_NOT_FOUND)
        is_data = isinstance(txn, DataTransaction)
        return Response(
            {
                "reference": txn.client_reference,
                "date": txn.timestamp,
                "item": f"Data {txn.plan}" if is_data else "Airtime",
                "network": txn.network,
                "phone": txn.phone,
                "amount": str(txn.amount),
                "status": txn.status,
                "vtpass_request_id": txn.provider_request_id,
                "vtpass_transaction_id": txn.provider_reference,
            }
        )


# ===================== Saved numbers & network detection =====================
@extend_schema(description="The user's saved numbers (GET) or save one (POST {name, phone, network}).")
class BeneficiaryListView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = BeneficiarySerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # building the API docs: there's no user
            return Beneficiary.objects.none()
        return Beneficiary.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


@extend_schema(description="Remove a saved number.")
class BeneficiaryDetailView(generics.DestroyAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = BeneficiarySerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Beneficiary.objects.none()
        return Beneficiary.objects.filter(user=self.request.user)


@extend_schema(
    description="Guess a number's network from its prefix. Numbers can be ported, so let the user change it.",
    parameters=[OpenApiParameter(name="phone", required=True, type=str)],
    responses={200: OpenApiTypes.OBJECT},
)
class DetectNetworkView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        phone = normalize_ng_phone(request.query_params.get("phone", ""))
        if not phone:
            return Response(
                {"phone": ["Enter a Nigerian number like 0703xxxxxxx or +234703xxxxxxx."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"phone": phone, "network": detect_network(phone)})
