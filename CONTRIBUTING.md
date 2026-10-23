# Contributing

QuickSurf moves real money, so the rules below are stricter than usual. Read
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/adr/](docs/adr/) first.

## Set up

```bash
cp .env.example .env              # sandbox keys only
docker compose up                 # API on :8000, Postgres and Redis included
docker compose run --rm web python manage.py test
pip install -r requirements-dev.txt   # ruff and coverage, if you work outside Docker
```

## Making a change

1. Branch from `main` (`feature/…`, `fix/…`). Merging `main` deploys to production.
2. Keep pull requests small and about one thing. Fill in the template.
3. Before pushing:

   ```bash
   ruff check . && ruff format .
   python manage.py makemigrations --check --dry-run
   coverage run manage.py test && coverage report
   ```

4. CI must be green. It checks:
   - lint and formatting;
   - that migrations are up to date;
   - `check --deploy` against production settings;
   - the tests on Postgres and Redis;
   - coverage of 85% overall and on the money code;
   - `pip-audit`.

## Rules for money code

- **Only `wallets/services.py` changes `Wallet.balance` or `Wallet.locked`,** and every change writes a
  ledger row. Never update a wallet any other way, including in migrations, the admin or the shell. Use
  `wallets.services.adjust` with a note.
- **Use `Decimal` for money, never `float`.** Amounts are quantized to two places. Paystack uses kobo
  (integers).
- **Never call VTpass `/pay` twice for one purchase,** and never treat an unclear answer as a failure
  ([ADR 3](docs/adr/0003-unknown-outcomes-stay-pending.md)).
- **Never credit a top-up the verified Paystack data doesn't support:** amount, currency and reference
  must match.
- **Lock the wallet** (`select_for_update`) before reading a balance you are about to act on.
- **Every money path needs tests,** including the failure and retry cases. The coverage gate for these
  modules is listed in `.github/workflows/ci.yml`.

## Rules for migrations

- **Additive only:** new tables, new nullable or defaulted columns, new constraints that existing data
  satisfies. Old code must keep working against the new schema, so a deploy can be rolled back.
- **Removing or renaming takes two releases:** first stop using the column, then drop it.
- **Data migrations** need a reverse function, or a comment saying why there isn't one. Test them against
  a copy of production-like data.
- **Never edit a migration** that has been merged.

## API changes

- **Keep `/api/v1/` backwards compatible:** add fields, never remove or repurpose them.
- **Every new error** needs a stable `code` ([ADR 2](docs/adr/0002-versioned-api-and-error-envelope.md))
  and a line in `docs/API.md`.
- **Update the frontend repo** if it should use the change.

## Security

- **Never commit secrets.** `.env` is ignored. If a secret leaks, rotate it
  ([runbooks/key-rotation.md](docs/runbooks/key-rotation.md)); deleting the commit is not enough.
- **Don't log request bodies, PINs, codes, tokens or full phone numbers.** `core/privacy.py` masks what
  it can.
- **Tests and scripts use sandbox or fake providers.** Nothing in a test may reach a real provider.

## Docs to keep current

| Change | Update |
|---|---|
| New setting | `.env.production.example`, `DEPLOYMENT.md` |
| New cron job | `DEPLOYMENT.md`, `README.md`, `docs/runbooks/README.md` |
| API change | `docs/API.md` |
| Something on-call needs to know | a runbook |
| A significant design choice | a new ADR in `docs/adr/` |
