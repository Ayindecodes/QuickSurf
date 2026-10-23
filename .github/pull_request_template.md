## What and why

<!-- What changes for users or operators, and why. Link the issue if there is one. -->

## Checklist

- [ ] Tests cover the change, including failure/retry paths for anything touching money
- [ ] `ruff check . && ruff format .` and `makemigrations --check` pass
- [ ] Migrations are additive (old code still works against the new schema), or the PR explains the two-step plan
- [ ] Balances only change through `wallets/services.py`; no float money
- [ ] New errors have a `code` and are in `docs/API.md`; new settings are in `.env.production.example` and `DEPLOYMENT.md`
- [ ] No secrets, PINs, tokens or full phone numbers in code, logs or fixtures

## How it was tested

<!-- Commands run, sandbox checks done, screenshots for anything user-facing. -->
