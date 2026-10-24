# Architecture

QuickSurf sells Nigerian airtime and data from a prepaid naira wallet. This page is the map. The decision
records in [`docs/adr/`](adr/) explain the choices, and the [runbooks](runbooks/README.md) cover operations.

## The pieces

```
 Browser ── HTTPS ──▶ Frontend (Next.js on Vercel, repo: quicksurf-frontend)
    │
    └──── HTTPS, Bearer JWT ──▶ API (Django + DRF on Render, gunicorn) ──▶ PostgreSQL
                                   │      │                               Redis (locks, rate limits, cache)
                                   │      ├──▶ Paystack  (top-ups: initialize, verify; webhooks in)
                                   │      └──▶ VTpass    (airtime/data: pay, requery, plans; callbacks in)
                                   └── Scheduled jobs (settling, alerts, reports, email queue)
```

- **The frontend** calls the API from the browser. It holds no secrets: tokens live in browser storage,
  protected by a strict Content-Security-Policy (see the frontend README).
- **Hosting:** the API runs either self-hosted on one server (`deploy/`: Caddy, Docker Compose, supercronic
  for the scheduled jobs, nightly encrypted backups) or on Render. See [DEPLOYMENT.md](../DEPLOYMENT.md).
- **The API** is the only thing that touches money, providers or the database. It runs on gunicorn with
  threads, because purchases spend most of their time waiting on VTpass.
- **PostgreSQL** is required, not just preferred. Row locks (`select_for_update`) keep wallets correct
  under concurrency, and SQLite ignores them.
- **Redis** holds purchase locks, rate-limit counters and cached data plans. Without it each process
  has its own, and production warns at startup.

## Django apps

| App | Owns |
|---|---|
| `users` | Email accounts, JWT login, email verification and password-reset codes, transaction PIN, profile, account closure (`security.py`) |
| `wallets` | `Wallet`, the append-only ledger (`Transaction`), **`services.py`: the only code that changes balances**, history and statements |
| `payments` | Paystack top-ups: `PaymentIntent`, initialize, verify, webhook, reconciliation helpers |
| `services` | Airtime and data purchases, the VTpass client (`vtpass.py`), settlement (`settlement.py`), plans and pricing, receipts, saved numbers, VTpass callbacks |
| `rewards` | Loyalty points ledger, redemption into the wallet, clawback on reversal |
| `notifications` | Email log and the queued receipt emails |
| `core` | Request IDs and JSON logs, throttling, error handling, alerts, health check, money audits, ops commands |

## How money moves

Every naira is a row in `wallets.Transaction`, written by `wallets/services.py` while the wallet row is
locked ([ADR 1](adr/0001-wallet-ledger.md)). `Wallet.balance` is spendable money, and `Wallet.locked` is
money held for purchases waiting on VTpass.

**Top-up**
1. `POST /payments/init/` creates a `PaymentIntent` and asks Paystack for a checkout URL.
2. The user pays on Paystack.
3. The money is credited when either the browser calls `GET /payments/verify/<ref>/` or Paystack's
   signed webhook arrives, whichever comes first.
4. It is credited **once**, and only if Paystack charged exactly the amount and currency asked for.
   Otherwise the intent goes to `review` for a person to look at.
5. `verify_pending_topups` catches payments where neither the browser nor the webhook came back.

**Purchase**
1. `POST /services/airtime/` or `/data/` checks the PIN, the limits and the price. The data price always
   comes from VTpass's live plan list, never from the client.
2. Under a lock it **holds** the amount and creates the purchase with a fresh VTpass request ID.
3. It calls VTpass `/pay` **once**.
4. The answer is mapped by VTpass's documented codes ([ADR 3](adr/0003-unknown-outcomes-stay-pending.md)):

   | VTpass answer | Purchase | Money |
   |---|---|---|
   | delivered | `successful` | captured |
   | failed | `failed` | released (refunded) |
   | anything unclear | `pending` | stays held |

5. Pending purchases are settled by a requery: from the status endpoint, VTpass's callback (which only
   triggers a requery), or the `requery_pending` cron. If VTpass later reverses a delivered purchase,
   it is refunded (`reversal`) and its points are taken back.

**The same order never charges twice.** Purchases are idempotent on `client_reference`, enforced
three ways:
- a Redis lock;
- a unique column;
- a re-check inside the wallet lock.

The load test sends the same order five times at once to check this ([load-testing.md](load-testing.md)).

## API

- Everything is served under `/api/v1/` ([ADR 2](adr/0002-versioned-api-and-error-envelope.md)). Errors
  share one envelope with a machine-readable `code`.
- The frontend guide is [API.md](API.md). The full reference is generated at `/api/docs/`.
- Auth uses JWTs. Refresh tokens rotate and are blacklisted after use. Staff use the Django admin with
  enforced TOTP.

## Scheduled jobs (`deploy/crontab`, or Render cron)

| Job | Why it exists |
|---|---|
| `requery_pending` | Settles purchases VTpass hasn't answered clearly |
| `verify_pending_topups` | Credits payments whose browser and webhook both went missing |
| `send_queued_emails` | Sends receipts outside the purchase request |
| `ops_alerts` | Emails staff about stuck purchases, failure spikes, low VTpass balance, ledger mismatches, rejected webhooks |
| `daily_report` | Daily totals and a two-way Paystack reconciliation |
| `check_wallets` | Proves every wallet equals its ledger |
| `vtpass_balance` | Warns before our VTpass float runs out |
| `purge_old_logs` | Deletes provider/email/login logs after 180 days |

## Security model

- **Production refuses to start when unsafe.** That covers DEBUG on, a weak `SECRET_KEY`, sandbox
  VTpass with LIVE mode, and a non-live Paystack key.
- **Paystack webhooks** must carry a valid HMAC signature and fail closed without a secret. **VTpass
  callbacks** are unsigned, so they are only a hint to requery.
- **Every purchase needs the user's PIN.** PINs and emailed codes are stored as keyed HMACs.
- **Rate limits** apply per IP, per email and per user. The client IP comes from `CF-Connecting-IP` or
  the proxy chain (`core/net.py`).
- **Logs** mask phone numbers and emails and never include secrets or request bodies (`core/privacy.py`).
  Every response carries an `X-Request-ID`.
