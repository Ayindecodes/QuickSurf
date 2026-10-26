# Deploying QuickSurf

QuickSurf has two parts: this API, and the frontend
([quicksurf-frontend](https://github.com/Ayindecodes/quicksurf-frontend)) on **Vercel**.

The API can run in either of two places:

| Option | How it deploys | Guide |
|---|---|---|
| **A rented server (VPS)** | You run `deploy/deploy.sh deploy`. Caddy, Postgres, Redis, the scheduled jobs and encrypted backups run in Docker Compose | [deploy/README.md](deploy/README.md) |
| **Render** | Merging `main` deploys it. Render runs the database, Redis and cron jobs | The "Render" section below |

Everything else on this page applies to both. For the first launch, follow [docs/LAUNCH.md](docs/LAUNCH.md).

## Environments

| | Backend | Providers | Frontend |
|---|---|---|---|
| Local | `docker compose up` (`.env` from `.env.example`) | VTpass sandbox, Paystack test | `npm run dev` |
| Staging | A second VPS stack or Render service with its own database, `ENV=staging`, `DEBUG=False` | `PROVIDER_MODE=SANDBOX`, VTpass sandbox keys, Paystack `sk_test_` | Vercel preview pointing at staging |
| Production | VPS (`deploy/`) or Render, `ENV=production` | `PROVIDER_MODE=LIVE`, live VTpass, Paystack `sk_live_` | Vercel production |

`ENV=production` insists on live keys, so staging uses another `ENV` value and sets `DEBUG=False` itself.

## Render (backend, if not self-hosting)

Put the shared variables in an **Environment Group** and attach it to the web service and every cron
job, so they can't drift apart.

**Web service**
- **Build:** `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate`
- **Start:** `gunicorn quicksurf.wsgi:application` (settings come from `gunicorn.conf.py`)
- **Health check path:** `/api/health/`
- **Capacity:**
  - `WEB_CONCURRENCY` (processes, default 2) × `GUNICORN_THREADS` (default 4) is how many requests run
    at once. A purchase holds one for as long as VTpass takes to answer.
  - The [load test](docs/load-testing.md) did about 5 purchases/s with 2×4 and about 8.5/s with 2×8,
    with VTpass answering in 1.5s.
  - Each thread keeps its own database connection, so stay under the database's connection limit.

**Database:** Render PostgreSQL on a **paid plan with backups**. Free databases expire and aren't
backed up. Practise a restore before launch ([runbooks/backups.md](docs/runbooks/backups.md)).

**Redis:** Render Key Value (Redis), with `REDIS_URL` set. Without it, purchase locks and rate limits
are per process.

**Cron jobs** (same build command, same environment group):

| Command | Schedule |
|---|---|
| `python manage.py requery_pending` | every 5 minutes |
| `python manage.py verify_pending_topups` | every 10 minutes |
| `python manage.py send_queued_emails` | every 2 minutes |
| `python manage.py ops_alerts` | every 15 minutes |
| `python manage.py vtpass_balance --warn-below 10000` | hourly |
| `python manage.py check_wallets` | daily |
| `python manage.py daily_report` | daily, early morning |
| `python manage.py purge_old_logs --apply` | weekly |

### Environment variables

Every variable is listed in `.env.production.example`. The ones that matter most:

| Variable | Notes |
|---|---|
| `ENV=production`, `DEBUG=False`, `SECRET_KEY` | Production refuses a `SECRET_KEY` under 50 characters |
| `DATABASE_URL`, `DB_SSL_REQUIRE=True`, `REDIS_URL` | |
| `ALLOWED_HOSTS` | The API's hostname |
| `CLIENT_IP_HEADER` | `CF-Connecting-IP` on Render; `X-Real-IP` when self-hosted (the compose file sets it) |
| `PROVIDER_MODE=LIVE`, `VTPASS_API_KEY`, `VTPASS_PUBLIC_KEY`, `VTPASS_SECRET_KEY` | Production refuses LIVE with sandbox URLs or missing keys |
| `PAYSTACK_SECRET_KEY` (`sk_live_…`), `PAYSTACK_PUBLIC_KEY`, `PAYSTACK_WEBHOOK_SECRET` | Webhooks fail closed without the secret |
| `FRONTEND_URL` | The frontend's origin. It is the only browser origin allowed (CORS) |
| `PAYSTACK_CALLBACK_URL` | `https://<frontend>/dashboard/topup/callback` |
| `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Codes and receipts are emailed |
| `ADMIN_URL` | Unguessable. Staff must use an authenticator app (`STAFF_MFA_REQUIRED`) |
| `OPS_ALERT_EMAILS`, `SENTRY_DSN` | Alerts and error tracking |
| `PURCHASES_ENABLED`, `TOPUPS_ENABLED` | Kill switches |
| Limits: `AIRTIME_MAX_AMOUNT`, `TOPUP_MAX_AMOUNT`, `DAILY_PURCHASE_LIMIT`, `NEW_ACCOUNT_DAILY_LIMIT`, `PURCHASE_RATE`, `TOPUP_RATE` | See the soft-launch values in LAUNCH.md |

### Provider dashboards
- **Paystack webhook:** `https://<api>/api/payments/webhook/`
- **VTpass callback:** `https://<api>/api/services/vtpass/callback/`. Also whitelist Render's outbound IPs
  if VTpass requires it.

## Vercel (frontend)

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_BASE` | `https://<api>` (no `/api`) |
| `NEXT_PUBLIC_SENTRY_DSN` | Optional |

Also enable Vercel Analytics and Speed Insights in the project. Preview deployments only work against an
API whose `FRONTEND_URL` allows the preview's origin, so point previews at staging.

## After every deploy

1. The logs show `BOOT ENV=production … PROVIDER_MODE=LIVE`.
2. `GET /api/health/` returns `200` with `"database": "ok"` and `"cache": "ok"`.
3. Run `python manage.py check_wallets`. Every wallet must match its ledger.
4. Run `python manage.py vtpass_balance`. It must show our VTpass float.
5. Smoke test in the browser:
   - log in;
   - check the balance and history;
   - load the data plans for one network;
   - on the first launch only, do one small real top-up and one ₦50 airtime purchase on a staff account.
6. Watch the logs and the `ops_alerts` email for 30 minutes.

Against **staging** (never production: it buys airtime),
`python scripts/smoke_test.py https://<staging-api> --email … --password … --i-confirm-this-is-sandbox`
checks health, login, PIN, plans, a top-up with a Paystack test card if the wallet is low, and a
sandbox airtime purchase with its ledger entries.

## Rolling back

See [runbooks/rollback.md](docs/runbooks/rollback.md). When self-hosting, `deploy/deploy.sh rollback` does
the code part. In short:
- use the kill switches first;
- roll back the code with Render's "Rollback";
- never roll the code back to before a migration that changed what old code relies on.
