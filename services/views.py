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
    """
    Convert DRF QueryDict / dict-like to a plain dict of scalars.
    If it's already a dict, return as-is.
    If it's a string, try json.loads; else return {}.
    """
    if isinstance(maybe_mapping, dict):
        return maybe_mapping
    # DRF QueryDict has .items()
    if hasattr(maybe_mapping, "items"):
        out = {}
        for k, v in maybe_mapping.items():
            # QueryDict can yield lists; pick first scalar
            if isinstance(v, (list, tuple)):
                out[k] = v[0] if v else None
            else:
                out[k] = v
        return out
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
    """Ensure provider response is a dict; if string, try json.loads; else wrap."""
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
    qs = qs.order_by(ordering) if ordering in allowed else qs.order_by("-timestamp")
    return qs


def validate_request_fields(data, required_fields):
    for field in required_fields:
        if field not in data or data[field] in ("", None):
            return f"{field} is required."
    return None


# ---------- views ----------
@extend_schema(
    description="Purchase airtime (VTpass behind the scenes; MOCK/LIVE via PROVIDER_MODE).",
    request=AirtimePurchaseRequestSchema,
    responses={
        201: AirtimeTransactionSerializer,
        200: AirtimeTransactionSerializer,  # idempotent repeat
        400: OpenApiResponse(description="Bad request"),
        500: OpenApiResponse(description="Server error"),
    },
    parameters=[
        OpenApiParameter(name="status", description="successful|failed|pending", required=False, type=str),
        OpenApiParameter(name="network", description="mtn|glo|airtel|9mobile", required=False, type=str),
        OpenApiParameter(name="search", description="phone or client_reference", required=False, type=str),
        OpenApiParameter(name="date_from", description="YYYY-MM-DD >=", required=False, type=str),
        OpenApiParameter(name="date_to", description="YYYY-MM-DD <=", required=False, type=str),
        OpenApiParameter(name="ordering", description="timestamp|-timestamp|amount|-amount", required=False, type=str),
        OpenApiParameter(name="page", required=False, type=int),
        OpenApiParameter(name="page_size", required=False, type=int),
    ],
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

        # Idempotency: return existing if seen before
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

                # Provider call (pass idempotency as request_id)
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

                if txn.status != "successful":
                    wallet.balance += amount
                    wallet.save()

                return Response(AirtimeTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    description="Purchase data (VTpass behind the scenes; MOCK/LIVE via PROVIDER_MODE).",
    request=DataPurchaseRequestSchema,
    responses={
        201: DataTransactionSerializer,
        200: DataTransactionSerializer,  # idempotent repeat
        400: OpenApiResponse(description="Bad request"),
        500: OpenApiResponse(description="Server error"),
    },
    parameters=[
        OpenApiParameter(name="status", description="successful|failed|pending", required=False, type=str),
        OpenApiParameter(name="network", description="mtn|glo|airtel|9mobile", required=False, type=str),
        OpenApiParameter(name="search", description="phone or client_reference", required=False, type=str),
        OpenApiParameter(name="date_from", description="YYYY-MM-DD >=", required=False, type=str),
        OpenApiParameter(name="date_to", description="YYYY-MM-DD <=", required=False, type=str),
        OpenApiParameter(name="ordering", description="timestamp|-timestamp|amount|-amount", required=False, type=str),
        OpenApiParameter(name="page", required=False, type=int),
        OpenApiParameter(name="page_size", required=False, type=int),
    ],
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

        # Idempotency
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

                wallet.balance -= amount
                wallet.save()

                txn = DataTransaction.objects.create(
                    user=user,
                    amount=amount,
                    network=str(data["network"]).lower(),
                    phone=data["phone"],
                    plan=data["plan"],
                    status="pending",
                    client_reference=client_ref,
                )

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

                if txn.status != "successful":
                    wallet.balance += amount
                    wallet.save()

                return Response(DataTransactionSerializer(txn).data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
