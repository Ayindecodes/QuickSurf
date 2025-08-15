# wallets/views.py
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination

from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiParameter

from .models import Wallet
from services.models import AirtimeTransaction, DataTransaction
from .serializers import (
    WalletBalanceSerializer,
    TransactionSerializer,
    CreditSerializer,
    LockSerializer,
    UnlockSerializer,
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
            "locked": str(getattr(wallet, "locked_amount", 0)),
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
    description="List the authenticated user's wallet transactions (most recent first).",
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

        qs = wallet.transactions.all().order_by("-timestamp")

        paginator = SafePaginator()
        page = paginator.paginate_queryset(qs, request)
        serializer = TransactionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


@extend_schema(
    description="Admin-only credit to a user's wallet.",
    request=CreditSerializer,
    responses={201: TransactionSerializer, 400: OpenApiResponse(description="Validation error")},
)
class CreditView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        serializer = CreditSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            tx = serializer.save()
            return Response(TransactionSerializer(tx).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    description="Lock funds in the authenticated user's wallet.",
    request=LockSerializer,
    responses={201: TransactionSerializer, 400: OpenApiResponse(description="Validation error")},
)
class LockFundsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = {"user": request.user.id, "amount": request.data.get("amount")}
        serializer = LockSerializer(data=data, context={"request": request})
        if serializer.is_valid():
            tx = serializer.save()
            return Response(TransactionSerializer(tx).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    description="Unlock previously locked funds in the authenticated user's wallet.",
    request=UnlockSerializer,
    responses={201: TransactionSerializer, 400: OpenApiResponse(description="Validation error")},
)
class UnlockFundsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = {"user": request.user.id, "amount": request.data.get("amount")}
        serializer = UnlockSerializer(data=data, context={"request": request})
        if serializer.is_valid():
            tx = serializer.save()
            return Response(TransactionSerializer(tx).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

