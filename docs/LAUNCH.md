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

## 2. Launch day: moving from Render to the self-hosted server

Today's live site runs the old code on Render. On launch day, its customers and their money are copied to
the new server, which then takes over. Pick a quiet hour, for example 6–8 am, and allow about two hours.

### The day before
- [ ] The new server is set up and was tested in **test mode** (`ENV=staging`, VTpass sandbox, Paystack
  test keys) with the new frontend pointed at it.
- [ ] `deploy/.env` is switched to the **live** settings:
  - `ENV=production`, `PROVIDER_MODE=LIVE`, live keys, soft-launch limits;
  - **`PURCHASES_ENABLED=False` and `TOPUPS_ENABLED=False`**.

  Then run `deploy/deploy.sh restart`. It must report healthy.
- [ ] **On Render, turn auto-deploy off.** This one is critical: Render rebuilds whenever `main` changes,
  and its build command runs migrations, which would upgrade the **live** database under the old code.
  Go to the web service and every cron job, then Settings → Build & Deploy → Auto-Deploy: **No**. Do this
  before merging anything.
- [ ] Check the Render database can be copied:
  - its page shows the **PostgreSQL version**. It must be 16 or lower; if it's 17, tell the developer
    first;
  - under **Access Control**, the new server's IP must be allowed to connect.
- [ ] The frontend's business details and legal review are done (`npm run launch-check` passes).

### On the day
1. **Stop sales on Render:** set `PURCHASES_ENABLED=False` and `TOPUPS_ENABLED=False` in its environment.
   Payments already made are still credited.
2. **Wait about 15 minutes** so purchases in progress settle. Anything still pending is copied over and
   settled by the new server.
3. **Merge the backend PRs** into `main`, in order: #3, #4, … #12. Retarget each next PR to `main` if
   GitHub doesn't. Render won't deploy, because auto-deploy is off.
4. **Release `main` on the new server:** `deploy/deploy.sh deploy`.
5. **Copy the live data across.**
   1. In Render, open the database → **Connections** → copy the **External Database URL**.
   2. On the new server, run the command below and type `IMPORT` when asked:

      ```bash
      deploy/deploy.sh import-database '<External Database URL>'
      ```

   It copies everything (customers, passwords, wallets, purchases, payments, points) and upgrades it to
   the new version. Opening ledger entries are created from the old balances. It then checks every wallet
   against its ledger and prints the money audit. **Save that report:** it lists past problems to settle
   with customers.
6. **Point the providers at the new server:**
   - Paystack webhook → `https://<API_DOMAIN>/api/payments/webhook/`;
   - VTpass callback → `https://<API_DOMAIN>/api/services/vtpass/callback/`;
   - if VTpass whitelists IPs, add the server's IP.
7. **Switch the website:**
   1. In Vercel, set `NEXT_PUBLIC_API_BASE=https://<API_DOMAIN>`.
   2. Merge frontend PR #2 into #1, then #1 into `main`. Vercel deploys it.
   3. Open the site and log in with an existing account. The balance must match Render's.
8. **Turn sales on, on the new server:** set `PURCHASES_ENABLED=True` and `TOPUPS_ENABLED=True` in
   `deploy/.env`, then run `deploy/deploy.sh restart`.
9. **Do a live test from a staff account:**
   - top up ₦200 with a real card;
   - buy ₦50 of airtime to your own number;
   - check the receipt, the history and the email;
   - run `deploy/deploy.sh manage check_wallets`.
10. **Retire Render:** suspend the web service and the cron jobs. Keep the database for two weeks as an
    extra copy, then delete it.
11. **Take the first backup now** (`deploy/deploy.sh backup-now`) and check it reached your bucket. Watch
    the logs (`deploy/deploy.sh logs api`) and the alert emails for an hour, then announce the launch to
    the soft-launch group.

### If it goes wrong
- **Before step 8** (sales are still off everywhere):
  1. Point Vercel and the provider URLs back at Render.
  2. Turn Render's sales back on.
  3. Investigate.

  Nothing new has happened on the new server, so nothing is lost.
- **After step 8:** don't go back to Render, whose data is now out of date. Turn the kill switches on
  and fix forward ([runbooks/rollback.md](runbooks/rollback.md)).

### If you stay on Render instead
Same idea, without the copy:
1. Turn auto-deploy off and back up the database.
2. Set every new environment variable and the soft-launch limits, with the kill switches off.
3. Merge #3–#12.
4. Use **Manual Deploy → latest commit** on the web service and the cron jobs. The build runs the
   migrations.
5. Run the same checks.
6. Merge the frontend.
7. Turn sales on and do the same live test.

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
