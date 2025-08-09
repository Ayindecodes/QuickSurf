import uuid
from .models import ProviderLog

print("🔥 vtpass_mock.py LOADED")


def generate_request_id(prefix="REQ"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def mock_purchase_response(success=True, product_name="", phone="", amount="0", variation_code=None):
    return {
        "code": "000" if success else "999",
        "response_description": "TRANSACTION SUCCESSFUL" if success else "TRANSACTION FAILED",
        "content": {
            "transactions": {
                "status": "delivered" if success else "failed",
                "product_name": product_name,
                "phone": phone,
                "amount": str(amount),
                "variation_code": variation_code,
                "transactionId": generate_request_id("MOCK"),
            }
        }
    }


def purchase_airtime(network, phone, amount, simulate_success=True):
    print(f"🧪 MOCK AIRTIME >> {network} | {phone} | {amount}")

    request_data = {
        "network": network,
        "phone": phone,
        "amount": str(amount),
    }

    response_data = mock_purchase_response(
        success=simulate_success,
        product_name=f"{network.upper()} Airtime VTU",
        phone=phone,
        amount=amount
    )

    print("📦 Logging airtime provider request...")

    try:
        log = ProviderLog.objects.create(
            service_type="airtime",
            request_payload=request_data,
            response_payload=response_data,
            status_code=200
        )
        print(f"✅ Airtime log saved. Log ID: {log.id}")
    except Exception as e:
        print("❌ Airtime log error:", str(e))

    return response_data


def purchase_data(network, phone, plan_code, simulate_success=True):
    print(f"🧪 MOCK DATA >> {network} | {phone} | {plan_code}")

    request_data = {
        "network": network,
        "phone": phone,
        "plan_code": plan_code,
    }

    response_data = mock_purchase_response(
        success=simulate_success,
        product_name=f"{network.upper()} Data Plan {plan_code}",
        phone=phone,
        amount="variable",
        variation_code=plan_code
    )

    print("📦 Logging data provider request...")

    try:
        log = ProviderLog.objects.create(
            service_type="data",
            request_payload=request_data,
            response_payload=response_data,
            status_code=200
        )
        print(f"✅ Data log saved. Log ID: {log.id}")
    except Exception as e:
        print("❌ Data log error:", str(e))

    return response_data
