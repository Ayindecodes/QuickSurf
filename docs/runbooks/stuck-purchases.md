# Purchases stuck "pending"

**Sign:** the `stuck_purchases` alert ("N purchase(s) pending for over 30 min").

A purchase is **pending** when VTpass hasn't given a clear answer (a timeout, "processing", or an unknown
code). The customer's money is **held**, not lost. It's settled once VTpass answers: captured if
delivered, refunded if failed. **Never refund a pending purchase by hand**: VTpass may still deliver it,
and the money would leave twice.

## Steps

1. **Ask VTpass again:**
   ```bash
   python manage.py requery_pending --age-mins 0
   ```
   Or, in the admin, select the purchases under Airtime/Data transactions and run **Re-query provider status**.
2. **Still pending?** Look the purchase up in the VTpass dashboard by its **VTpass request ID**
   (`provider_request_id`, which starts with the date).
   - **Delivered** at VTpass: the next re-query will capture it.
   - **Failed or reversed** at VTpass: the next re-query refunds it.
   - **VTpass has no record of it:** re-query answers `015` and the purchase is refunded automatically.
   - **VTpass says to contact them (`044`, `083`):** contact VTpass support with the request ID.
3. **Is it the customer asking?** The money is held and they'll get either the airtime or a refund. Give
   them the purchase's `client_reference`.

## Why it happens

- **VTpass is slow or down.** See [vtpass-outage.md](vtpass-outage.md).
- **The `requery_pending` cron isn't running.** Check Render cron jobs.
- **The VTpass callback URL isn't set** in the VTpass dashboard
  (`https://<host>/api/services/vtpass/callback/`). With it set, purchases usually settle within seconds.
