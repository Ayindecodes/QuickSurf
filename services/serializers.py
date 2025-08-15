# services/serializers.py
from decimal import Decimal
import re
from rest_framework import serializers
from .models import AirtimeTransaction, DataTransaction, NETWORK_CHOICES, STATUS_CHOICES

NETWORK_KEYS = {k for k, _ in NETWORK_CHOICES}
STATUS_KEYS = {k for k, _ in STATUS_CHOICES}

# ---------- Phone normalization ----------
def normalize_ng_phone(raw: str) -> str:
    """
    Accept common NG formats and return local 11-digit form (e.g., '0703...').
    Allowed inputs:
      - '0703xxxxxxx' (11 digits starting with 0)
      - '+234703xxxxxxx' (13 digits with +234)
      - '234703xxxxxxx' (13 digits with 234)
      - with spaces/dashes/parentheses mixed in
    Output: '0703xxxxxxx'
    """
    if not raw:
        return ""
    digits = re.sub(r"\D+", "", str(raw))
    if digits.startswith("234"):
        if len(digits) == 13:
            return "0" + digits[3:]           # 234703... -> 0703...
        if len(digits) == 14 and digits.startswith("2340"):
            return digits[3:]                 # '2340' + 10 -> already 0 + 10
        return ""
    if len(digits) == 11 and digits.startswith("0"):
        return digits
    return ""  # reject other forms for safety


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
            "provider_request_id",   # aligned with models.py
            "provider_status",
            "timestamp",
            "updated",
        ]
        read_only_fields = [
            "user",
            "status",
            "client_reference",
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
            "provider_request_id",   # aligned with models.py
            "provider_status",
            "external_id",
            "timestamp",
            "updated",
        ]
        read_only_fields = [
            "user",
            "status",
            "client_reference",
            "provider_request_id",
            "provider_status",
            "external_id",
            "timestamp",
            "updated",
        ]


# ---------------------------------
# Purchase request (write) schemas
# ---------------------------------
CLIENT_REF_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")

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
            raise serializers.ValidationError(
                f"Unsupported network '{v}'. Choose one of: {', '.join(sorted(NETWORK_KEYS))}."
            )
        return key  # ensure strictly lowercase for consistency

    def validate_phone(self, v: str):
        normalized = normalize_ng_phone(v)
        if not normalized:
            raise serializers.ValidationError(
                "Enter a valid Nigerian number like 0703xxxxxxx or +234703xxxxxxx."
            )
        return normalized

    def validate_client_reference(self, v: str):
        v = (v or "").strip() or None
        if v and not CLIENT_REF_RE.match(v):
            raise serializers.ValidationError("client_reference contains invalid characters or is too long (max 64).")
        return v


class DataPurchaseRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    network = serializers.CharField()
    phone = serializers.CharField()
    plan = serializers.CharField()
    client_reference = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, v: Decimal):
        if v <= 0:
            raise serializers.ValidationError("Amount must be greater than 0.")
        return v

    def validate_network(self, v: str):
        key = (v or "").lower().strip()
        if key not in NETWORK_KEYS:
            raise serializers.ValidationError(
                f"Unsupported network '{v}'. Choose one of: {', '.join(sorted(NETWORK_KEYS))}."
            )
        return key

    def validate_phone(self, v: str):
        normalized = normalize_ng_phone(v)
        if not normalized:
            raise serializers.ValidationError(
                "Enter a valid Nigerian number like 0703xxxxxxx or +234703xxxxxxx."
            )
        return normalized

    def validate_plan(self, v: str):
        if not (v or "").strip():
            raise serializers.ValidationError("Plan (variation_code) is required.")
        return v.strip()

    def validate_client_reference(self, v: str):
        v = (v or "").strip() or None
        if v and not CLIENT_REF_RE.match(v):
            raise serializers.ValidationError("client_reference contains invalid characters or is too long (max 64).")
        return v
