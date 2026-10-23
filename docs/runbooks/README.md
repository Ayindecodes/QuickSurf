# Runbooks

What to do when something goes wrong. Alerts and the daily report link to the page that applies.

| Situation | Runbook |
|---|---|
| Purchases failing, a spike of failures, low VTpass balance | [vtpass-outage.md](vtpass-outage.md) |
| Purchases stuck "pending" | [stuck-purchases.md](stuck-purchases.md) |
| Top-ups not credited, Paystack errors, rejected webhooks | [paystack-outage.md](paystack-outage.md) |
| Wallet/ledger mismatch, top-up "review", suspected double credit | [money-mismatch.md](money-mismatch.md) |
| Rotating keys or secrets | [key-rotation.md](key-rotation.md) |
| A bad deploy | [rollback.md](rollback.md) |
| Backups, restoring the database | [backups.md](backups.md) |

## First moves in any incident

1. **Stop the bleeding if money could be lost.** Set `PURCHASES_ENABLED=False` and/or `TOPUPS_ENABLED=False`
   in the Render environment. Payments already made are still credited.
2. **Find the request.** Errors shown to customers include a `request_id`. Search the logs for it: in
   production each log line is JSON with a `request_id` field.
3. **Check health.** `GET /api/health/` reports the database and cache.
4. **Afterwards,** turn the kill switches back on, run `python manage.py check_wallets`, and note what happened.

## Monitoring setup (one-time)

- **Uptime:** point an uptime monitor (UptimeRobot, Better Stack, ...) at `https://<host>/api/health/`, alerting
  when it isn't `200`.
- **Errors:** set `SENTRY_DSN` to send errors and `ERROR` log lines to Sentry. Request bodies are never sent.
- **Alerts:** set `OPS_ALERT_EMAILS` (comma-separated). It needs working SMTP settings.
- **Scheduled jobs:** see the cron table in [DEPLOYMENT.md](../../DEPLOYMENT.md#render-backend).

## Where to look

- **Admin** (`/<ADMIN_URL>`):
  - **Users** (search by email): wallet, verification, and actions to unlock the PIN, sign out
    everywhere, or mark the email verified.
  - **Airtime/Data transactions** (search by reference, phone, VTpass request ID): has a re-query action.
  - **Payment intents** (search by reference): has a re-verify action.
  - **Wallet ledger** (search by reference): every naira movement.
  - **Provider logs:** what VTpass/Paystack said.
  - **Login activity:** sign-ins by IP and device.
- Every admin action is recorded in that object's **History**.
