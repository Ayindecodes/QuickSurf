# services/vtpass.py
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional, Tuple, Any

import requests
from decouple import config

# ============================================================================
# Configuration (LIVE or MOCK/SANDBOX via env)
# ============================================================================
PROVIDER_MODE = config("PROVIDER_MODE", default="MOCK").upper()  # LIVE | MOCK
VTPASS_API_KEY = config("VTPASS_API_KEY", default="")
VTPASS_PUBLIC_KEY = config("VTPASS_PUBLIC_KEY", default="")
VTPASS_SECRET_KEY = config("VTPASS_SECRET_KEY", default="")
VTPASS_BASE_URL = config(
    "VTPASS_BASE_URL",
    default=("https://vtpass.com/api" if PROVIDER_MODE == "LIVE" else "https://sandbox.vtpass.com/api"),
).rstrip("/")

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


def _mask_value(val: Optional[str]) -> str:
    if not val:
        return ""
    s = str(val)
    if "@" in s:
        name, _, domain = s.partition("@")
        return (name[:2] + "***@" + domain) if name else "***@" + domain
    if s.isdigit() and len(s) >= 7:
        return f"{s[:3]}***{s[-4:]}"
    if len(s) > 6:
        return s[:3] + "***" + s[-3:]
    return "***"


def _mask_payload(payload: Dict) -> Dict:
    if not payload:
        return {}
    masked = dict(payload)
    for k in ["phone", "billersCode", "email", "secret-key", "public-key", "api-key"]:
        if k in masked:
            masked[k] = _mask_value(masked[k])
    return masked


def _request(
    method: str,
    path: str,
    *,
    json: Optional[Dict] = None,
    params: Optional[Dict] = None,
    timeout: Tuple[int, int] = DEFAULT_TIMEOUT,
    retries: int = MAX_RETRIES,
    log_fn: Optional[Callable[[Dict], None]] = None,
) -> Tuple[int, Dict]:
    """
    Minimal retry loop for transient network errors only (connect/read timeouts).
    Never retries on any HTTP response to avoid duplicate provider charges.
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
                    "request": _mask_payload(json or params or {}),
                    "response": _mask_payload(parsed),
                })
            return resp.status_code, parsed

        except (requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as e:
            last_exc = e
            if attempt < retries:
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
                    "request": _mask_payload(json or params or {}),
                    "response": _mask_payload(parsed_err),
                })
            return 0, parsed_err

    raise RuntimeError(f"Unexpected _request flow. Last error: {last_exc}")


def generate_request_id(prefix: str = "") -> str:
    """
    First 12 chars must be Lagos time YYYYMMDDHHMM (>=12 chars total).
    Docs: Request ID Format.
    """
    now = datetime.utcnow() + timedelta(hours=1)  # Africa/Lagos (UTC+1)
    base = now.strftime("%Y%m%d%H%M")
    return f"{base}{uuid.uuid4().hex[:12].upper()}"


# ============================================================================
# Mapping helpers (strict per VTU docs)
# ============================================================================

def _tx_dict(body: Dict) -> Dict:
    return body.get("content", {}).get("transactions", {}) if isinstance(body, dict) else {}


def strict_map_outcome(body: Dict) -> str:
    """
    Success only when: code == "000" AND transactions.status == "delivered".
    Pending when: transactions.status == "pending" OR code in {"099","016"}.
    Failed otherwise (incl. 027/028 etc.).
    """
    try:
        code = str(body.get("code", "")).strip()
        tx = _tx_dict(body)
        tx_status = str(tx.get("status", "")).lower()
        if code == "000" and tx_status == "delivered":
            return "successful"
        if tx_status == "pending" or code in {"099", "016"}:
            return "pending"
    except Exception:
        pass
    return "failed"


# ============================================================================
# Service IDs
# ============================================================================
AIRTIME_SERVICE_IDS = {
    "mtn": "mtn",
    "glo": "glo",
    "airtel": "airtel",
    "9mobile": "9mobile",
}

DATA_SERVICE_IDS = {
    "mtn": "mtn-data",
    "glo": "glo-data",
    "airtel": "airtel-data",
    "9mobile": "9mobile-data",
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

    status_code, body = _request("POST", "/pay", json=payload, log_fn=log_fn)
    outcome = strict_map_outcome(body if isinstance(body, dict) else {})

    return {
        "ok": outcome == "successful",
        "state": outcome,                 # successful | pending | failed
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

    status_code, body = _request("POST", "/pay", json=payload, log_fn=log_fn)
    outcome = strict_map_outcome(body if isinstance(body, dict) else {})

    return {
        "ok": outcome == "successful",
        "state": outcome,                 # successful | pending | failed
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
        "state": outcome,                 # successful | pending | failed
        "provider": body,
        "request_id": request_id,
        "message": (body.get("response_description") if isinstance(body, dict) else "") or "",
        "http_status": status_code,
    }
