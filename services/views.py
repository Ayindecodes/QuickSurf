from decimal import Decimal
import json

from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_date

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination

from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter

from wallets.models import Wallet
from .models import AirtimeTransaction, DataTransaction, ProviderLog
from .serializers import (
    AirtimeTransactionSerializer,
    DataTransactionSerializer,
    AirtimePurchaseRequestSchema,
    DataPurchaseRequestSchema,
)
from .vtpass import purchase_airtime, purchase_data


# ---------- small utils ----------
class SafePaginator(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def _to_plain_dict(maybe_mapping):
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


def _to_plain_json(maybe_json):
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


def validate_request_fields(data, required_fields):
    """Ensure all required fields exist."""
    for field in required_fields:
        if field not in data or data[field] in ("", None):
            return f"{field} is required."
    return None


# ---------- Airtime ----------
@extend_schema(
    description="Purchase airtime via VTpass (sandbox/live based on PROVIDER_MODE).",
    request=AirtimePurchaseRequestSchema,
    responses={
        201: AirtimeTransactionSerializer,
        200: AirtimeTransactionSerializer,
        400: OpenApiResponse(description="Bad request"),
        500: OpenApiResponse(description="Server error"),
    }
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
        data = _to_plain_dict(request.data)

        error = validate_request_fields(data, ["amount", "network", "phone", "client_reference"])
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        client_ref = data["client_reference"]

        # Idempotency check
        existing = AirtimeTransaction.objects.filter(client_reference=client_ref).first()
        if existing:
            return Response(AirtimeTransactionSerializer(existing).data, status=status.HTTP_200_OK)

        # Validate amount
        try:
            amount = Decimal(str(data["amount"]))
            if amount <= 0:
                raise ValueError
        except Exception:
            return Response({"error": "Invalid amount."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(user=user)
                if wallet.balance < amount:
                    return Response({"error": "Insufficient funds."}, status=status.HTTP_400_BAD_REQUEST)

                # Lock funds
                wallet.balance -= amount
                wallet.save()

                txn = AirtimeTransaction.objects.create(
                    user=user,
                    amount=amount,
                    network=str(data["network"]).lower(),
                    phone=data["phone"],
                    status="pending",
                    client_reference=client_ref,
                )

                # Call VTpass
                vt_raw = purchase_airtime(data["network"], data["phone"], amount, request_id=client_ref)
                vtpass_response = _to_plain_json(vt_raw)

                ProviderLog.objects.create(
                    user=user,
                    service_type="airtime",
                    client_reference=client_ref,
                    request_payload=data,
                    response_payload=vtpass_response,
                    status_code=str(vtpass_response.get("code", "unknown")),
                )

                txn.status = "successful" if vtpass_response.get("code") == "000" else "failed"
                txn.save()

                # Refund if failed
                if txn.status != "successful":
                    wallet.balance += amount
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
    }
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
        data = _to_plain_dict(request.data)

        error = validate_request_fields(data, ["amount", "network", "phone", "plan", "client_reference"])
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        client_ref = data["client_reference"]

        # Idempotency check
        existing = DataTransaction.objects.filter(client_reference=client_ref).first()
        if existing:
            return Response(DataTransactionSerializer(existing).data, status=status.HTTP_200_OK)

        # Validate amount
        try:
            amount = Decimal(str(data["amount"]))
            if amount <= 0:
                raise ValueError
        except Exception:
            return Response({"error": "Invalid amount."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(user=user)
                if wallet.balance < amount:
                    return Response({"error": "Insufficient funds."}, status=status.HTTP_400_BAD_REQUEST)

                # Lock funds
                wallet.balance -= amount
                wallet.save()

                txn = DataTransaction.objects.create(
                    user=user,
                    amount=amount,
                    network=str(data["network"]).lower(),
                    phone=data["phone"],
                    plan=data["plan"],  # plan = VTpass variation_code
                    status="pending",
                    client_reference=client_ref,
                )

                # Call VTpass with variation_code
                vt_raw = purchase_data(data["network"], data["phone"], data["plan"], request_id=client_ref)
                vtpass_response = _to_plain_json(vt_raw)

                ProviderLog.objects.create(
                    user=user,
                    service_type="data",
                    client_reference=client_ref,
                    request_payload=data,
                    response_payload=vtpass_response,
                    status_code=str(vtpass_response.get("code", "unknown")),
                )

                txn.status = "successful" if vtpass_response.get("code") == "000" else "failed"
                txn.save()

                # Refund if failed
                if txn.status != "successful":
                    wallet.balance += amount
                    wallet.save()

                return Response(DataTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
