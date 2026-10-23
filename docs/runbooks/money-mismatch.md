# Money mismatches

How money moves: [docs/adr/0001-wallet-ledger.md](../adr/0001-wallet-ledger.md). Corrections are always
**staff adjustments**, made by someone in the Finance role: `POST /api/wallet/adjust/` with
`{user, amount (±), note}`. Never edit balances directly. The Wallet admin is read-only on purpose.

## `ledger_mismatch` alert / `check_wallets` fails

A wallet no longer equals the sum of its ledger, so something changed a balance outside `wallets/services.py`.

1. Run `python manage.py check_wallets` to list the wallets and the difference.
2. Open the wallet's ledger in the admin (Wallet ledger, search the user's email) and the admin
   **History** for that user.
3. Find the cause: a database edit, a script, or a code path that skipped the ledger. Fix the cause first.
4. Correct the balance with a staff adjustment whose note explains the mismatch.

## Top-up in "review"

Paystack charged a different amount or currency than the top-up asked for, so it wasn't credited.

1. Check the payment in the Paystack dashboard.
2. If the customer really paid, credit what they actually paid with a staff adjustment. Use the note to
   reference the Paystack reference. If not, leave it.

## Suspected double credit, or money from the old code

- `python manage.py audit_money` is a read-only report. It covers wallets that don't match their history,
  underpriced data purchases, payments the old code credited twice, and refunds where VTpass's outcome was
  unknown.
- `python manage.py clear_stale_locked` lists `locked` money that no pending purchase explains, left by the
  old code. It was never real money, so `--apply` removes it rather than refunding it.

## Daily report: "credited but Paystack shows no successful payment"

Treat it as serious. Credits require Paystack's verification or a correctly signed webhook.

1. Pause top-ups (`TOPUPS_ENABLED=False`) if there's more than one.
2. Check the reference in the Paystack dashboard and in the Provider logs.
3. Reverse it with a negative staff adjustment if it wasn't paid.
