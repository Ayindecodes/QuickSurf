# services/vtpass.py
import base64
import uuid
import time
from typing import Callable, Dict, Optional, Tuple

import requests
from decouple import config

# ------------------------------------------------------------------------------
# Config
# ------------------------------------------------------------------------------
PROVIDER_MODE = config("PROVIDER_MODE", default="MOCK").upper()  # LIVE | MOCK
VTPASS_EMAIL = config("VTPASS_EMAIL", default="")
VTPASS_API_KEY = config("VTPASS_API_KEY", default="")
VTPASS_PUBLIC_KEY = config("VTPASS_PUBLIC_KEY", default="")

VTPASS_BASE_URL = (
    "https://vtpass.com/api" if PROVIDER_MODE == "LIVE" else "https://sandbox.vtpass.com/api"
)

DEFAULT_TIMEOUT = 15
MAX_RETRIES = 2  # small, to avoid duplicate live charges on flaky networks

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------
def _basic_auth_token() -> str:
    auth_string = f"{VTPASS_EMAIL}:{VTPASS_API_KEY}"
    return base64.b64encode(auth_string.encode()).decode()


def vtpass_headers(include_public_key: bool = True) -> Dict[str, str]:
    """
    VTpass expects Basic auth using email:api_key. Public key is used on some GETs.
    Keeping api-key on all calls is harmless and sometimes required by their edge.
    """
    headers = {
        "Authorization": f"Basic {_basic_auth_token()}",
        "Content-Type": "application/json",
        "cache-control": "no-cache",
    }
    if include_public_key and VTPASS_PUBLIC_KEY:
        headers["api-key"] = VTPASS_PUBLIC_KEY
    return headers


