# Quicksurf Backend Deployment Checklist

## 1) Secrets and environment
- Rotate all currently exposed live keys before deployment.
- Set every value from `.env.production.example` in the Render environment (never commit them).

## 2) Mandatory production settings
The app refuses to start if any of these are missing or unsafe:
- `ENV=production`, `SECRET_KEY`, `DATABASE_URL`
- `PROVIDER_MODE=LIVE` (explicit), with `VTPASS_API_KEY`, `VTPASS_PUBLIC_KEY`, `VTPASS_SECRET_KEY`,
  and a non-sandbox `VTPASS_BASE_URL`
- `PAYSTACK_SECRET_KEY` starting with `sk_live_`

Also set:
- `DEBUG=False`, `DB_SSL_REQUIRE=True`
- `REDIS_URL`: purchase locks and rate limits are per-process without it
- `SECRET_KEY` of at least 50 random characters (production refuses shorter)
- SMTP (`EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`): verification and reset codes are emailed
- `ADMIN_URL`: something unguessable. `STAFF_MFA_REQUIRED` is on by default in production.
- `TRANSACTION_PIN_REQUIRED=False` **only** if the live frontend has no PIN screen yet

Then run `python manage.py check --deploy`.

## 3) Render service
- **Build command:** `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate`
- **Start command:** `gunicorn quicksurf.wsgi:application`. Port, workers and the 90s timeout come from `gunicorn.conf.py`.
- **Cron jobs** (see README "Operations"):
  - `ops_alerts` (every 15 min)
  - `requery_pending`
  - `daily_report` (daily)
  - `purge_old_logs --apply` (weekly)
  - `verify_pending_topups`
  - `send_queued_emails` (every 2 min: receipts wait for it)
  - `vtpass_balance --warn-below 10000`
  - `check_wallets`
- **Callbacks:**
  - Paystack webhook → `https://<host>/api/payments/webhook/`
  - VTpass callback → `https://<host>/api/services/vtpass/callback/`

## 4) One-time setup
- Set `OPS_ALERT_EMAILS` (and optionally `SENTRY_DSN`), and point an uptime monitor at `/api/health/` (see docs/runbooks/README.md).
- `python manage.py setup_staff_roles`, then put staff in the Support or Finance group.
- Each staff member signs in at `/<ADMIN_URL>` and sets up an authenticator app.

## 5) Live endpoint smoke tests
- Health:
  - `GET /api/health/` -> expect `200` with `"database": "ok"` and `"cache": "ok"`
- Auth:
  - `POST /api/users/register/` -> expect `201`, `access` + `refresh`
  - `POST /api/users/login/` -> expect `200`, `access` + `refresh`
- Wallet:
  - `GET /api/wallet/` with Bearer token -> expect `200`
  - `GET /api/wallet/transactions/` with Bearer token -> expect `200` (ledger entries)
- Services:
  - `GET /api/services/plans/mtn/` -> expect `200` with plans and prices
  - `POST /api/services/airtime/` with a small amount (minimum ₦50) -> expect `201` or a controlled `4xx/5xx` with JSON
  - `POST /api/services/data/` with a plan from `/plans/` -> expect `201` or a controlled `4xx/5xx` with JSON
- Payments:
  - `POST /api/payments/init/` -> expect `201` with `reference` + `authorization_url`
  - `GET /api/payments/verify/{reference}/` -> expect JSON status
  - Trigger a Paystack webhook -> expect `200` and the wallet credited once only

## 6) Post-deploy verification
- Confirm the logs show `BOOT ENV=production` and `PROVIDER_MODE=LIVE`.
- Run `python manage.py check_wallets` -> expect every wallet to match its ledger.
- Run `python manage.py vtpass_balance` -> expect the VTpass wallet balance.
- Confirm no repeated provider debits for the same `client_reference`.
- Confirm rewards and receipt email logs are created once per successful purchase.
