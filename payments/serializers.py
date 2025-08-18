# payments/serializers.py
from decimal import Decimal
from rest_framework import serializers
from .models import PaymentIntent

class PaymentInitRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    metadata = serializers.DictField(required=False)

    def validate_amount(self, v: Decimal):
        if v < Decimal("100"):
            raise serializers.ValidationError("Minimum top-up is ₦100.")
        return v

class PaymentIntentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentIntent
        fields = [
            "id", "user", "amount", "currency", "reference", "status",
            "authorization_url", "access_code", "paid_at",
            "created", "updated",
        ]
        read_only_fields = fields
