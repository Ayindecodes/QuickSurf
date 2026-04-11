# Quicksurf Backend Deployment Checklist

## 1) Secrets and environment
- Rotate all currently exposed live keys before deployment.
- Copy `.env.production.example` to `.env.production`.
- Fill all placeholder values.
- Keep `.env.production` private (it is gitignored).

## 2) Mandatory production checks
- Ensure:
  - `ENV=production`
  - `DEBUG=False`
  - `DB_SSL_REQUIRE=True`
  - `PROVIDER_MODE=LIVE`
  - `REDIS_URL` is set
- Run:
  - `.\.venv\Scripts\python.exe manage.py check`
  - `.\.venv\Scripts\python.exe manage.py check --deploy`

## 3) Database/static
- Run:
  - `.\.venv\Scripts\python.exe manage.py migrate`
  - `.\.venv\Scripts\python.exe manage.py collectstatic --noinput`

## 4) Live endpoint smoke tests
- Health:
  - `GET /api/health/` -> expect `200`
- Auth:
  - `POST /api/users/register/` -> expect `201`, `access` + `refresh`
  - `POST /api/users/login/` -> expect `200`, `access` + `refresh`
- Wallet:
  - `GET /api/wallet/` with Bearer token -> expect `200`
  - `POST /api/wallet/lock/` then `/unlock/` -> expect `201`
- Services:
  - `POST /api/services/airtime/` with a tiny amount -> expect `201` or controlled `4xx/5xx` with JSON
  - `POST /api/services/data/` with valid plan -> expect `201` or controlled `4xx/5xx` with JSON
- Payments:
  - `POST /api/payments/init/` -> expect `201` with `reference` + `authorization_url`
  - `GET /api/payments/verify/{reference}/` -> expect JSON status
  - Trigger Paystack webhook -> expect `200` and wallet credit once only

## 5) Post-deploy verification
- Confirm logs show:
  - `BOOT ENV=production`
  - `PROVIDER_MODE=LIVE`
- Confirm no repeated provider debits for same `client_reference`.
- Confirm rewards and receipt email logs are created once per successful purchase.

