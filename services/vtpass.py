import requests
import base64
import uuid
from decouple import config

# ✅ Load secrets from .env
VTPASS_EMAIL      = config("VTPASS_EMAIL")
VTPASS_API_KEY    = config("VTPASS_API_KEY")
VTPASS_PUBLIC_KEY = config("VTPASS_PUBLIC_KEY")
VTPASS_SECRET_KEY = config("VTPASS_SECRET_KEY")
VTPASS_BASE_URL   = config("VTPASS_BASE_URL", default="https://sandbox.vtpass.com/api")

# ✅ Prepare Basic Auth Header (email + API key)
auth_string = f"{VTPASS_EMAIL}:{VTPASS_API_KEY}"
AUTH_HEADER = base64.b64encode(auth_string.encode()).decode()


def generate_request_id(prefix="REQ"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def vtpass_headers():
    return {
        "Authorization": f"Basic {AUTH_HEADER}",
        "Content-Type": "application/json",
        "cache-control": "no-cache"
    }


# ✅ Service ID maps — match VTpass naming
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


# ✅ Service variations lookup (uses public key)
def get_service_variations(service_id):
    """Fetch available plans/packages for a given service_id."""
    try:
        url = f"{VTPASS_BASE_URL}/service-variations?serviceID={service_id}"
        headers = {"api-key": VTPASS_PUBLIC_KEY}
        r = requests.get(url, headers=headers, timeout=10)
        return r.json()
    except requests.exceptions.RequestException as e:
        return {"code": "999", "response_description": "Network error", "raw": str(e)}


# ✅ Airtime purchase — now accepts custom request_id for idempotency
def purchase_airtime(network, phone, amount, request_id=None):
    service_id = AIRTIME_SERVICE_IDS.get(network.lower())
    if not service_id:
        return {"code": "998", "response_description": f"Unsupported network: {network}"}

    payload = {
        "request_id": request_id or generate_request_id("AIRTIME"),
        "serviceID": service_id,
        "amount": str(amount),
        "phone": phone
    }
    try:
        r = requests.post(
            f"{VTPASS_BASE_URL}/pay",
            json=payload,
            headers=vtpass_headers(),
            timeout=10
        )
        return r.json()
    except requests.exceptions.RequestException as e:
        return {
            "code": "999",
            "response_description": "Network error",
            "raw": str(e)
        }


# ✅ Data purchase — now accepts custom request_id for idempotency
def purchase_data(network, phone, plan_code, request_id=None):
    service_id = DATA_SERVICE_IDS.get(network.lower())
    if not service_id:
        return {"code": "998", "response_description": f"Unsupported network: {network}"}

    payload = {
        "request_id": request_id or generate_request_id("DATA"),
        "serviceID": service_id,
        "billersCode": phone,
        "variation_code": plan_code,
        "phone": phone
    }
    try:
        r = requests.post(
            f"{VTPASS_BASE_URL}/pay",
            json=payload,
            headers=vtpass_headers(),
            timeout=10
        )
        return r.json()
    except requests.exceptions.RequestException as e:
        return {
            "code": "999",
            "response_description": "Network error",
            "raw": str(e)
        }
