import requests
from decouple import config
BASE = config("VTPASS_BASE", default="https://vtpass.com/api")
USER = config("VTPASS_USERNAME")
PWD  = config("VTPASS_PASSWORD")
def _auth(): return (USER, PWD)

def _post(save, endpoint, payload):
    url = f"{BASE}{endpoint}"
    save("req", endpoint, payload, None)
    r = requests.post(url, json=payload, auth=_auth(), timeout=30)
    data = r.json() if r.headers.get("content-type","").startswith("application/json") else {"raw": r.text}
    save("res", endpoint, data, r.status_code)
    return r.status_code, data

def pay_airtime(save, *, network, amount, phone, request_id):
    return _post(save, "/pay", {"serviceID": network, "amount": str(amount), "phone": phone, "request_id": request_id})

def pay_data(save, *, network, variation_code, amount, phone, request_id):
    return _post(save, "/pay", {"serviceID": f"{network}-data", "variation_code": variation_code, "amount": str(amount), "phone": phone, "request_id": request_id})

def requery(save, *, request_id):
    return _post(save, "/requery", {"request_id": request_id})
