from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework import status

from .models import Wallet
from .serializers import (
    WalletBalanceSerializer,
    TransactionSerializer,
    CreditSerializer,
    LockSerializer,
    UnlockSerializer,
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


class TransactionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            wallet = Wallet.objects.get(user=request.user)
            transactions = wallet.transactions.order_by('-timestamp')
            serializer = TransactionSerializer(transactions, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except Wallet.DoesNotExist:
            return Response({"detail": "Wallet not found."}, status=status.HTTP_404_NOT_FOUND)


class CreditView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        serializer = CreditSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            tx = serializer.save()
            return Response(TransactionSerializer(tx).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LockFundsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = {
            'user': request.user.id,
            'amount': request.data.get('amount')
        }
        serializer = LockSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            tx = serializer.save()
            return Response(TransactionSerializer(tx).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UnlockFundsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        data = {
            'user': request.user.id,
            'amount': request.data.get('amount')
        }
        serializer = UnlockSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            tx = serializer.save()
            return Response(TransactionSerializer(tx).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
