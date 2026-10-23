# services/vtpass.py
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Optional, Tuple

import requests
from django.conf import settings

from core.privacy import mask_pii

# ============================================================================
# Configuration: one source of truth in quicksurf/settings.py (PROVIDER_MODE, VTPASS_*)
# ============================================================================
PROVIDER_MODE = settings.PROVIDER_MODE  # LIVE | SANDBOX
VTPASS_API_KEY = settings.VTPASS_API_KEY
VTPASS_PUBLIC_KEY = settings.VTPASS_PUBLIC_KEY
VTPASS_SECRET_KEY = settings.VTPASS_SECRET_KEY
VTPASS_BASE_URL = settings.VTPASS_BASE_URL

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 25
DEFAULT_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
MAX_RETRIES = 1  # retry only pre-flight network errors; never on HTTP responses

Session = requests.Session()


# ============================================================================
# Helpers
# ============================================================================

def _headers_for(method: str) -> Dict[str, str]:
    """
    VTpass (official docs):
      - GET  : api-key + public-key
      - POST : api-key + secret-key
    """
    m = (method or "GET").upper()
    h = {
        "Content-Type": "application/json",
        "cache-control": "no-cache",
        "api-key": VTPASS_API_KEY or "",
    }
    if m == "GET":
        if VTPASS_PUBLIC_KEY:
            h["public-key"] = VTPASS_PUBLIC_KEY
    else:  # POST/PUT/etc.
        if VTPASS_SECRET_KEY:
            h["secret-key"] = VTPASS_SECRET_KEY
    return h


def _safe_json(resp: requests.Response) -> Dict:
    try:
        body = resp.json()
        if isinstance(body, dict):
            body.setdefault("http_status", resp.status_code)
            return body
        return {"raw": body, "http_status": resp.status_code}
    except Exception:
        return {"raw": getattr(resp, "text", ""), "http_status": resp.status_code}