def generate_request_id(prefix: str = "REQ") -> str:
    """Generate a unique client reference for idempotency."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _mask_value(val: Optional[str]) -> str:
    if not val:
        return ""
    s = str(val)
    if "@" in s:
        # mask email
        name, _, domain = s.partition("@")
        return (name[:2] + "***@" + domain) if name else "***@" + domain
    if s.isdigit() and len(s) >= 7:
        return f"{s[:3]}***{s[-4:]}"
    if len(s) > 6:
        return s[:3] + "***" + s[-3:]
    return "***"


def _mask_payload(payload: Dict) -> Dict:
    """Shallow mask for common sensitive fields before logging."""
    if not payload:
        return {}
    masked = dict(payload)
    for k in ["phone", "billersCode", "email", "api-key", "Authorization"]:
        if k in masked:
            masked[k] = _mask_value(masked[k])
    return masked


def _request(
    method: str,
    path: str,
    *,
    json: Optional[Dict] = None,
    params: Optional[Dict] = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = MAX_RETRIES,
    log_fn: Optional[Callable[[Dict], None]] = None,
) -> Tuple[int, Dict]:
    """
    Minimal retry loop for transient network errors.
    Never retries on HTTP 2xx/4xx/5xx to avoid duplicate provider charges.
    Only retries on connection/timeouts (pre-flight).
    """
    url = f"{VTPASS_BASE_URL}{path}"
    last_exc = None

    for attempt in range(retries + 1):
        try:
            resp = requests.request(
                method,
                url,
                headers=vtpass_headers(),
                json=json,
                params=params,
                timeout=timeout,
            )
            # Log once per attempt (masked)
            if log_fn:
                log_fn({
                    "service": "vtpass",
                    "endpoint": path,
                    "status_code": resp.status_code,
                    "request_id": (json or params or {}).get("request_id"),
                    "request": _mask_payload(json or params or {}),
                    "response": _mask_payload(_safe_json(resp)),
                })
            return resp.status_code, _safe_json(resp)
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < retries:
                time.sleep(0.8)  # brief backoff
                continue
            # Final failure (network)
            payload = json or params or {}
            if log_fn:
                log_fn({
                    "service": "vtpass",
                    "endpoint": path,
                    "status_code": 0,
                    "request_id": payload.get("request_id"),
                    "request": _mask_payload(payload),
                    "response": {"error": str(e)},
                })
            return 0, {
                "code": "999",
                "response_description": "Network error",
                "error": str(e),
            }
    # Should not reach here
    raise RuntimeError(f"Unexpected _request flow. Last error: {last_exc}")


def _safe_json(resp: requests.Response) -> Dict:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def map_provider_state(payload: Dict) -> Tuple[str, bool]:
    """
    Map VTpass provider 'code' to internal state.
    - "000" => success
    - "099" and some others may indicate pending (VTpass will confirm via status)
    - anything else => failed
    Returns (state, ok)
    """
    code = str(payload.get("code", "")).strip()
    if code == "000":
        return "success", True
    if code in {"099", "016"}:
        # 016 often shows up for 'transaction in progress' on some services
        return "pending", False
    return "failed", False

# ------------------------------------------------------------------------------
# Service IDs
# ------------------------------------------------------------------------------
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

# ------------------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------------------
def get_service_variations(service_id: str, *, log_fn: Optional[Callable[[Dict], None]] = None) -> Dict:
    """
    Fetch available plans/packages for a given service_id.
    """
    status, body = _request(
        "GET",
        "/service-variations",
        params={"serviceID": service_id},
        log_fn=log_fn,
    )
    return {
        "ok": status == 200 and str(body.get("code", "")) in {"000"},
        "state": "success" if str(body.get("code", "")) == "000" else "failed",
        "provider": body,
        "message": body.get("response_description") or "",
    }


def purchase_airtime(
    network: str,
    phone: str,
    amount: float,
    *,
    request_id: Optional[str] = None,
    log_fn: Optional[Callable[[Dict], None]] = None,
) -> Dict:
    """
    Make an airtime purchase. Ensure you persist request_id (idempotency) before calling.
    """
    service_id = AIRTIME_SERVICE_IDS.get(network.lower())
    if not service_id:
        return {"ok": False, "state": "failed", "message": f"Unsupported network: {network}"}

    rid = request_id or generate_request_id("AIRTIME")
    payload = {
        "request_id": rid,
        "serviceID": service_id,
        "amount": str(amount),
        "phone": phone,
    }
    status, body = _request("POST", "/pay", json=payload, log_fn=log_fn)
    state, ok = map_provider_state(body)
    return {
        "ok": ok,
        "state": state,  # success | pending | failed
        "provider": body,
        "request_id": rid,
        "message": body.get("response_description") or "",
        "http_status": status,
    }


def purchase_data(
    network: str,
    phone: str,
    variation_code: str,
    *,
    request_id: Optional[str] = None,
    log_fn: Optional[Callable[[Dict], None]] = None,
) -> Dict:
    """
    Purchase data bundle. Persist request_id before calling for idempotency.
    """
    service_id = DATA_SERVICE_IDS.get(network.lower())
    if not service_id:
        return {"ok": False, "state": "failed", "message": f"Unsupported network: {network}"}

    rid = request_id or generate_request_id("DATA")
    payload = {
        "request_id": rid,
        "serviceID": service_id,
        "billersCode": phone,      # VTpass uses billersCode for the target line
        "variation_code": variation_code,
        "phone": phone,
    }
    status, body = _request("POST", "/pay", json=payload, log_fn=log_fn)
    state, ok = map_provider_state(body)
    return {
        "ok": ok,
        "state": state,
        "provider": body,
        "request_id": rid,
        "message": body.get("response_description") or "",
        "http_status": status,
    }


def requery_status(
    request_id: str,
    *,
    log_fn: Optional[Callable[[Dict], None]] = None,
) -> Dict:
    """
    Re-query the status of a prior transaction (idempotent reconciliation).
    """
    status, body = _request(
        "GET",
        "/requery",
        params={"request_id": request_id},
        log_fn=log_fn,
    )
    state, ok = map_provider_state(body)
    return {
        "ok": ok,
        "state": state,
        "provider": body,
        "request_id": request_id,
        "message": body.get("response_description") or "",
        "http_status": status,
    }
