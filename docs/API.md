# QuickSurf API guide (for the frontend)

The full reference is generated from the code: `/api/docs/` (Swagger) and `/api/redoc/`. This guide
covers the flows and the rules the reference can't show.

Use the **`/api/v1/`** paths. The unversioned `/api/` paths still work for the old frontend, but
their errors keep the old shapes and they will be removed once nothing uses them.

## Basics

- JSON in and out. Send `Authorization: Bearer <access token>` on everything except sign-up, login,
  token refresh and password reset.
- Amounts are naira strings with two decimals (`"1500.00"`). Send amounts as strings too.
- Dates are ISO 8601 with a timezone. Date filters take `YYYY-MM-DD` (Lagos time).
- Phone numbers can be sent as `0803…`, `+234803…` or `234803…`. They always come back as `0803…`.
- Lists with `page`/`page_size` return `{"count", "next", "previous", "results"}`. The page size is
  at most 100.

### Errors

Every `/api/v1/` error looks like this:

```json
{
  "error": {
    "code": "pin_invalid",
    "message": "Wrong PIN.",
    "attempts_left": 3
  },
  "request_id": "5f0c…"
}
```

- Show `message` to the user and use `code` in your logic. Some errors carry extra values next to
  the message, such as `attempts_left`, `locked_until` or `price`.
- Validation errors have code `invalid` and add
  `"fields": {"phone": ["Enter a Nigerian number like …"]}`. Show each message next to its field.
- `request_id` is also in the `X-Request-ID` header of every response. Show it on error screens
  ("quote this to support"): it finds the exact log lines.

Codes you will meet:

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `invalid` | Field errors (see `fields`) |
| 400 | `insufficient_funds`, `daily_limit` | The balance is too low; the daily purchase limit is reached |
| 400 | `price_changed`, `unknown_plan` | The data plan's price changed (the new one is in `price`); the plan no longer exists |
| 400 | `redemption_refused`, `invalid_range` | See Rewards and History |
| 401 | `not_authenticated`, `authentication_failed`, `token_not_valid` | Refresh the access token, or sign in again |
| 403 | `email_unverified` | The email must be verified before the first top-up |
| 403 | `pin_not_set`, `pin_invalid`, `pin_locked` | See PIN |
| 403 | `permission_denied` | Not allowed |
| 404 | `not_found` | Unknown purchase, reference or plan |
| 409 | `reference_in_use`, `account_not_empty` | The `client_reference` belongs to another purchase; the account still holds money |
| 429 | `throttled`, `purchase_in_progress` | Too many requests; a purchase for this number is already going through. Wait and retry |
| 500 | `server_error` | Our fault. Show the `request_id` |
| 502/503 | `provider_error`, `unavailable`, `plans_unavailable` | VTpass or Paystack trouble. Retry later |
| 503 | `purchases_paused`, `topups_paused` | Sales or top-ups are switched off for now |

## Accounts

| Step | Call |
|---|---|
| Sign up | `POST /api/v1/users/register/ {email, password}` → `{access, refresh, email_verification_sent}` |
| Sign in | `POST /api/v1/users/login/ {email, password}` → `{access, refresh}`. You can send an `X-Device-Id` header |
| Refresh | `POST /api/v1/token/refresh/ {refresh}` → `{access, refresh}` |
| Sign out | `POST /api/v1/users/logout/ {refresh}`, or `{refresh, everywhere: true}` to sign out of all devices |
| Verify email | `POST /api/v1/users/verify-email/request/`, then `…/verify-email/confirm/ {code}` |
| Forgot password | `POST /api/v1/users/password-reset/request/ {email}`, then `…/confirm/ {email, code, new_password}` |
| Profile | `GET /api/v1/users/me/`, `PATCH /api/v1/users/me/ {first_name, last_name, phone}` |
| Change password | `POST /api/v1/users/me/password/ {current_password, new_password}` → new `{access, refresh}` |
| Close account | `DELETE /api/v1/users/me/ {password}` → 204 |

How sign-in works:

- **Refresh tokens rotate.** Every refresh returns a new refresh token, and the old one stops
  working, so always store the new one.
- **Access tokens** last `JWT_ACCESS_MINUTES` (60 by default). On a 401, refresh once and retry. If
  the refresh fails too, send the user to sign-in.
- **Changing the password** signs out every other device. Replace your stored tokens with the ones
  it returns.
- **The emailed codes** are 6 digits and last 15 minutes. You can request a new one at most once a
  minute.
- **Closing an account** is refused (`account_not_empty`) while the wallet holds money or a
  purchase is pending. Closing removes the user's personal details and keeps the transaction
  records.

The profile's `email_verified` and `has_pin` tell you which setup steps are still to do.

## PIN

Purchases need the user's 4-digit transaction PIN.

- **Set or change it:** `POST /api/v1/users/pin/ {pin, password}`. Repeated digits (`1111`) and
  runs (`1234`) are refused.
- **Check it's set:** `GET /api/v1/users/pin/` → `{has_pin, locked_until}`.
- **Send it** as `pin` in every purchase request.

When the PIN is wrong:

- `pin_invalid` carries `attempts_left`.
- The fifth wrong PIN gives `pin_locked` with `locked_until`, and purchases stay blocked for 30
  minutes.
- `pin_not_set` means you should send the user to set a PIN.

## Topping up (Paystack)

