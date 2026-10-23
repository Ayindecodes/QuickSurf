from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Transaction, Wallet

User = get_user_model()


class WalletBalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ("balance", "locked")


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = ("id", "amount", "type", "timestamp", "reference", "balance_after")


class AdjustmentSerializer(serializers.Serializer):
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    note = serializers.CharField(max_length=255)

    def validate_amount(self, v):
        if v == 0:
            raise serializers.ValidationError("Amount can't be zero.")
        return v
