# Launch plan

The checklist for taking QuickSurf live: the backend PRs #3–#10, then the new frontend.

- **Owner "You":** the business owner (accounts, dashboards, legal, money decisions).
- **Owner "Dev":** whoever does the deploys.

Tick each item off in the PR or a copy of this file.

## 1. Before launch

### Business and legal (You)
- [ ] Fill in the business details in the frontend (`src/content/business.ts`):
  - registered name;
  - RC/BN number;
  - address;
  - support email and WhatsApp;
  - response times;
  - the wallet refund policy.

  `npm run launch-check` must pass.
- [ ] A lawyer reviews the Terms, Privacy Policy and Refund Policy pages. They are drafts written to
  match how the system works, not legal advice.
- [ ] Check whether QuickSurf must register with the Nigeria Data Protection Commission. That depends on
  how many people's data you process, so ask your lawyer.
- [ ] Paystack business verification is complete, so live keys work and settlements reach the bank.
- [ ] The VTpass live account is approved and funded.
- [ ] The support inbox and WhatsApp number are working and monitored.

### Keys and accounts (You)
- [ ] Rotate the VTpass and Paystack keys that were exposed in the repository's history
  ([runbooks/key-rotation.md](runbooks/key-rotation.md)).
- [ ] Run `python manage.py audit_money --csv` against production on the **current** code, and keep the
  file. It lists past money problems to settle with customers.

### Infrastructure (You set up, Dev checks)
Everything here follows [DEPLOYMENT.md](../DEPLOYMENT.md).
- [ ] **If self-hosting** ([deploy/README.md](../deploy/README.md)):
  - a written agreement with the server's owner about access to data and money;
  - `server-setup.sh` run, and SSH key logins only;
  - DNS for the API pointing at the server;
  - `deploy/.env` filled in;
  - the backup key made on your own computer;
  - off-server backup storage and `BACKUP_PING_URL` set;
  - a first `deploy/deploy.sh deploy`;
  - the recorded client IP checked: it must be your real IP.
- [ ] **If on Render:**
  - PostgreSQL on a paid plan with backups;
  - Redis;
  - an Environment Group with every variable;
  - the cron jobs.
- [ ] SMTP works: `python manage.py sendtestemail you@example.com`.
- [ ] Paystack webhook URL and VTpass callback URL are set in their dashboards.
- [ ] Vercel: `NEXT_PUBLIC_API_BASE` (and the Sentry DSN, if you want one) for production.
- [ ] A staging copy: Render service and database with `ENV=staging`, sandbox and test keys, and Vercel
  previews pointed at it.

### Staging sign-off (Dev runs, You approve)
- [ ] `python scripts/smoke_test.py https://<staging-api> … --i-confirm-this-is-sandbox` passes.
- [ ] In the staging frontend, by hand:
  - sign up and confirm the email;
  - top up with a Paystack test card;
  - set a PIN;
  - buy airtime and data with VTpass sandbox numbers;
  - see a failed purchase refunded;
  - check history and download a statement;
  - redeem rewards;
  - reset the password.
- [ ] Staff sign in to the admin with an authenticator app. `setup_staff_roles` has been run, and staff
  are in the Support or Finance groups.

### Load, backups, monitoring
- [x] Load test of the purchase path ([load-testing.md](load-testing.md)): no money errors under
  concurrency, about 8.5 purchases/s with `GUNICORN_THREADS=8`.
- [ ] A small load test on staging (`--count 5 --per-user 3`) passes.
- [ ] The restore drill is done ([runbooks/backups.md](runbooks/backups.md)). Restore time and data age
  are written down.
- [ ] `OPS_ALERT_EMAILS` is set.
- [ ] An uptime monitor watches `/api/health/`.
- [ ] Sentry DSNs are set (optional).
- [ ] Vercel Analytics is on.

## 2. Launch day

Pick a quiet hour, for example 6–8 am. Allow about two hours.

1. **Stop sales on the current site** (this works on today's code):
   `PURCHASES_ENABLED=False`, `TOPUPS_ENABLED=False`.
2. **Back up:** take a manual database backup and record its name. This is the rollback point.
3. **Prepare the server.** (Self-hosted: nothing deploys until you run `deploy/deploy.sh`, so skip the
   auto-deploy step.) On Render:
   - turn **auto-deploy off**;
   - make sure every new environment variable is set. The new code refuses to start without them;
   - set the soft-launch limits below.
4. **Merge the backend PRs in order:**
   - #3, then #4, … then #10;
   - after each merge, retarget the next PR to `main` if GitHub hasn't done it already;
   - wait for CI on each.
5. **Deploy once:**
   - self-hosted: `deploy/deploy.sh deploy`;
   - Render: **Manual Deploy → latest commit** on the web service and the cron jobs.

   Either way, the migrations run as part of the deploy.
6. **Check, with sales still off** (DEPLOYMENT.md, "After every deploy"):
   - health;
   - `BOOT … PROVIDER_MODE=LIVE` in the logs;
   - `check_wallets`;
   - `vtpass_balance`.
7. **Merge frontend PR #1.** Vercel deploys it. Open the site and log in.
8. **Turn sales on:** `PURCHASES_ENABLED=True`, `TOPUPS_ENABLED=True`.
9. **Do a live test from a staff account:**
   - top up ₦200 with a real card;
   - buy ₦50 of airtime to your own number;
   - check the receipt, the history and the email receipt;
   - run `check_wallets`.
10. **Turn auto-deploy back on.** Watch the logs and alerts for an hour, then announce the launch to
    the soft-launch group.

If anything in steps 5–9 goes wrong, see [runbooks/rollback.md](runbooks/rollback.md): kill switches
first, then fix forward.

## 3. Soft launch (first 1–2 weeks)

Keep the exposure small while real traffic proves the system.

| Setting | Soft launch | Normal default |
|---|---|---|
| `AIRTIME_MAX_AMOUNT` | 5000 | 50000 |
| `TOPUP_MAX_AMOUNT` | 20000 | 500000 |
| `DAILY_PURCHASE_LIMIT` | 20000 | 200000 |
| `NEW_ACCOUNT_DAILY_LIMIT` | 5000 | 20000 |
| `PURCHASE_RATE` | 15/hour | 30/hour |
| `TOPUP_RATE` | 5/hour | 20/hour |
| `GUNICORN_THREADS` | 8 | 4 |

- **Keep the VTpass float small,** for example ₦50,000, and top it up daily. That caps what any bug could
  spend.
- **Invite a small group first,** say 50–100 people, before announcing widely.

## 4. Review after one week (You and Dev)

| Look at | Where | Healthy |
|---|---|---|
| Purchases by status | `daily_report` emails | `failed` under 5% and explained; nothing `pending` over 30 minutes |
| Reversals | Admin, filter by status `reversed` | Rare; each one refunded |
| Top-ups in `review` | Admin, payment intents | Zero, or each one resolved |
| Paystack reconciliation | `daily_report` | No unmatched payments |
| Ledger | `check_wallets` (daily cron) | Always passes |
| Alerts | `ops_alerts` emails | Every alert understood and acted on |
| Errors | Sentry and logs | No unexplained 500s |
| Support | Inbox and WhatsApp | First replies within the promised time; disputes closed |
| Speed | Vercel Speed Insights | Pages load in under 3 s on mobile |

Then decide:
- whether to raise the limits toward the defaults;
- whether to widen the audience;
- what to fix first.

Write the decisions down, and repeat the review after a month.
