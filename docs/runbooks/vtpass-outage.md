# VTpass: purchases failing or low balance

**Signs:**
- the `failure_spike` alert ("N of M purchases failed in the last hour, most common VTpass code: X");
- customers reporting failed purchases;
- the `vtpass_balance` alert.

Failed purchases are refunded automatically, so customers keep their money. What's lost is sales.

## 1. Read the code in the alert

| Code | Meaning | Do |
|---|---|---|
| `018` | Our VTpass wallet is empty | Fund the VTpass wallet in the VTpass dashboard. Purchases resume immediately. |
| `027` | Server IP not whitelisted | Ask VTpass support to whitelist Render's outbound IPs. |
| `087` | Invalid credentials | The VTpass keys on Render are wrong or were rotated. See [key-rotation.md](key-rotation.md). |
| `023`, `021`, `022`, `024` | Account/API access problem | Contact VTpass support. |
| `085` | Request ID format rejected | A bug on our side. Check `services/vtpass.py` `generate_request_id` and the deployed version. |
| `030`, `034`, `035` | Network/service down at VTpass | Usually temporary. Consider pausing sales (below). |
| `999` / `none` | We couldn't reach VTpass | Check VTpass status; these purchases stay **pending** (see [stuck-purchases.md](stuck-purchases.md)). |

Codes: https://www.vtpass.com/documentation/response-codes/

## 2. If it will last a while, pause sales

Set `PURCHASES_ENABLED=False` on Render. Customers get "Purchases are temporarily unavailable" instead of
failures.

## 3. When it's fixed

1. Set `PURCHASES_ENABLED=True`.
2. Run `python manage.py requery_pending` to settle anything left pending.
3. Run `python manage.py vtpass_balance` to confirm the balance is healthy.

## Low balance (`vtpass_balance` alert)

Fund the VTpass wallet before it reaches zero: purchases fail with `018` once it's empty. The threshold is
`--balance-below` on `ops_alerts` (default ₦10,000). Raise it as sales grow.
