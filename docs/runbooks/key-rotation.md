# Rotating keys and secrets

Rotate a key immediately if it was ever committed, pasted in chat, or seen by someone who has left.

| Secret | Where | After changing it |
|---|---|---|
| `VTPASS_API_KEY`, `VTPASS_PUBLIC_KEY`, `VTPASS_SECRET_KEY` | VTpass dashboard → API keys | Update Render. Check with `python manage.py vtpass_balance`. |
| `PAYSTACK_SECRET_KEY` (and `PAYSTACK_WEBHOOK_SECRET`) | Paystack dashboard → Settings → API Keys & Webhooks | Update Render (both, if `PAYSTACK_WEBHOOK_SECRET` is set). Webhooks are signed with this key: watch for the `bad_webhook_signatures` alert, then run `verify_pending_topups --min-age-mins 0`. |
| `SECRET_KEY` | Render only (generate 50+ random characters) | See below. |
| `EMAIL_HOST_PASSWORD` | Your email provider | Update Render. Send yourself a password reset to check. |
| `SENTRY_DSN` | Sentry → project settings | Update Render. |

## Rotating `SECRET_KEY`

It signs login tokens and keys the stored hashes of transaction PINs and emailed codes. Changing it:
- **signs every user out**, because all tokens become invalid;
- **makes every transaction PIN stop working**, so users must set a new one (`/api/users/pin/` with their
  password);
- **invalidates codes already emailed.**

Do it at a quiet time, and tell users they'll need to log in again and reset their PIN.

## Steps

1. Generate the new key in the provider's dashboard, keeping the old one active if the provider allows.
2. Update the value in the Render environment. Render redeploys.
3. Check: `GET /api/health/`, `python manage.py vtpass_balance`, and a test top-up or purchase if possible.
4. Revoke the old key in the provider's dashboard.
