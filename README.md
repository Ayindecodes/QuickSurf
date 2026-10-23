# QuickSurf API

Backend for buying Nigerian airtime and mobile data. Users top up a naira wallet with
**Paystack** and spend it on MTN, Glo, Airtel and 9mobile airtime or data, delivered by
**VTpass**. Django 5.2 + Django REST Framework, PostgreSQL, Redis.

API docs (when running): `/api/docs/` (Swagger) and `/api/redoc/`. The frontend guide is
[docs/API.md](docs/API.md). Use the `/api/v1/` paths: the unversioned `/api/` copies are kept for the
old frontend.

## Run it locally

You need Docker.

```bash
cp .env.example .env        # add VTpass sandbox and Paystack test keys
docker compose up           # API on http://localhost:8000 (migrations run on start)
docker compose run --rm web python manage.py createsuperuser
```

`.env.example` runs VTpass in **SANDBOX** mode (sandbox.vtpass.com). Outside production that is
also the default, so a laptop can't spend real money by accident.

## Tests

```bash
docker compose run --rm web python manage.py test
```

Tests run against Postgres (SQLite ignores the row locks the money code relies on) and never call
VTpass or Paystack.

## Development

```bash
pip install -r requirements-dev.txt       # or use the docker compose service
ruff check . && ruff format .             # lint and format (CI rejects unformatted code)
coverage run manage.py test && coverage report
```

GitHub Actions (`.github/workflows/ci.yml`) runs on every pull request and every push to `main`:
- lint and formatting;
- a check that migrations are up to date;
- Django's deploy checks against production settings;
- the tests on Postgres and Redis;
- coverage of at least 85%, overall and for the money code;
- `pip-audit`.

Dependabot opens weekly update PRs.

To check a staging deployment end to end (sandbox only: it buys airtime):

```bash
python scripts/smoke_test.py https://staging.example.com --email you@example.com --password '...' --i-confirm-this-is-sandbox
```

## How money moves

Read [docs/adr/0001-wallet-ledger.md](docs/adr/0001-wallet-ledger.md) before changing anything
that touches balances. In short:

- `Wallet.balance` is spendable money; `Wallet.locked` is held for purchases waiting on VTpass.
- Only `wallets/services.py` changes balances, and every change writes a ledger row
  (`wallets.Transaction`) with the signed change and the balance after it.
- A purchase **holds** its amount, then is **captured** (delivered), **released** (failed) or
  **reversed** (VTpass refunded it). Purchases are settled in `services/settlement.py`.
- Paystack top-ups are credited once per reference, and only if Paystack charged exactly the
  amount and currency asked for (`payments/services.py`).
- VTpass outcomes follow its documented response codes (`services/vtpass.py`). Anything
  unclear stays **pending**, with the money held, until a requery or VTpass callback settles it.

## Accounts and security

- **Passwords:** Django's strength rules apply. Emails are case-insensitive.
- **Email verification:** a 6-digit code is emailed at sign-up (`/api/users/verify-email/...`), and is
  needed before the first top-up. Accounts that existed before verification was added count as verified.
- **Password reset:** `/api/users/password-reset/request/` then `/confirm/` with the emailed code.
  Resetting signs the user out everywhere.
- **Tokens:** JWT access tokens last `JWT_ACCESS_MINUTES` (default 60). Refresh tokens rotate on use;
  `/api/users/logout/` revokes one, or all with `everywhere: true`.
- **Transaction PIN:** purchases need a 4-digit `pin`, set with `/api/users/pin/` (which needs the
  account password). Five wrong PINs lock purchases for 30 minutes.
- **Profile:** `/api/v1/users/me/` reads and updates names and phone, and `DELETE` with the password
  closes the account. Closing is refused while the wallet holds money; otherwise personal details are
  removed and the financial records kept.
- **Limits:** per-IP limits on login, sign-up and reset, and per email on login. Per user on purchases
  and top-ups. A lower daily cap applies to accounts under a day old.
