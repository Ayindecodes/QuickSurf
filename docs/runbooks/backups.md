# Backups and restore drill

A backup only counts once it has been restored. Do this drill **before launch**, then every quarter,
and write down the results.

## What protects the data

- **Render PostgreSQL backups:** use a paid plan. Check in the dashboard how long backups are kept and
  whether point-in-time recovery is included. Free databases expire and have no backups.
- **An independent weekly dump** that doesn't depend on Render. Run it from a trusted machine with the
  external connection string:

  ```bash
  pg_dump --format=custom --no-owner "$DATABASE_URL" > quicksurf-$(date +%F).dump
  ```

  Store it encrypted (it contains personal and financial data), outside Render, and keep at least four.

## The restore drill

1. **Restore into a new, empty database:** Render's "restore to new database", or a scratch Postgres with
   `pg_restore --no-owner --dbname "$SCRATCH_URL" quicksurf-YYYY-MM-DD.dump`. **Never restore over
   production.**
2. **Point a throwaway copy of the app at it,** with no live providers so nothing can move money:

   ```bash
   ENV=staging DEBUG=False PROVIDER_MODE=SANDBOX DATABASE_URL=$SCRATCH_URL \
     PURCHASES_ENABLED=False TOPUPS_ENABLED=False python manage.py check_wallets
   ```

3. **Check it is complete:**
   - `check_wallets` passes;
   - `python manage.py audit_money` looks like production's;
   - the latest purchase and payment timestamps are as recent as the backup should be.
4. **Record:**
   - how long the restore took. That is how long the site would be down;
   - how old the newest data was. That is how much could be lost.
5. **Delete the scratch database and any copies.**

## If production must be restored for real

1. Turn the kill switches on (`PURCHASES_ENABLED=False`, `TOPUPS_ENABLED=False`).
2. Restore to a **new** database and check it as above.
3. Point `DATABASE_URL` at it, for the web service and all cron jobs.
4. Reconcile everything that happened after the backup:
   - Paystack payments (dashboard, or `daily_report`'s reconciliation);
   - VTpass purchases (VTpass dashboard).

   Credit or refund by hand with `wallets.services.adjust` and a note
   ([money-mismatch.md](money-mismatch.md)).
5. Run `check_wallets`, then turn the kill switches off.
