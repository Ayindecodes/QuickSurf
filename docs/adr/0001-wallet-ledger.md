# 1. Wallet ledger

- Status: accepted
- Date: 2026-10-23

## Context

Wallet balances were changed directly by five different pieces of code (purchase views, the
status endpoint, the `requery_pending` command, Paystack crediting and the lock/unlock
endpoints), each with its own rules. Two of them disagreed about what `locked` means, and top-ups,
purchases and refunds left no record. That made it impossible to tell where a wallet's money came
from, and it allowed bugs such as one payment being credited twice.

## Decision

1. **Meaning of the two fields**
   - `Wallet.balance` is money the user can spend now.
   - `Wallet.locked` is money held for purchases still waiting on VTpass.
   - The two never overlap. A wallet's total is `balance + locked`.
2. **Every change writes one ledger row** (`wallets.Transaction`), recording the signed
   `balance_delta` / `locked_delta` and the values right after (`balance_after` / `locked_after`).
   Rows are never edited or deleted. A wallet must always equal the sum of its ledger deltas.
   `manage.py check_wallets` verifies this.
3. **Only `wallets/services.py` changes balances:**

   | Entry | When | balance | locked |
   |---|---|---|---|
   | `topup` | Paystack payment credited | + | |
   | `hold` | purchase started | − | + |
   | `capture` | purchase delivered | | − |
   | `release` | purchase failed (refund) | + | − |
   | `adjustment` | staff correction, with a note and the staff user | ± | |
   | `opening` | balance carried over when the ledger started | ± | ± |

4. **Idempotency comes from the database, not from status flags.**
   - The pair `(type, reference)` is unique for `opening`, `topup`, `hold`, `capture` and
     `release`, so a Paystack reference can be credited once and a purchase can be held once.
   - A purchase's hold can be captured or released, but only once and never both.
5. **Purchases are settled by `services/settlement.py`.** `successful` and `failed` are final:
   later provider answers don't change them or move money again.
6. **Users can't lock or unlock money themselves.** Staff changes go through adjustments.

## Consequences

- Balances can be explained and reconciled line by line, and double credits are blocked by a
  unique constraint.
- The migration opens the ledger from current balances, with an `opening` row per wallet and a
  `hold` row per pending purchase. Earlier history stays as it was; rows from before the ledger
  have types `credit`/`debit`/`lock`/`unlock`/`adjust` and zero deltas.
- Some wallets have `locked` money that no pending purchase explains. It was left by the old
  `/wallet/lock` endpoint or the old `requery_pending` refund, both of which added to `locked`
  without taking from `balance`, so it was never real. `manage.py clear_stale_locked` lists it,
  and `--apply` removes it with an adjustment entry. It is removed, not refunded.
- New code that needs to move money must add a function to `wallets/services.py`, not write
  `Wallet.balance` directly.
