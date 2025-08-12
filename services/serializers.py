# services/serializers.py
from decimal import Decimal
import re
from rest_framework import serializers
from .models import AirtimeTransaction, DataTransaction, NETWORK_CHOICES, STATUS_CHOICES

PHONE_REGEX = re.compile(r"^\+?\d{7,15}$")
NETWORK_KEYS = {k for k, _ in NETWORK_CHOICES}
STATUS_KEYS = {k for k, _ in STATUS_CHOICES}


# ------------------------------
# Model serializers (read models)
# ------------------------------
class AirtimeTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AirtimeTransaction
        fields = [
            "id",
            "user",
            "network",
            "phone",
            "amount",
            "status",
            "client_reference",
            "provider_request_id",
            "provider_status",
            "timestamp",
            "updated",
        ]
        read_only_fields = [
            "user",
            "status",
            "client_reference",        # server sets this on create flow
            "provider_request_id",
            "provider_status",
            "timestamp",
            "updated",
        ]


class DataTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataTransaction
        fields = [
            "id",
            "user",
            "network",
            "phone",
            "plan",
            "amount",
            "status",
            "client_reference",
            "provider_request_id",
            "provider_status",
            "external_id",
            "timestamp",
            "updated",
        ]
        read_only_fields = [
            "user",
            "status",
            "client_reference",        # server sets this on create flow
            "provider_request_id",
            "provider_status",
            "external_id",
            "timestamp",
            "updated",
        ]


# ---------------------------------
# Purchase request (write) schemas
# ---------------------------------
class AirtimePurchaseRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    network = serializers.CharField()
    phone = serializers.CharField()
    client_reference = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, v: Decimal):
        if v <= 0:
            raise serializers.ValidationError("Amount must be greater than 0.")
        return v

    def validate_network(self, v: str):
        key = (v or "").lower().strip()
        if key not in NETWORK_KEYS:
            raise serializers.ValidationError(f"Unsupported network '{v}'. Choose one of: {', '.join(sorted(NETWORK_KEYS))}.")
        return key

    def validate_phone(self, v: str):
        if not PHONE_REGEX.match(v or ""):
            raise serializers.ValidationError("Enter a valid phone number (7–15 digits, optional leading +).")
        return v

    def validate_client_reference(self, v: str):
        # allow server to generate if missing/blank
        return v.strip() or None


class DataPurchaseRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    network = serializers.CharField()
    phone = serializers.CharField()
    plan = serializers.CharField()                 # your UI must provide the VTpass variation_code
    client_reference = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, v: Decimal):
        if v <= 0:
            raise serializers.ValidationError("Amount must be greater than 0.")
        return v

    def validate_network(self, v: str):
        key = (v or "").lower().strip()
        if key not in NETWORK_KEYS:
            raise serializers.ValidationError(f"Unsupported network '{v}'. Choose one of: {', '.join(sorted(NETWORK_KEYS))}.")
        return key

    def validate_phone(self, v: str):
        if not PHONE_REGEX.match(v or ""):
            raise serializers.ValidationError("Enter a valid phone number (7–15 digits, optional leading +).")
        return v

    def validate_plan(self, v: str):
        if not (v or "").strip():
            raise serializers.ValidationError("Plan (variation_code) is required.")
        return v.strip()

    def validate_client_reference(self, v: str):
        # allow server to generate if missing/blank
        return v.strip() or None