1. `POST /api/v1/payments/init/ {amount}` (at least ₦100) → `{reference, authorization_url, status}`.
   This fails with `email_unverified` until the email is verified.
2. Open `authorization_url` (Paystack checkout). Keep the `reference`.
3. When the user comes back, call `GET /api/v1/payments/verify/<reference>/` →
   `{reference, status, credited?}`.

| `status` | Show |
|---|---|
| `success` | Paid and credited. Refresh the balance |
| `pending` | Not finished yet. Check again in a few seconds; a background job also credits late payments |
| `failed` / `abandoned` | Not paid. Nothing was charged |
| `review` | Paid, but not the amount asked for. **Not credited**: support will follow up |

You can verify the same reference as often as you like: it is only ever credited once.

## Buying airtime and data

**Picking the network**

- `GET /api/v1/services/detect-network/?phone=…` → `{phone, network}` suggests a network from the
  number's prefix. `network` is `null` for unknown prefixes.
- Numbers can move between networks, so let the user change the suggestion.

**Data plans**

`GET /api/v1/services/plans/<network>/` → `{network, plans: [{code, name, price}]}`. `network` is `mtn`, `glo`,
`airtel` or `9mobile`, and the prices are what the user pays.

**The purchase**

Send:

```
POST /api/v1/services/airtime/ {network, phone, amount, pin, client_reference}
POST /api/v1/services/data/    {network, phone, plan, amount, pin, client_reference}
```

- Airtime is whole naira, between ₦50 and ₦50,000 by default.
- For data, `plan` is the plan's `code`. Send the `price` you showed as `amount`. If the price has
  changed, the request is refused with the current `price`, so show it and ask again.
- **`client_reference`:** make a new one (for example a UUID) when the user taps Buy, and reuse it
  if you retry that same purchase. A retry then returns the original purchase (200) instead of
  charging twice (a new purchase returns 201). A reference already used for something else gives
  409.

**Purchase `status`**

| Status | Meaning | The money |
|---|---|---|
| `pending` (or `initiated`) | Sent to VTpass, waiting for the result | Held (shown in `locked`) |
| `successful` | Delivered | Spent |
| `failed` | Not delivered | Back in the balance |
| `reversed` | Delivered, then VTpass reversed it | Back in the balance |

For a `pending` purchase, poll `GET /api/v1/services/status/<client_reference>/` every 10–30
seconds. It asks VTpass at most every 30 seconds. Purchases also settle on their own in the
background, so the user can leave the screen.

**Receipt:** `GET /api/v1/services/receipts/<client_reference>/` returns the date, item, network,
phone, amount, status and VTpass IDs. Successful purchases are also emailed.

**Saved numbers**

| Action | Call |
|---|---|
| List | `GET /api/v1/services/beneficiaries/` |
| Save | `POST /api/v1/services/beneficiaries/ {name, phone, network}` |
| Remove | `DELETE /api/v1/services/beneficiaries/<id>/` |

A user can save up to 50 numbers, each number once.

**Limits:**

- 30 purchases an hour per user.
- ₦200,000 a day per user by default, and less for accounts under a day old.

## Wallet and history

**Balance and summary**

- `GET /api/v1/wallet/` → `{balance, locked}`.
  - `balance` is what can be spent.
  - `locked` is held for pending purchases.
- `GET /api/v1/wallet/summary/` returns the balance plus the last 10 airtime and data purchases.

**History**

`GET /api/v1/wallet/history/?kind=&date_from=&date_to=&page=` lists one line per money movement,
newest first:

```json
{"id": 41, "date": "…", "kind": "purchase", "description": "Airtime for 08031234567 (MTN)",
 "amount": "-300.00", "balance_after": "1700.00", "reference": "…",
 "status": "successful", "network": "mtn", "phone": "08031234567", "plan": null}
```

- `kind` is one of:
  - `topup`;
  - `purchase` (the money leaving, with the purchase's current `status`);
  - `refund` (failed or reversed purchases);
  - `reward` (points redeemed);
  - `adjustment` (support correction);
  - `opening` (balance before the ledger existed).
- `amount` is signed.
- Purchase lines also have `status`, `network`, `phone` and `plan`.

**Statement:** `GET /api/v1/wallet/statement/?date_from=&date_to=` downloads a CSV, oldest first.

- With no dates it covers the last 30 days.
- Ranges over a year return `invalid_range`.

## Rewards

- **Earning:** a successful purchase earns 1 point per ₦100 by default.
- **Reversals:** if VTpass reverses the purchase, its points are taken back. This can leave the
  points total below zero.

**Balance**

`GET /api/v1/rewards/` → `{points, naira_value, point_value, min_redemption, recent: [{date, points, reason}]}`

**Redeeming**

`POST /api/v1/rewards/redeem/ {points}` → `{credited, points, balance}`

- The naira goes straight into the wallet, and the history shows it as a `reward` line.
- The request is refused with `redemption_refused` if it's below `min_redemption` (100 by
  default), more than the user has, or rewards are off.

## Rate limits

When a limit is hit you get a 429 with code `throttled`. Wait before retrying; DRF sends a
`Retry-After` header.

| What | Limit |
|---|---|
| Login | 20 a minute per IP, and 10 an hour per email |
| Sign-up | 20 an hour per IP |
| Password reset, email codes, PIN changes, password changes | 10 an hour |
| Reward redemptions | 10 an hour |
| Top-ups | 20 an hour |
| Purchases | 30 an hour |
| Everything | 1,000 a day per user |