- **Staff:**
  - sign in to the admin (at `ADMIN_URL`) through allauth, and must set up an authenticator app when
    `STAFF_MFA_REQUIRED` (on in production);
  - `python manage.py setup_staff_roles` creates the Support (read-only) and Finance (can adjust
    wallets) groups.

## Code map

| Path | What it does |
|---|---|
| `quicksurf/settings.py` | Configuration from environment variables; refuses unsafe production settings |
| `users/` | Email/password accounts, JWT login, registration |
| `wallets/` | Wallet, ledger, `services.py` (all balance changes), `check_wallets`, `clear_stale_locked` |
| `payments/` | Paystack init, verify, webhook (`views.py`), shared logic (`services.py`), `verify_pending_topups` |
| `services/` | Airtime/data purchases (`views.py`), VTpass client (`vtpass.py`), settlement, data plans, VTpass callback, `vtpass_balance` |
| `core/` | Throttling, `requery_pending`, `audit_money` |
| `rewards/` | Loyalty points: earned on successful purchases, taken back on reversals, redeemed into the wallet (`services.py`) |
| `notifications/` | Emails: codes are sent at once, receipts are queued for `send_queued_emails` |

## Operations

Run these on a schedule (Render cron jobs):

| Command | How often | What it does |
|---|---|---|
| `python manage.py ops_alerts` | every 15 min | Emails `OPS_ALERT_EMAILS` about stuck purchases, failure spikes, top-ups in review, low VTpass balance, ledger mismatches, rejected webhooks |
| `python manage.py requery_pending` | every 5 min | Settles purchases still pending with VTpass; logs ones stuck for over 30 min |
| `python manage.py send_queued_emails` | every 2 min | Sends queued receipt emails; retries a failed send up to 3 times |
| `python manage.py verify_pending_topups` | every 10 min | Credits paid Paystack top-ups that were never verified; abandons stale ones |
| `python manage.py vtpass_balance --warn-below 10000` | hourly | Fails (and logs an error) when our VTpass wallet runs low |
| `python manage.py check_wallets` | daily | Fails if any wallet doesn't equal the sum of its ledger |
| `python manage.py daily_report` | daily | Yesterday's money totals and a Paystack reconciliation, emailed to `OPS_ALERT_EMAILS` |
| `python manage.py purge_old_logs --apply` | weekly | Deletes logs older than 180 days and expired token records |

When something goes wrong, see the [runbooks](docs/runbooks/README.md). Every response has an `X-Request-ID`
header, and errors return it as `request_id`: search the logs for it.

One-off tools:

- `python manage.py audit_money [--csv]` is a read-only report of past money problems.
- `python manage.py clear_stale_locked [--apply]` removes locked money left by the old code.

Rewards: 1 point per ₦100 spent (`POINTS_PER_NAIRA`), worth `REWARD_POINT_VALUE` naira each, redeemable from
`REWARDS_MIN_REDEMPTION` points. Redemptions are wallet credits on the ledger (type `reward`).

Kill switches: set `PURCHASES_ENABLED=False` or `TOPUPS_ENABLED=False` to stop new sales or top-ups
without a code change. Payments already made are still credited.

## Deploying (Render)

- **Build command:** `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate`
- **Start command:** `gunicorn quicksurf.wsgi:application` (settings come from `gunicorn.conf.py`)
- **Environment:** see `.env.production.example`. Production refuses to start without `SECRET_KEY`,
  `DATABASE_URL`, an explicit `PROVIDER_MODE`, the VTpass keys for LIVE, and a live (`sk_live_`)
  Paystack key.
- **Callbacks:** set the Paystack webhook to `/api/payments/webhook/` and the VTpass callback to
  `/api/services/vtpass/callback/`.
- **After each deploy:** follow `DEPLOYMENT_CHECKLIST.md`.