def _request(
    method: str,
    path: str,
    *,
    json: Optional[Dict] = None,
    params: Optional[Dict] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    retries: int = MAX_RETRIES,
    resend_safe: bool = True,
    log_fn: Optional[Callable[[Dict], None]] = None,
) -> Tuple[int, Dict]:
    """
    Minimal retry loop for transient network errors only; never retries on an HTTP response.
    A connect timeout means the request never reached VTpass, so it is always safe to retry.
    Any other network error may come after VTpass received the request, so it is only retried
    when resend_safe (never for /pay, which would risk a second charge). Network errors come
    back as code "999", which callers must treat as an unknown outcome, not a failure.
    """
    url = f"{VTPASS_BASE_URL}{path}"
    last_exc: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            resp = Session.request(
                method=method,
                url=url,
                headers=_headers_for(method),
                json=json,
                params=params,
                timeout=timeout,
            )
            parsed = _safe_json(resp)
            if log_fn:
                log_fn({
                    "service": "vtpass",
                    "endpoint": path,
                    "status_code": resp.status_code,
                    "request_id": (json or params or {}).get("request_id"),
                    "request": mask_pii(json or params or {}),
                    "response": mask_pii(parsed),
                })
            return resp.status_code, parsed

        except (requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as e:
            last_exc = e
            if attempt < retries and (resend_safe or isinstance(e, requests.exceptions.ConnectTimeout)):
                time.sleep(0.8)
                continue
            parsed_err = {
                "code": "999",
                "response_description": "Network error",
                "error": str(e),
                "http_status": 0,
            }
            if log_fn:
                log_fn({
                    "service": "vtpass",
                    "endpoint": path,
                    "status_code": 0,
                    "request_id": (json or params or {}).get("request_id"),
                    "request": mask_pii(json or params or {}),
                    "response": mask_pii(parsed_err),
                })
            return 0, parsed_err

    raise RuntimeError(f"Unexpected _request flow. Last error: {last_exc}")


LAGOS = timezone(timedelta(hours=1), "Africa/Lagos")  # no daylight saving


def generate_request_id() -> str:
    """
    VTpass request ID: today's date and time in Africa/Lagos as YYYYMMDDHHII, then random
    alphanumerics (https://www.vtpass.com/documentation/how-to-generate-request-id/).
    """
    return datetime.now(LAGOS).strftime("%Y%m%d%H%M") + uuid.uuid4().hex[:12].upper()


# ============================================================================
# Mapping helpers (https://www.vtpass.com/documentation/response-codes/)
# ============================================================================

# Codes meaning VTpass did not and will not deliver, so the held money can be refunded.
FAILED_CODES = {
    "010",  # variation code does not exist
    "011",  # invalid arguments
    "012",  # product does not exist
    "013",  # below minimum amount
    "015",  # invalid request id (on requery: VTpass never received the purchase)
    "016",  # transaction failed
    "017",  # above maximum amount
    "018",  # low wallet balance (our VTpass wallet)
    "019",  # likely duplicate transaction (refused)
    "021", "022", "023", "024",  # account locked / suspended / no API access / inactive
    "027",  # IP not whitelisted
    "028",  # product not whitelisted on account
    "030",  # biller not reachable
    "031", "032",  # quantity limits
    "034", "035",  # service suspended / inactive
    "085",  # improper request id
    "087",  # invalid credentials
    "091",  # transaction not processed
}
REVERSED_CODES = {"040"}  # transaction reversal: VTpass returned the money to our wallet


def _tx_dict(body: Dict) -> Dict:
    content = body.get("content") if isinstance(body, dict) else None
    tx = content.get("transactions") if isinstance(content, dict) else None
    return tx if isinstance(tx, dict) else {}


def strict_map_outcome(body: Dict) -> str:
    """
    "successful" only for code 000 with status delivered; "reversed" for code 040 or status
    reversed; "failed" only for codes that mean VTpass won't deliver (or status failed).
    Everything else (099 processing, 014 request id already used, 083 system error, 999 our
    network error, unknown codes, unreadable bodies) is "pending": the outcome is unknown, so
    the money stays held until a requery or a VTpass callback settles it.
    """
    if not isinstance(body, dict):
        return "pending"
    code = str(body.get("code", "")).strip()
    tx_status = str(_tx_dict(body).get("status", "")).lower()
    if code in REVERSED_CODES or tx_status == "reversed":
        return "reversed"
    if code in FAILED_CODES or tx_status == "failed":
        return "failed"
    if code == "000" and tx_status == "delivered":
        return "successful"
    return "pending"


def transaction_id(body: Dict) -> Optional[str]:
    """VTpass's own transactionId for a purchase, if the response has one."""
    tx_id = _tx_dict(body).get("transactionId") or (body.get("transactionId") if isinstance(body, dict) else None)
    return str(tx_id) if tx_id else None


def extract_variations(content: Any) -> list:
    """Plan list from a /service-variations response (VTpass sometimes spells the key "varations")."""
    if not isinstance(content, dict):
        return []
    found = content.get("variations") or content.get("varations")
    return found if isinstance(found, list) else []


# ============================================================================
# Service IDs
# ============================================================================
AIRTIME_SERVICE_IDS = {
    "mtn": "mtn",
    "glo": "glo",
    "airtel": "airtel",
    "9mobile": "etisalat",  # VTpass still uses 9mobile's old name
}

DATA_SERVICE_IDS = {
    "mtn": "mtn-data",
    "glo": "glo-data",
    "airtel": "airtel-data",
    "9mobile": "etisalat-data",
}


# ============================================================================
# Public API
# ============================================================================
def get_service_variations(service_id: str, *, log_fn: Optional[Callable[[Dict], None]] = None) -> Dict:
    status, body = _request(
        "GET",
        "/service-variations",
        params={"serviceID": service_id},
        log_fn=log_fn,
    )
    code = str(body.get("code", "")).strip() or str(status)
    content = body.get("content") if isinstance(body, dict) else None
    variations = content.get("variations") if isinstance(content, dict) else None
    ok = (status == 200) and (code == "000" or (isinstance(variations, list) and variations))
    return {
        "ok": ok,
        "code": code,
        "response_description": body.get("response_description") or body.get("message") or "",
        "content": content,
        "raw": body,
        "http_status": status,
        "service_id": service_id,
    }


def purchase_airtime(
    network: str,
    phone: str,
    amount: float | int,
    *,
    request_id: Optional[str] = None,
    log_fn: Optional[Callable[[Dict], None]] = None,
) -> Dict:
    service_id = AIRTIME_SERVICE_IDS.get((network or "").lower())
    if not service_id:
        return {"ok": False, "state": "failed", "message": f"Unsupported network: {network}", "http_status": 400}

    rid = request_id or generate_request_id()
    payload = {
        "request_id": rid,
        "serviceID": service_id,
        "amount": int(float(amount)),  # VTU: send numeric
        "phone": str(phone),
    }

    status_code, body = _request("POST", "/pay", json=payload, resend_safe=False, log_fn=log_fn)
    outcome = strict_map_outcome(body if isinstance(body, dict) else {})

    return {
        "ok": outcome == "successful",
        "state": outcome,                 # successful | pending | failed | reversed
        "provider": body,
        "request_id": rid,
        "message": (body.get("response_description") if isinstance(body, dict) else "") or "",
        "http_status": status_code,
    }


def purchase_data(
    network: str,
    phone: str,
    variation_code: str,
    *,
    request_id: Optional[str] = None,
    log_fn: Optional[Callable[[Dict], None]] = None,
) -> Dict:
    service_id = DATA_SERVICE_IDS.get((network or "").lower())
    if not service_id:
        return {"ok": False, "state": "failed", "message": f"Unsupported network: {network}", "http_status": 400}

    rid = request_id or generate_request_id()
    payload = {
        "request_id": rid,
        "serviceID": service_id,
        "billersCode": str(phone),   # VTpass uses billersCode for target line
        "variation_code": variation_code,
        "phone": str(phone),
    }

    status_code, body = _request("POST", "/pay", json=payload, resend_safe=False, log_fn=log_fn)
    outcome = strict_map_outcome(body if isinstance(body, dict) else {})

    return {
        "ok": outcome == "successful",
        "state": outcome,                 # successful | pending | failed | reversed
        "provider": body,
        "request_id": rid,
        "message": (body.get("response_description") if isinstance(body, dict) else "") or "",
        "http_status": status_code,
    }


def requery_status(
    request_id: str,
    *,
    log_fn: Optional[Callable[[Dict], None]] = None,
) -> Dict:
    # VTpass requery is POST in the official docs
    status_code, body = _request(
        "POST",
        "/requery",
        json={"request_id": request_id},
        log_fn=log_fn,
    )

    outcome = strict_map_outcome(body if isinstance(body, dict) else {})

    return {
        "ok": outcome == "successful",
        "state": outcome,                 # successful | pending | failed | reversed
        "provider": body,
        "request_id": request_id,
        "message": (body.get("response_description") if isinstance(body, dict) else "") or "",
        "http_status": status_code,
    }


def get_balance() -> Optional[Decimal]:
    """Our VTpass wallet balance, or None if VTpass couldn't be reached or answered oddly."""
    status_code, body = _request("GET", "/balance")
    contents = body.get("contents") if isinstance(body, dict) else None
    raw = contents.get("balance") if isinstance(contents, dict) else None
    try:
        balance = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return balance.quantize(Decimal("0.01")) if status_code == 200 and balance.is_finite() else None
