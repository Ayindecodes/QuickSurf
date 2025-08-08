import requests
import base64
import uuid
from decouple import config

# ✅ Load secrets from .env
VTPASS_EMAIL = config("VTPASS_EMAIL")
VTPASS_API_KEY = config("VTPASS_API_KEY")
VTPASS_BASE_URL = config("VTPASS_BASE_URL", default="https://sandbox.vtpass.com/api")

# ✅ Prepare Basic Auth Header
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


def purchase_airtime(network, phone, amount):
    payload = {
        "request_id": generate_request_id("AIRTIME"),
        "serviceID": f"{network}-airtime",
        "amount": str(amount),
        "phone": phone
    }

    try:
        response = requests.post(
            f"{VTPASS_BASE_URL}/pay",
            json=payload,
            headers=vtpass_headers(),
            timeout=10
        )
        return response.json()
    except requests.exceptions.RequestException as e:
        return {
            "code": "999",
            "response_description": "Network error",
            "raw": str(e)
        }


def purchase_data(network, phone, plan_code):
    payload = {
        "request_id": generate_request_id("DATA"),
        "serviceID": f"{network}-data",
        "billersCode": phone,
        "variation_code": plan_code,
        "phone": phone
    }

    try:
        response = requests.post(
            f"{VTPASS_BASE_URL}/pay",
            json=payload,
            headers=vtpass_headers(),
            timeout=10
        )
        return response.json()
    except requests.exceptions.RequestException as e:
        return {
            "code": "999",
            "response_description": "Network error",
            "raw": str(e)
        }
