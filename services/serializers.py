from __future__ import annotations

from decimal import Decimal
import re
from typing import Set

from django.conf import settings
from rest_framework import serializers

from .models import (
    AirtimeTransaction,
    DataTransaction,
    NETWORK_CHOICES,
    STATUS_CHOICES,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NETWORK_KEYS = {k for k, _ in NETWORK_CHOICES}
STATUS_KEYS = {k for k, _ in STATUS_CHOICES}

# VTpass sandbox "magic" numbers used to simulate outcomes for airtime
SANDBOX_MAGIC_NUMBERS: Set[str] = {
    "08011111111",   # success
    "201000000000",  # pending
    "500000000000",  # unexpected response
    "400000000000",  # no response
    "300000000000",  # timeout
}

ALLOW_SANDBOX_NUMBERS = str(getattr(settings, "PROVIDER_MODE", "LIVE")).upper() != "LIVE"

# ---------------------------------------------------------------------------
# Phone normalization
# ---------------------------------------------------------------------------

def normalize_ng_phone(raw: str) -> str:
    """
    Accept common NG formats and return local 11‑digit form (e.g., '0703...').
    Allowed inputs:
      - '0703xxxxxxx' (11 digits starting with 0)
      - '+234703xxxxxxx' (13 chars -> 234 + 10 digits)
      - '234703xxxxxxx'
      - ignores spaces/dashes/()

    Special case: when not LIVE (sandbox/mock), allow VTpass sandbox magic
    numbers unchanged (some are 12 digits like 201000000000).
    """
    if not raw:
        return ""

    digits = re.sub(r"\D+", "", str(raw))

    # Allow sandbox magic numbers as-is
    if ALLOW_SANDBOX_NUMBERS and digits in SANDBOX_MAGIC_NUMBERS:
        return digits

    # Convert +234/234 formats to local 0xxxxxxxxxx
    if digits.startswith("234"):
        # 234 + 10 digits -> 13 total
        if len(digits) == 13:
            return "0" + digits[3:]
        # some gateways send '2340' + 10 digits
        if len(digits) == 14 and digits.startswith("2340"):
            return digits[3:]
        return ""

    # Already local format
    if len(digits) == 11 and digits.startswith("0"):
        return digits

    return ""  # reject other forms


# ---------------------------------------------------------------------------
# Read serializers (models → JSON)
# ---------------------------------------------------------------------------
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
        read_only_fields = fields


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
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Write serializers (requests → validated data)
# ---------------------------------------------------------------------------
CLIENT_REF_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")


class AirtimePurchaseRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    network = serializers.CharField()
    phone = serializers.CharField()
    client_reference = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, v: Decimal):
        if v <= 0:
            raise serializers.ValidationError("Amount must be greater than 0.")
        # Optional business rule: minimum VTU amount (uncomment if you want)
        # if v < Decimal("50"):
        #     raise serializers.ValidationError("Minimum airtime amount is ₦50.")
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
            # Tailored error message to help frontend
            if ALLOW_SANDBOX_NUMBERS:
                raise serializers.ValidationError(
                    "Enter a valid NG number like 0703xxxxxxx/+234703xxxxxxx, or use VTpass sandbox test numbers."
                )
            raise serializers.ValidationError(
                "Enter a valid Nigerian number like 0703xxxxxxx or +234703xxxxxxx."
            )
        return normalized

    def validate_client_reference(self, v: str):
        v = (v or "").strip() or None
        if v and not CLIENT_REF_RE.match(v):
            raise serializers.ValidationError(
                "client_reference contains invalid characters or is too long (max 64)."
            )
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
            if ALLOW_SANDBOX_NUMBERS:
                raise serializers.ValidationError(
                    "Enter a valid NG number like 0703xxxxxxx/+234703xxxxxxx, or use VTpass sandbox test numbers."
                )
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
            raise serializers.ValidationError(
                "client_reference contains invalid characters or is too long (max 64)."
            )
        return v

