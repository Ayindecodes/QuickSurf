# payments/paystack.py
import hmac, hashlib, json, requests
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings

BASE = getattr(settings, "PAYSTACK_BASE_URL", "https://api.paystack.co")
SECRET = getattr(settings, "PAYSTACK_SECRET_KEY", "")
WEBHOOK_SECRET = getattr(settings, "PAYSTACK_WEBHOOK_SECRET", "") or SECRET

TIMEOUT = (5, 25)  # connect, read

def kobo(amount: Decimal) -> int:
    return int((amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100))

def _auth_headers():
    return {"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"}

def initialize(email: str, amount_naira: Decimal, reference: str, metadata=None, callback_url=None):
    payload = {
        "email": email,
        "amount": kobo(amount_naira),
        "reference": reference,
    }
    if metadata: payload["metadata"] = metadata
    if callback_url: payload["callback_url"] = callback_url
    r = requests.post(f"{BASE}/transaction/initialize", headers=_auth_headers(), json=payload, timeout=TIMEOUT)
    return r.status_code, r.json() if "application/json" in r.headers.get("Content-Type","") else {"raw": r.text, "http_status": r.status_code}

def verify(reference: str):
    r = requests.get(f"{BASE}/transaction/verify/{reference}", headers=_auth_headers(), timeout=TIMEOUT)
    return r.status_code, r.json() if "application/json" in r.headers.get("Content-Type","") else {"raw": r.text, "http_status": r.status_code}

def valid_webhook(signature_header: str, raw_body: bytes) -> bool:
    if not signature_header:
        return False
    digest = hmac.new(WEBHOOK_SECRET.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature_header)
