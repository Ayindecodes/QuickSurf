# Paystack: top-ups not credited or failing

## A customer paid but their wallet wasn't credited

1. Find the payment in the admin under **Payment intents** (search the reference, or the customer's email).
2. Check its status:
   - **pending / initialized:** run the **Re-verify with Paystack** action. If Paystack says it's paid, the
     wallet is credited (once).
   - **review:** Paystack charged a different amount or currency than requested, so it wasn't credited.
     See [money-mismatch.md](money-mismatch.md).
   - **failed / abandoned:** Paystack says it wasn't paid. Ask the customer for the Paystack receipt and
     check the Paystack dashboard.
3. `python manage.py verify_pending_topups` does step 2 for every pending top-up, and runs on a schedule.

## `bad_webhook_signatures` alert

Paystack's webhooks are being rejected. Almost always it's **`PAYSTACK_SECRET_KEY` on Render not matching the
secret key in the Paystack dashboard**, for example after rotating keys. Webhooks are signed with that key.

1. Compare the key on Render with Paystack dashboard → Settings → API Keys & Webhooks.
2. Fix it on Render. If `PAYSTACK_WEBHOOK_SECRET` is set, it must be the same secret key.
3. Run `python manage.py verify_pending_topups --min-age-mins 0` to credit anything missed meanwhile.

If the keys match, someone may be sending forged webhooks. They're rejected, so no money moves, but check
the logs for the source IPs (`Rejected a Paystack webhook with a bad signature from ...`).

## Paystack is down or failing

- **Starting top-ups fails** with 502 "Payment provider unavailable". If it lasts, set `TOPUPS_ENABLED=False`.
- **When Paystack recovers,** run `python manage.py verify_pending_topups --min-age-mins 0`.

## Daily report says "paid at Paystack but not credited"

Re-verify that reference (step 2 above). If it's still not credited, check whether the PaymentIntent exists.
If it doesn't, the payment wasn't started by QuickSurf, so contact the customer.
