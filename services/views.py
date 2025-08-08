from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from decimal import Decimal

from wallets.models import Wallet
from .models import AirtimeTransaction, DataTransaction
from .serializers import AirtimeTransactionSerializer, DataTransactionSerializer
from .vtpass_mock import purchase_airtime, purchase_data  # <-- using mock for now


class AirtimePurchaseView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        txns = AirtimeTransaction.objects.filter(user=request.user).order_by('-timestamp')
        serializer = AirtimeTransactionSerializer(txns, many=True)
        return Response(serializer.data)

    def post(self, request):
        user = request.user
        data = request.data

        for field in ["amount", "network", "phone"]:
            if field not in data:
                return Response({"error": f"{field} is required."}, status=400)

        try:
            amount = Decimal(data["amount"])
            if amount <= 0:
                raise ValueError
        except:
            return Response({"error": "Invalid amount."}, status=400)

        wallet = Wallet.objects.get(user=user)
        if wallet.balance < amount:
            return Response({"error": "Insufficient funds."}, status=400)

        # MOCK VTpass call
        vtpass_response = purchase_airtime(data["network"], data["phone"], amount)
        print("🧪 VTpass Airtime Response:", vtpass_response)

        if not isinstance(vtpass_response, dict):
            return Response({"error": "Invalid VTpass response", "raw": str(vtpass_response)}, status=502)

        transaction_status = "successful" if vtpass_response.get("code") == "000" else "failed"

        if transaction_status == "successful":
            wallet.balance -= amount
            wallet.save()

        txn = AirtimeTransaction.objects.create(
            user=user,
            amount=amount,
            network=data["network"],
            phone=data["phone"],
            status=transaction_status
        )

        serializer = AirtimeTransactionSerializer(txn)
        return Response(serializer.data, status=201)


class DataPurchaseView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        txns = DataTransaction.objects.filter(user=request.user).order_by('-timestamp')
        serializer = DataTransactionSerializer(txns, many=True)
        return Response(serializer.data)

    def post(self, request):
        user = request.user
        data = request.data

        for field in ["amount", "network", "phone", "plan"]:
            if field not in data:
                return Response({"error": f"{field} is required."}, status=400)

        try:
            amount = Decimal(data["amount"])
            if amount <= 0:
                raise ValueError
        except:
            return Response({"error": "Invalid amount."}, status=400)

        wallet = Wallet.objects.get(user=user)
        if wallet.balance < amount:
            return Response({"error": "Insufficient funds."}, status=400)

        # MOCK VTpass call
        vtpass_response = purchase_data(data["network"], data["phone"], data["plan"])
        print("🧪 VTpass Data Response:", vtpass_response)

        if not isinstance(vtpass_response, dict):
            return Response({"error": "Invalid VTpass response", "raw": str(vtpass_response)}, status=502)

        transaction_status = "successful" if vtpass_response.get("code") == "000" else "failed"

        if transaction_status == "successful":
            wallet.balance -= amount
            wallet.save()

        txn = DataTransaction.objects.create(
            user=user,
            amount=amount,
            network=data["network"],
            phone=data["phone"],
            plan=data["plan"],
            status=transaction_status
        )

        serializer = DataTransactionSerializer(txn)
        return Response(serializer.data, status=201)
