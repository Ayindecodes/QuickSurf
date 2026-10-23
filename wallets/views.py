# wallets/views.py
import csv
from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from services.models import AirtimeTransaction, DataTransaction

from . import services as wallet_services
from .history import KINDS, describe, history
from .models import Wallet
from .serializers import (
    AdjustmentSerializer,
    TransactionSerializer,
    WalletBalanceSerializer,
)


# Simple paginator (page & page_size)
class SafePaginator(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


@extend_schema(
    description="Get the authenticated user's wallet balance.",
    request=None,
    responses={200: WalletBalanceSerializer, 404: OpenApiResponse(description="Wallet not found.")},
)
class WalletBalanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            wallet = Wallet.objects.get(user=request.user)
            serializer = WalletBalanceSerializer(wallet)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Wallet.DoesNotExist:
            return Response({"detail": "Wallet not found."}, status=status.HTTP_404_NOT_FOUND)


@extend_schema(
    description="Balance + last 10 airtime/data purchases for the authenticated user.",
    request=None,
    responses={
        200: OpenApiResponse(description="Summary payload"),
        404: OpenApiResponse(description="Wallet not found."),
    },
)
class WalletSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            wallet = Wallet.objects.get(user=request.user)
        except Wallet.DoesNotExist:
            return Response({"detail": "Wallet not found."}, status=status.HTTP_404_NOT_FOUND)

        airtime = AirtimeTransaction.objects.filter(user=request.user).order_by("-timestamp")[:10]
        data = DataTransaction.objects.filter(user=request.user).order_by("-timestamp")[:10]

        return Response(
            {
                "balance": str(wallet.balance),
                "locked": str(wallet.locked),
                "recent_airtime": [
                    {
                        "amount": str(t.amount),
                        "network": t.network,
                        "phone": t.phone,
                        "status": t.status,
                        "client_reference": t.client_reference,
                        "provider_request_id": t.provider_request_id,
                        "provider_status": t.provider_status,
                        "timestamp": t.timestamp,
                    }
                    for t in airtime
                ],
                "recent_data": [
                    {
                        "amount": str(t.amount),
                        "network": t.network,
                        "phone": t.phone,
                        "plan": t.plan,
                        "status": t.status,
                        "client_reference": t.client_reference,
                        "provider_request_id": t.provider_request_id,
                        "provider_status": t.provider_status,
                        "timestamp": t.timestamp,
                    }
                    for t in data
                ],
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    description="List the authenticated user's wallet ledger entries (most recent first).",
    request=None,
    parameters=[
        OpenApiParameter(name="page", required=False, type=int),
        OpenApiParameter(name="page_size", required=False, type=int),
    ],
    responses={200: TransactionSerializer(many=True), 404: OpenApiResponse(description="Wallet not found.")},
)
class TransactionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            wallet = Wallet.objects.get(user=request.user)
        except Wallet.DoesNotExist:
            return Response({"detail": "Wallet not found."}, status=status.HTTP_404_NOT_FOUND)

        qs = wallet.transactions.all().order_by("-timestamp", "-id")

        paginator = SafePaginator()
        page = paginator.paginate_queryset(qs, request)
        serializer = TransactionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class CanAdjustWallets(BasePermission):
    """Only staff given the wallets.adjust_wallet permission (the Finance role) can change balances."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_staff and user.has_perm("wallets.adjust_wallet"))


@extend_schema(
    description=(
        "Staff-only correction to a user's wallet (needs the wallets.adjust_wallet permission). A positive "
        "amount adds to the balance, a negative one takes from it. A note explaining why is required and "
        "is kept on the ledger entry."
    ),
    request=AdjustmentSerializer,
    responses={201: TransactionSerializer, 400: OpenApiResponse(description="Validation error")},
)
class AdjustView(APIView):
    permission_classes = [CanAdjustWallets]

    def post(self, request):
        serializer = AdjustmentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        try:
            entry = wallet_services.adjust(data["user"], data["amount"], data["note"], created_by=request.user)
        except wallet_services.InsufficientFunds:
            return Response({"amount": ["This would take the balance below zero."]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(TransactionSerializer(entry).data, status=status.HTTP_201_CREATED)


HISTORY_PARAMETERS = [
    OpenApiParameter(name="kind", required=False, type=str, enum=sorted(set(KINDS.values()))),
    OpenApiParameter(name="date_from", required=False, type=str, description="YYYY-MM-DD"),
    OpenApiParameter(name="date_to", required=False, type=str, description="YYYY-MM-DD"),
]


def _date_param(request, name):
    value = request.query_params.get(name)
    return parse_date(value) if value else None


@extend_schema(
    description="Everything that moved money in the wallet, newest first: top-ups, purchases (with their current "
    "status), refunds, rewards and corrections. Amounts are signed.",
    request=None,
    parameters=[
        *HISTORY_PARAMETERS,
        OpenApiParameter(name="page", required=False, type=int),
        OpenApiParameter(name="page_size", required=False, type=int),
    ],
    responses={200: OpenApiResponse(description="Paginated history lines")},
)
class HistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet = Wallet.objects.get(user=request.user)
        rows = history(
            wallet, request.query_params.get("kind"), _date_param(request, "date_from"), _date_param(request, "date_to")
        )
        paginator = SafePaginator()
        page = paginator.paginate_queryset(rows, request)
        return paginator.get_paginated_response(describe(page))


STATEMENT_MAX_DAYS = 366
STATEMENT_COLUMNS = ["date", "kind", "description", "amount", "balance_after", "reference", "status"]


@extend_schema(
    description="The history as a CSV statement for a date range (default: the last 30 days, at most a year).",
    request=None,
    parameters=HISTORY_PARAMETERS[1:],
    responses={200: OpenApiResponse(description="text/csv")},
)
class StatementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date_to = _date_param(request, "date_to") or timezone.localdate()
        date_from = _date_param(request, "date_from") or date_to - timedelta(days=30)
        if date_from > date_to or (date_to - date_from).days > STATEMENT_MAX_DAYS:
            return Response(
                {"error": f"Pick a range of at most {STATEMENT_MAX_DAYS} days.", "code": "invalid_range"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        wallet = Wallet.objects.get(user=request.user)
        lines = describe(history(wallet, date_from=date_from, date_to=date_to))
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="quicksurf-statement-{date_from}-to-{date_to}.csv"'
        writer = csv.DictWriter(response, fieldnames=STATEMENT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for line in reversed(lines):  # oldest first, like a bank statement
            writer.writerow({**line, "date": timezone.localtime(line["date"]).strftime("%Y-%m-%d %H:%M")})
        return response
