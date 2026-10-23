# 3. Unclear provider outcomes stay pending

- Status: accepted
- Date: 2026-10-23

## Context

The old code treated anything that wasn't a clear success as a failure and refunded the user. It also
retried `/pay` on timeouts. VTpass can deliver airtime and still return a timeout, an unknown code or
`099 processing`. Refunding then gives the airtime away free. Re-sending `/pay` can deliver and charge
twice. Both happened as money losses.

## Decision

- **`/pay` is sent once per purchase,** with a request ID generated before the call (VTpass's format: a
  Lagos-time `YYYYMMDDHHII` prefix). Network errors are never retried for `/pay`. Safe GETs and
  requeries may be retried.
- **Outcomes follow VTpass's documented response codes** (`services/vtpass.py`):

  | Answer | Outcome |
  |---|---|
  | `000` with status `delivered` | `successful` |
  | `040` or status `reversed` | `reversed` |
  | Codes that mean VTpass won't deliver, or status `failed` | `failed` |
  | **Everything else** (timeouts, `099`, `014`, unknown codes, unreadable bodies) | **`pending`** |

- **A pending purchase keeps the money held** until a requery gives a definite answer. Requeries come
  from the user's status check (at most every 30 seconds), VTpass's callback, and the
  `requery_pending` cron.
- **VTpass callbacks aren't signed,** so their content is only a hint to requery. Money never moves on
  a callback's word alone.
- **A delivered purchase VTpass later reverses** is refunded once (`reversal` ledger entry), and its
  loyalty points are taken back.

## Consequences

- Some purchases sit in `pending` for minutes, so users see "Processing" and the app polls.
  `ops_alerts` flags any pending for over 30 minutes.
- Money is never refunded for a purchase that might have been delivered, and never charged twice for
  one order.
- Support can always answer "where is my money": it is captured, held, or back in the balance, and the
  ledger shows which.
