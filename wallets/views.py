# wallets/views.py
from rest_framework.views import APIView
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination

from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter

from . import services as wallet_services
from .models import Wallet
from services.models import AirtimeTransaction, DataTransaction
from .serializers import (
    WalletBalanceSerializer,
    TransactionSerializer,
    AdjustmentSerializer,
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
    responses={200: OpenApiResponse(description="Summary payload"), 404: OpenApiResponse(description="Wallet not found.")},
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

        return Response({
            "balance": str(wallet.balance),
            "locked": str(wallet.locked),
            "recent_airtime": [{
                "amount": str(t.amount),
                "network": t.network,
                "phone": t.phone,
                "status": t.status,
                "client_reference": t.client_reference,
                "provider_request_id": t.provider_request_id,
                "provider_status": t.provider_status,
                "timestamp": t.timestamp,
            } for t in airtime],
            "recent_data": [{
                "amount": str(t.amount),
                "network": t.network,
                "phone": t.phone,
                "plan": t.plan,
                "status": t.status,
                "client_reference": t.client_reference,
                "provider_request_id": t.provider_request_id,
                "provider_status": t.provider_status,
                "timestamp": t.timestamp,
            } for t in data],
        }, status=status.HTTP_200_OK)


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
