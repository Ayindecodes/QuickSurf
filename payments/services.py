# payments/services.py
"""
Paystack payment handling shared by the verify endpoint, the webhook, the admin and the
verify_pending_topups command. Wallet money itself only moves through wallets.services.
"""
from django.db import transaction
from django.utils import timezone

from services.models import ProviderLog
from wallets import services as wallet_services

from .models import PaymentIntent
from .paystack import kobo

# No Paystack answer may change these: "success" is credited, "review" waits for staff.
FINAL_STATUSES = ("success", "review")

# Paystack fields worth keeping; card authorization, customer details and logs are dropped.
KEPT_DATA_FIELDS = ("id", "status", "reference", "amount", "currency", "paid_at", "created_at",
                    "channel", "gateway_response", "fees")


def trim_payload(body):
    """A Paystack verify response or webhook without card, customer or log details."""
    if not isinstance(body, dict):
        return body
    trimmed = {key: body[key] for key in ("event", "status", "message", "raw", "http_status") if key in body}
    data = body.get("data")
    if isinstance(data, dict):
        trimmed["data"] = {key: data[key] for key in KEPT_DATA_FIELDS if key in data}
    return trimmed


def log_paystack(user, reference, endpoint, request_payload, response, status_code):
    try:
        ProviderLog.objects.create(
            user=user,
            service_type="paystack",
            client_reference=reference,
            request_payload=request_payload or {},
            response_payload=response or {},
            status_code=str(status_code),
            endpoint=endpoint,
            provider="paystack",
        )
    except Exception:
        pass


def credit_wallet_once(user, amount, reference: str) -> bool:
    """Credit a paid intent to its wallet and mark it success. Returns False if it was already final."""
    with transaction.atomic():
        intent = PaymentIntent.objects.select_for_update().get(reference=reference, user=user)
        if intent.status in FINAL_STATUSES:
            return False

        # The ledger's unique (topup, reference) entry is the hard guarantee against crediting twice.
        credited = wallet_services.credit_topup(user, amount, reference)
        intent.mark_success(when=timezone.now())
    return credited


def set_status_unless_final(intent, new_status: str) -> None:
    """Change an intent's status unless it is already credited or waiting for staff review."""
    PaymentIntent.objects.filter(pk=intent.pk).exclude(status__in=FINAL_STATUSES).update(
        status=new_status, updated=timezone.now()
    )


def payment_matches(intent, data) -> bool:
    """Paystack charged exactly what this intent asked for, in the same currency, for this reference."""
    try:
        paid_kobo = int(data.get("amount"))
    except (TypeError, ValueError):
        return False
    return (
        paid_kobo == kobo(intent.amount)
        and data.get("currency") == intent.currency
        and data.get("reference") == intent.reference
    )


def apply_verification(intent, code, body):
    """
    Apply a Paystack verify response to an intent. Returns (status, credited), where status is
    "success", "review" (paid, but not the amount/currency asked for, so not credited),
    "failed", "abandoned" or "pending".
    """
    if intent.status in FINAL_STATUSES:
        return intent.status, False

    body = body if isinstance(body, dict) else {}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    paystack_status = data.get("status")

    if code == 200 and body.get("status") and paystack_status == "success":
        if not payment_matches(intent, data):
            set_status_unless_final(intent, "review")
            return "review", False
        return "success", credit_wallet_once(intent.user, intent.amount, intent.reference)

    if paystack_status in ("failed", "abandoned"):
        set_status_unless_final(intent, paystack_status)
        return paystack_status, False

    set_status_unless_final(intent, "pending")
    return "pending", False
