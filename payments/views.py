import hmac, hashlib, json
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponseForbidden
from wallets.models import Wallet
from users.models import User
from .models import PaystackPayment


@csrf_exempt
def paystack_webhook(request):
    secret = settings.PAYSTACK_SECRET_KEY.encode()
    signature = request.headers.get("X-Paystack-Signature")

    payload = request.body
    expected = hmac.new(secret, payload, hashlib.sha512).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return HttpResponseForbidden("Invalid signature")

    event = json.loads(payload)
    if event.get("event") == "charge.success":
        data = event["data"]
        reference = data["reference"]
        amount = int(data["amount"]) / 100  # Paystack sends in kobo

        payment = PaystackPayment.objects.filter(reference=reference).first()
        if payment and payment.status != "success":
            payment.status = "success"
            payment.raw_response = data
            payment.save()

            wallet = Wallet.objects.get(user=payment.user)
            wallet.balance += amount
            wallet.save()

    return JsonResponse({"status": "ok"})
