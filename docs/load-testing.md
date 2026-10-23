# Load testing the purchase path

`scripts/load_test.py` puts the purchase path under load and then checks that no money went wrong. It
runs three phases:

| Phase | What it does | What must hold |
|---|---|---|
| steady | Many users buy ₦100 airtime repeatedly, all at once | Every purchase is answered; latency stays reasonable |
| same-ref | Each user sends one order 5 times simultaneously | Charged exactly once per order |
| overspend | Each user sends more orders at once than their balance covers | Never below zero; the rest refused with `insufficient_funds` |

Afterwards, every wallet must:
- not be negative;
- have nothing left on hold;
- equal the sum of its history.

Then `manage.py check_wallets` checks every wallet against its ledger.

## Results (2026-10-24)

Setup:
- the API on gunicorn with Postgres 16 and Redis 7 (production settings, `DEBUG=False`);
- a stand-in VTpass that takes **1.5 s** to answer each purchase (VTpass is usually 1–3 s);
- 40 users with ₦2,000 each.

| gunicorn | steady (400 purchases) | same-ref (40 orders × 5) | overspend (560 orders) | Wallets |
|---|---|---|---|---|
| 2 workers × 4 threads (default) | 4.6/s, median 4.3 s, p95 7.4 s | 40 charged once | 360 succeeded (the exact cap), 199 refused | all consistent |
| 2 workers × 8 threads | 8.6/s, median 3.2–3.7 s, p95 5.4–5.7 s | 40 charged once | 360 succeeded, 200 refused | all consistent |

What it shows:
- **Capacity is threads ÷ VTpass latency.** A purchase holds a thread while VTpass answers, so 8 threads
  at 1.5 s top out near 5 purchases/s. `GUNICORN_THREADS=8` nearly doubles that.
  - Even 5/s is 18,000 purchases an hour, far beyond launch traffic.
  - Raise threads before raising instances, and watch the database connection count: one connection
    per thread.
- **No money errors under concurrency:**
  - duplicate submissions were charged once;
  - concurrent orders never overdrew a wallet;
  - every ledger balanced.
- **A dropped connection about once per 1,000 requests, now fixed.** It came from gunicorn's default
  2-second keep-alive closing a connection just as it was reused. The request never reached the app, so
  no money moved.
  - `gunicorn.conf.py` now keeps idle connections for 75 s (`GUNICORN_KEEPALIVE`), so the proxy in front
    closes them first.
  - After the change: 0 dropped connections in 2,320 requests.

## Running it

**Never against production.** It creates users with money and buys airtime.

### Locally, with a stand-in VTpass (recommended)

```bash
docker network create qs-load
docker run -d --name qs-load-db --network qs-load -e POSTGRES_PASSWORD=test -e POSTGRES_DB=quicksurf postgres:16-alpine
docker run -d --name qs-load-redis --network qs-load redis:7-alpine
# a stand-in VTpass on qs-load-fake:9010 that answers /vtpass/pay after a delay
# (e.g. the fake_providers.py used in development)
docker run -d --name qs-load-api --network qs-load -v "$PWD:/app:ro" -w /app \
  -e ENV=development -e DEBUG=False -e ALLOWED_HOSTS=qs-load-api -e SECRET_KEY=<50+ random chars> \
  -e DATABASE_URL=postgres://postgres:test@qs-load-db:5432/quicksurf -e REDIS_URL=redis://qs-load-redis:6379/0 \
  -e PROVIDER_MODE=SANDBOX -e VTPASS_BASE_URL=http://qs-load-fake:9010/vtpass \
  -e PURCHASE_RATE=100000/hour -e DRF_USER_THROTTLE=100000/day \
  <image with requirements.txt> sh -c "python manage.py migrate && gunicorn quicksurf.wsgi:application"

docker exec qs-load-api python manage.py loadtest_users --count 40 --output /tmp/users.json
docker exec qs-load-api cat /tmp/users.json > users.json
python scripts/load_test.py http://qs-load-api:8000 users.json --per-user 10 --concurrency 20   # from a container on qs-load
```

- **Rate limits:** raise `PURCHASE_RATE`, or the per-user limit (30 an hour) answers `429` and hides
  the result.
- **`loadtest_users` refuses to run** with `ENV=production` or `PROVIDER_MODE=LIVE`.

### On staging

Staging uses the real VTpass sandbox, so keep runs small, for example
`--count 5 --per-user 3 --concurrency 5`. Its purpose is to check the deployed setup (proxy, Redis,
database), not to measure capacity.
