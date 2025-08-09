from decimal import Decimal

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
# ✅ Use ONLY the real VTpass client
from .vtpass import purchase_airtime, purchase_data


# ---------- helpers ----------
class SafePaginator(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def apply_txn_filters(qs, request):
    """
    Filters: ?status=...&network=...&search=...&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
    Ordering: ?ordering=timestamp|-timestamp|amount|-amount
    """
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
    if ordering in allowed:
        qs = qs.order_by(ordering)
    else:
        qs = qs.order_by("-timestamp")

    return qs


def validate_request_fields(data, required_fields):
    for field in required_fields:
        if field not in data:
            return f"{field} is required."
    return None


# ---------- views ----------
@extend_schema(
    description="Purchase airtime (VTpass behind the scenes; MOCK/LIVE via PROVIDER_MODE).",
    request=AirtimePurchaseRequestSchema,
    responses={
        201: AirtimeTransactionSerializer,
        200: AirtimeTransactionSerializer,  # idempotency hit returns existing
        400: OpenApiResponse(description="Bad request"),
        500: OpenApiResponse(description="Server error"),
    },
    parameters=[
        OpenApiParameter(name="status", description="Filter by status (successful|failed|pending)", required=False, type=str),
        OpenApiParameter(name="network", description="Filter by network (mtn|glo|airtel|9mobile)", required=False, type=str),
        OpenApiParameter(name="search", description="Search phone or client_reference", required=False, type=str),
        OpenApiParameter(name="date_from", description=">= YYYY-MM-DD", required=False, type=str),
        OpenApiParameter(name="date_to", description="<= YYYY-MM-DD", required=False, type=str),
        OpenApiParameter(name="ordering", description="timestamp|-timestamp|amount|-amount", required=False, type=str),
        OpenApiParameter(name="page", required=False, type=int),
        OpenApiParameter(name="page_size", required=False, type=int),
    ],
)
class AirtimePurchaseView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = AirtimeTransaction.objects.filter(user=request.user)
        qs = apply_txn_filters(qs, request)

        paginator = SafePaginator()
        page = paginator.paginate_queryset(qs, request)
        serializer = AirtimeTransactionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        user = request.user
        data = request.data

        error = validate_request_fields(data, ["amount", "network", "phone", "client_reference"])
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        client_ref = data["client_reference"]
        existing = AirtimeTransaction.objects.filter(client_reference=client_ref).first()
        if existing:
            serializer = AirtimeTransactionSerializer(existing)
            return Response(serializer.data, status=status.HTTP_200_OK)

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
                    network=data["network"],
                    phone=data["phone"],
                    status="pending",
                    client_reference=client_ref,
                )

                # ✅ Pass our client_reference as VTpass request_id (idempotency on provider)
                vtpass_response = purchase_airtime(
                    data["network"], data["phone"], amount, request_id=client_ref
                )

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

                serializer = AirtimeTransactionSerializer(txn)
                return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    description="Purchase data (VTpass behind the scenes; MOCK/LIVE via PROVIDER_MODE).",
    request=DataPurchaseRequestSchema,
    responses={
        201: DataTransactionSerializer,
        200: DataTransactionSerializer,  # idempotency hit returns existing
        400: OpenApiResponse(description="Bad request"),
        500: OpenApiResponse(description="Server error"),
    },
    parameters=[
        OpenApiParameter(name="status", description="Filter by status (successful|failed|pending)", required=False, type=str),
        OpenApiParameter(name="network", description="Filter by network (mtn|glo|airtel|9mobile)", required=False, type=str),
        OpenApiParameter(name="search", description="Search phone or client_reference", required=False, type=str),
        OpenApiParameter(name="date_from", description=">= YYYY-MM-DD", required=False, type=str),
        OpenApiParameter(name="date_to", description="<= YYYY-MM-DD", required=False, type=str),
        OpenApiParameter(name="ordering", description="timestamp|-timestamp|amount|-amount", required=False, type=str),
        OpenApiParameter(name="page", required=False, type=int),
        OpenApiParameter(name="page_size", required=False, type=int),
    ],
)
class DataPurchaseView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = DataTransaction.objects.filter(user=request.user)
        qs = apply_txn_filters(qs, request)

        paginator = SafePaginator()
        page = paginator.paginate_queryset(qs, request)
        serializer = DataTransactionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        user = request.user
        data = request.data

        error = validate_request_fields(data, ["amount", "network", "phone", "plan", "client_reference"])
        if error:
            return Response({"error": error}, status=status.HTTP_400_BAD_REQUEST)

        client_ref = data["client_reference"]
        existing = DataTransaction.objects.filter(client_reference=client_ref).first()
        if existing:
            serializer = DataTransactionSerializer(existing)
            return Response(serializer.data, status=status.HTTP_200_OK)

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
                    network=data["network"],
                    phone=data["phone"],
                    plan=data["plan"],
                    status="pending",
                    client_reference=client_ref,
                )

                # ✅ Pass client_reference through to VTpass
                vtpass_response = purchase_data(
                    data["network"], data["phone"], data["plan"], request_id=client_ref
                )

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

                serializer = DataTransactionSerializer(txn)
                return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Wallet.DoesNotExist:
            return Response({"error": "Wallet not found."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
