from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Wallet, Transaction

User = get_user_model()


class WalletBalanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ('balance', 'locked')


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = ('id', 'amount', 'type', 'timestamp', 'reference')


class CreditSerializer(serializers.Serializer):
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)

    def save(self):
        user = self.validated_data.get('user') or self.context['request'].user
        wallet = Wallet.objects.get(user=user)
        wallet.balance += self.validated_data['amount']
        wallet.save()
        return Transaction.objects.create(
            wallet=wallet,
            amount=self.validated_data['amount'],
            type='credit'
        )


class LockSerializer(serializers.Serializer):
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)

    def save(self):
        user = self.validated_data.get('user') or self.context['request'].user
        wallet = Wallet.objects.get(user=user)
        available = wallet.balance - wallet.locked
        if self.validated_data['amount'] > available:
            raise serializers.ValidationError("Insufficient available funds")
        wallet.locked += self.validated_data['amount']
        wallet.save()
        return Transaction.objects.create(
            wallet=wallet,
            amount=self.validated_data['amount'],
            type='lock'
        )


class UnlockSerializer(serializers.Serializer):
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)

    def save(self):
        user = self.validated_data.get('user') or self.context['request'].user
        wallet = Wallet.objects.get(user=user)
        if self.validated_data['amount'] > wallet.locked:
            raise serializers.ValidationError("Insufficient locked funds")
        wallet.locked -= self.validated_data['amount']
        wallet.save()
        return Transaction.objects.create(
            wallet=wallet,
            amount=self.validated_data['amount'],
            type='unlock'
        )
