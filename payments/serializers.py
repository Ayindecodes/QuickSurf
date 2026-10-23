# payments/serializers.py
from decimal import Decimal

from django.conf import settings
from rest_framework import serializers

from .models import PaymentIntent


class PaymentInitRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    metadata = serializers.DictField(required=False)

    def validate_amount(self, v: Decimal):
        if v < Decimal("100"):
            raise serializers.ValidationError("Minimum top-up is ₦100.")
        if settings.TOPUP_MAX_AMOUNT and v > settings.TOPUP_MAX_AMOUNT:
            raise serializers.ValidationError(f"The most you can top up at once is ₦{settings.TOPUP_MAX_AMOUNT:,}.")
        return v


class PaymentIntentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentIntent
        fields = [
            "id",
            "user",
            "amount",
            "currency",
            "reference",
            "status",
            "authorization_url",
            "access_code",
            "paid_at",
            "created",
            "updated",
        ]
        read_only_fields = fields
