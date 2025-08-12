# services/views.py
from decimal import Decimal
import json
from typing import Any, Dict

from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_date

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination

from drf_spectacular.utils import extend_schema, OpenApiResponse

from wallets.models import Wallet
from .models import AirtimeTransaction, DataTransaction, ProviderLog
from .serializers import (
    AirtimeTransactionSerializer,
    DataTransactionSerializer,
    # If you adopted my new serializer names, import them directly:
    AirtimePurchaseRequestSerializer,
    DataPurchaseRequestSerializer,
)
# If your project still uses the old names in other places, these aliases help:
AirtimePurchaseRequestSchema = AirtimePurchaseRequestSerializer
DataPurchaseRequestSchema = DataPurchaseRequestSerializer

# vtpass helper — supports both old (raw json) and new (stateful) versions
from .vtpass import purchase_airtime, purchase_data
try:
    from .vtpass import requery_status, get_service_variations  # new helpers
except Exception:
    requery_status = None
    get_service_variations = None


# ---------- small utils ----------
class SafePaginator(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def _to_plain_dict(maybe_mapping: Any) -> Dict:
    """Convert DRF QueryDict or string to plain dict."""
    if isinstance(maybe_mapping, dict):
        return maybe_mapping
    if hasattr(maybe_mapping, "items"):
        return {k: (v[0] if isinstance(v, (list, tuple)) else v) for k, v in maybe_mapping.items()}
    if isinstance(maybe_mapping, (bytes, bytearray)):
        try:
            return json.loads(maybe_mapping.decode("utf-8"))
        except Exception:
            return {}
    if isinstance(maybe_mapping, str):
        try:
            return json.loads(maybe_mapping)
        except Exception:
            return {}
    return {}


def _to_plain_json(maybe_json: Any) -> Dict:
    """Ensure provider response is always dict."""
    if isinstance(maybe_json, dict):
        return maybe_json
    if isinstance(maybe_json, (bytes, bytearray)):
        try:
            return json.loads(maybe_json.decode("utf-8"))
        except Exception:
            return {"raw": maybe_json.decode("utf-8", errors="ignore")}
    if isinstance(maybe_json, str):
        try:
            return json.loads(maybe_json)
        except Exception:
            return {"raw": maybe_json}
    return {"raw": str(maybe_json)}


def apply_txn_filters(qs, request):
    """Filter transactions by query params."""
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


def _map_provider_state(provider_body: Dict) -> str:
    """
    Normalize VTpass response:
    - If using my new vtpass.py → provider returns "state" directly.
    - Else fallback to "code" mapping.
    """
    # New helper shape
    state = str(provider_body.get("state", "")).lower()
    if state in {"success", "successful"}:
        return "successful"
    if state == "pending":
        return "pending"
    if state == "failed":
        return "failed"

    # Old VTpass raw shape
    code = str(provider_body.get("code", "")).strip()
    if code == "000":
        return "successful"
    if code in {"099", "016"}:
        return "pending"
    return "failed"


def _provider_status_code(provider_body: Dict) -> str:
    # prefer VTpass "code"; fallback to embedded http_status if present
    code = provider_body.get("code")
    if code is not None:
        return str(code)
    return str(provider_body.get("http_status", "unknown"))


def _log_provider(user, service_type: str, client_reference: str, request_payload: Dict, response_payload: Dict):
    """
    Centralized ProviderLog write. Keeps your current model fields.
    (If you later add more fields to ProviderLog, update only here.)
    """
    try:
        ProviderLog.objects.create(
            user=user,
            service_type=service_type,             # 'airtime' | 'data' | 'vtpass'
            client_reference=client_reference,
            request_payload=request_payload,
            response_payload=response_payload,
            status_code=_provider_status_code(response_payload),
        )
    except Exception:
        # Logging must never break the purchase flow
        pass


# ---------- Airtime ----------
@extend_schema(
    description="Purchase airtime via VTpass (sandbox/live based on PROVIDER_MODE).",
    request=AirtimePurchaseRequestSchema,
    responses={
        201: AirtimeTransactionSerializer,
        200: AirtimeTransactionSerializer,
        400: OpenApiResponse(description="Bad request"),
        500: OpenApiResponse(description="Server error"),
    },
)
class AirtimePurchaseView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = apply_txn_filters(AirtimeTransaction.objects.filter(user=request.user), request)
        paginator = SafePaginator()
        page = paginator.paginate_queryset(qs, request)
        serializer = AirtimeTransactionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        user = request.user
        payload = _to_plain_dict(request.data)

        # Validate input
        serializer = AirtimePurchaseRequestSchema(data=payload)
        serializer.is_valid(raise_exception=True)
        amt = Decimal(str(serializer.validated_data["amount"]))
        network = serializer.validated_data["network"]
        phone = serializer.validated_data["phone"]
        client_ref = serializer.validated_data.get("client_reference") or f"AIRTIME_{user.id}_{AirtimeTransaction.objects.count()+1}"

        # Idempotency: return existing txn if same client_reference already used
        existing = AirtimeTransaction.objects.filter(client_reference=client_ref).first()
        if existing:
            return Response(AirtimeTransactionSerializer(existing).data, status=status.HTTP_200_OK)

        try:
            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(user=user)
                if wallet.balance < amt:
                    return Response({"error": "Insufficient funds."}, status=status.HTTP_400_BAD_REQUEST)

                # Lock funds by deducting; refund on fail
                wallet.balance -= amt
                wallet.save()

                txn = AirtimeTransaction.objects.create(
                    user=user,
                    amount=amt,
                    network=network,
                    phone=phone,
                    status="pending",
                    client_reference=client_ref,
                )

                # Call VTpass
                raw = purchase_airtime(network, phone, amt, request_id=client_ref)
                provider = _to_plain_json(raw)
                _log_provider(user, "airtime", client_ref, payload, provider)

                # Map state and update txn
                new_status = _map_provider_state(provider)
                txn.status = new_status
                # optional provider metadata
                txn.provider_status = str(provider.get("response_description", ""))[:64]
                txn.save()

                # If failed → refund immediately
                if new_status == "failed":
                    wallet.balance += amt
                    wallet.save()

                return Response(AirtimeTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- Data ----------
@extend_schema(
    description="Purchase data via VTpass (sandbox/live based on PROVIDER_MODE).",
    request=DataPurchaseRequestSchema,
    responses={
        201: DataTransactionSerializer,
        200: DataTransactionSerializer,
        400: OpenApiResponse(description="Bad request"),
        500: OpenApiResponse(description="Server error"),
    },
)
class DataPurchaseView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = apply_txn_filters(DataTransaction.objects.filter(user=request.user), request)
        paginator = SafePaginator()
        page = paginator.paginate_queryset(qs, request)
        serializer = DataTransactionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        user = request.user
        payload = _to_plain_dict(request.data)

        # Validate input
        serializer = DataPurchaseRequestSchema(data=payload)
        serializer.is_valid(raise_exception=True)
        amt = Decimal(str(serializer.validated_data["amount"]))
        network = serializer.validated_data["network"]
        phone = serializer.validated_data["phone"]
        plan = serializer.validated_data["plan"]  # VTpass variation_code
        client_ref = serializer.validated_data.get("client_reference") or f"DATA_{user.id}_{DataTransaction.objects.count()+1}"

        # Idempotency
        existing = DataTransaction.objects.filter(client_reference=client_ref).first()
        if existing:
            return Response(DataTransactionSerializer(existing).data, status=status.HTTP_200_OK)

        try:
            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(user=user)
                if wallet.balance < amt:
                    return Response({"error": "Insufficient funds."}, status=status.HTTP_400_BAD_REQUEST)

                # Lock funds
                wallet.balance -= amt
                wallet.save()

                txn = DataTransaction.objects.create(
                    user=user,
                    amount=amt,
                    network=network,
                    phone=phone,
                    plan=plan,
                    status="pending",
                    client_reference=client_ref,
                )

                # Call VTpass
                raw = purchase_data(network, phone, plan, request_id=client_ref)
                provider = _to_plain_json(raw)
                _log_provider(user, "data", client_ref, payload, provider)

                # Map state and update txn
                new_status = _map_provider_state(provider)
                txn.status = new_status
                txn.provider_status = str(provider.get("response_description", ""))[:64]
                txn.save()

                # If failed → refund
                if new_status == "failed":
                    wallet.balance += amt
                    wallet.save()

                return Response(DataTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- Status Requery ----------
@extend_schema(
    description="Re-query provider status for a given client_reference and reconcile the transaction.",
    responses={
        200: OpenApiResponse(description="Status checked / reconciled"),
        404: OpenApiResponse(description="Transaction not found"),
        501: OpenApiResponse(description="Requery not available"),
    },
)
class PurchaseStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, client_reference: str):
        if requery_status is None:
            return Response({"detail": "Requery not available."}, status=501)

        # Find either airtime or data txn for this user
        airtime = AirtimeTransaction.objects.filter(user=request.user, client_reference=client_reference).first()
        data = None if airtime else DataTransaction.objects.filter(user=request.user, client_reference=client_reference).first()
        txn = airtime or data
        if not txn:
            return Response({"detail": "Transaction not found."}, status=404)

        provider = _to_plain_json(requery_status(client_reference))
        _log_provider(request.user, "vtpass", client_reference, {"action": "requery"}, provider)

        new_status = _map_provider_state(provider)
        if new_status != txn.status:
            # Update status + provider status text
            txn.status = new_status
            if hasattr(txn, "provider_status"):
                txn.provider_status = str(provider.get("response_description", ""))[:64]
            txn.save()

            # Handle refunds if it changed to failed and money was still locked
            if new_status == "failed":
                try:
                    with transaction.atomic():
                        wallet = Wallet.objects.select_for_update().get(user=request.user)
                        wallet.balance += txn.amount
                        wallet.save()
                except Wallet.DoesNotExist:
                    pass

        # Return fresh serialized txn
        if airtime:
            return Response(AirtimeTransactionSerializer(txn).data)
        return Response(DataTransactionSerializer(txn).data)


# ---------- Service Variations (e.g., data plans) ----------
@extend_schema(
    description="Get provider variations/plans (e.g., data bundles) for a given serviceID (e.g., 'mtn-data').",
    responses={
        200: OpenApiResponse(description="OK"),
        400: OpenApiResponse(description="Bad request"),
        501: OpenApiResponse(description="Variations not available"),
    },
)
class ServiceVariationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, service_id: str):
        if get_service_variations is None:
            return Response({"detail": "Variations not available."}, status=501)
        res = get_service_variations(service_id)
        return Response(res, status=200 if res.get("ok") else 400)

