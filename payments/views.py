import hmac
import hashlib
import json
import requests
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponseForbidden
from django.db import transaction
from wallets.models import Wallet
from .models import PaystackPayment


@csrf_exempt
def paystack_webhook(request):
    """Handle Paystack webhook events securely."""
    # Step 1: Verify HMAC signature
    secret = settings.PAYSTACK_SECRET_KEY.encode()
    signature = request.headers.get("X-Paystack-Signature", "")
    payload = request.body
    expected = hmac.new(secret, payload, hashlib.sha512).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return HttpResponseForbidden("Invalid signature")

    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return HttpResponseForbidden("Invalid payload")

    # Step 2: Process only successful charges
    if event.get("event") == "charge.success":
        data = event.get("data", {})
        reference = data.get("reference")
        amount = int(data.get("amount", 0)) / 100  # kobo → naira

        if not reference:
            return JsonResponse({"error": "Missing reference"}, status=400)

        # Step 3: Verify with Paystack API
        verify_url = f"https://api.paystack.co/transaction/verify/{reference}"
        headers = {"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"}
        verify_resp = requests.get(verify_url, headers=headers, timeout=10)
        verify_json = verify_resp.json()

        if not verify_json.get("status") or verify_json["data"]["status"] != "success":
            return JsonResponse({"error": "Verification failed"}, status=400)

        # Step 4: Credit user atomically
        with transaction.atomic():
            payment, created = PaystackPayment.objects.select_for_update().get_or_create(
                reference=reference,
                defaults={
                    "user": None,  # Optional: Fill if you link payments to users earlier
                    "amount": amount,
                    "status": "success",
                    "raw_response": data
                }
            )

            if not created and payment.status == "success":
                return JsonResponse({"status": "already processed"})

            payment.status = "success"
            payment.raw_response = data
            payment.save()

            if payment.user:
                wallet = Wallet.objects.select_for_update().get(user=payment.user)
                wallet.balance += amount
                wallet.save()

        return JsonResponse({"status": "success"})

    return JsonResponse({"status": "ignored"})
