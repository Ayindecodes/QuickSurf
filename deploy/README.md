# Self-hosting the QuickSurf API

The whole backend runs on one Linux server (a rented VPS) with Docker Compose:

```
internet ─▶ Caddy (HTTPS, :80/:443) ─▶ api (gunicorn)  ─▶ db (Postgres 16)
                                        scheduler        ─▶ redis
                                        backup ─▶ encrypted copies, off the server
```

| Service | What it does |
|---|---|
| `caddy` | HTTPS with automatic Let's Encrypt certificates; passes the real client IP as `X-Real-IP` |
| `api` | The Django API (`../Dockerfile`), WEB_CONCURRENCY × GUNICORN_THREADS requests at once |
| `scheduler` | The scheduled jobs in `crontab` (settling purchases, alerts, reports, email queue…) |
| `db` | PostgreSQL. Never exposed to the internet |
| `redis` | Locks, rate limits and cached plans (nothing that must survive a restart) |
| `backup` | A nightly `pg_dump`, encrypted, kept for 14 days and copied off the server |

The frontend stays on Vercel. Only its `NEXT_PUBLIC_API_BASE` changes, to `https://<API_DOMAIN>`.

## Before you start: trust

Anyone with root, or with membership of the `docker` group, on this server can read every secret in
`deploy/.env` (Paystack, VTpass, email) and all customer data, and can change the running code. That
includes the server's owner, your friend.
- **Give that access only to people you would trust with the business's money.**
- **Put the arrangement in writing.** Under the Nigeria Data Protection Act they process customer data
  on your behalf.
- **Give each person their own SSH key,** so access can be removed person by person.

Backups are encrypted to a key that never goes on the server (see Backups below), so a copied backup
can't be read without it.

## What you need

- **The server:** a VPS running Ubuntu 22.04 or 24.04, 2 vCPU and 4 GB of memory recommended (2 GB
  works, and the setup adds swap). Around 40 GB of disk.
- **DNS:** a domain name, with an `A` record for the API (for example `api.quicksurf.ng`) pointing at
  the server's IP.
- **Off-server storage for backups,** S3-compatible: Cloudflare R2, Backblaze B2, AWS S3…
- **A fixed IP,** which a VPS has. Give it to VTpass if they ask you to whitelist IPs.

### No domain yet?

You can start without buying one:
- **API address:** create a free name at [duckdns.org](https://www.duckdns.org) (sign in with Google or
  GitHub), e.g. `quicksurf.duckdns.org`, and set its IP to the server's. Use it as `API_DOMAIN`; Caddy
  gets a certificate for it like any other name. Customers never see it: they use the website on Vercel.
- **Email:** use a Gmail account made for QuickSurf, with an
  [app password](https://myaccount.google.com/apppasswords) (it needs 2-step verification):
  `EMAIL_HOST=smtp.gmail.com`, `EMAIL_PORT=587`, the address as `EMAIL_HOST_USER` and
  `DEFAULT_FROM_EMAIL`, and the 16-character app password as `EMAIL_HOST_PASSWORD`. Gmail allows about
  500 emails a day.

**Moving to your own domain later** needs no code or data changes:
1. Change `API_DOMAIN`, `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, then run `deploy/deploy.sh restart`.
2. Update the Paystack webhook and VTpass callback URLs.
3. Update `NEXT_PUBLIC_API_BASE` in Vercel.

## First-time setup

1. **Prepare the server.** As root, once:

   ```bash
   curl -fsSLO https://raw.githubusercontent.com/Ayindecodes/QuickSurf/main/deploy/server-setup.sh
   sudo bash server-setup.sh
   ```

   This installs Docker and git, and turns on:
   - the firewall (SSH, 80 and 443 only);
   - automatic security updates and fail2ban;
   - swap on small servers.

   It also creates the `quicksurf` user. Add your SSH key to `/home/quicksurf/.ssh/authorized_keys`.
   Once you can log in with it, run `sudo bash server-setup.sh --harden-ssh` to turn off password
   logins.

2. **Make the backup key,** on **your own computer**, not the server
   ([age](https://github.com/FiloSottile/age)):

   ```bash
   age-keygen -o quicksurf-backup.key     # prints "Public key: age1..."
   ```

   Keep `quicksurf-backup.key` safe, for example in a password manager. It is the only way to read the
   backups. The public key goes into `deploy/.env`.

3. **Get the code and settings.** As `quicksurf`:

   ```bash
   git clone https://github.com/Ayindecodes/QuickSurf.git && cd QuickSurf
   cp deploy/.env.example deploy/.env && chmod 600 deploy/.env
   nano deploy/.env        # every setting is explained in the file
   ```

4. **Deploy:**

   ```bash
   deploy/deploy.sh deploy
   ```

   This:
   - builds the images;
   - starts Postgres and Redis;
   - runs the database migrations;
   - starts everything;
   - waits for the health check.

   Caddy gets the HTTPS certificate on the first request to `https://<API_DOMAIN>`.

5. **Check it:**
   - `curl https://<API_DOMAIN>/api/health/` shows `"database": "ok"`, `"cache": "ok"` and the commit.
   - Sign in once, then run `deploy/deploy.sh manage shell -c "from core.models import LoginActivity as L; print(L.objects.latest('created').ip)"`.
     It must show your real public IP, not a `172.x` address.
   - Run `deploy/deploy.sh manage createsuperuser` and `deploy/deploy.sh manage setup_staff_roles`.
   - Point the Paystack webhook and VTpass callback at `https://<API_DOMAIN>/…`
     (see [DEPLOYMENT.md](../DEPLOYMENT.md#provider-dashboards)).
   - Set the frontend's `NEXT_PUBLIC_API_BASE` to `https://<API_DOMAIN>` in Vercel.
   - Point an uptime monitor at `https://<API_DOMAIN>/api/health/`.

## Moving the live site here from Render

Customers, passwords, wallets, purchases and payments are copied from Render's database and upgraded to
this version in one command:

```bash
deploy/deploy.sh import-database '<External Database URL from Render>'
```

It then checks every wallet against its ledger and prints a money audit. Follow the launch-day order in
[docs/LAUNCH.md](../docs/LAUNCH.md#2-launch-day-moving-from-render-to-the-self-hosted-server). **Render's
auto-deploy must be off before anything is merged into `main`.**

## Day to day

```bash
deploy/deploy.sh deploy                 # release the latest main (migrations included)
deploy/deploy.sh deploy v1.2.0          # or a tag/commit
deploy/deploy.sh rollback               # back to the previous release (migrations aren't undone)
deploy/deploy.sh status                 # what's running
deploy/deploy.sh logs api               # follow logs (api, scheduler, caddy, db, backup)
deploy/deploy.sh manage check_wallets   # any manage.py command
deploy/deploy.sh restart                # after editing deploy/.env
```

- **Kill switches:** set `PURCHASES_ENABLED=False` and/or `TOPUPS_ENABLED=False` in `deploy/.env`,
  then run `deploy/deploy.sh restart`. Payments already made are still credited.
- **Logs:** they rotate by themselves (5 × 20 MB per service). Production logs are JSON lines with a
  `request_id`.
- **Server updates:** security updates install themselves. Reboot now and then for kernel updates.
  Every service restarts by itself.

## Backups

- **Every night at `BACKUP_HOUR_UTC`,** the `backup` service:
  1. dumps the database;
  2. encrypts it to `BACKUP_AGE_RECIPIENT`;
  3. keeps `BACKUP_KEEP_DAYS` days of copies on the server;
  4. copies each one to `BACKUP_RCLONE_REMOTE`.
- **Watch for missed backups:** set `BACKUP_PING_URL` to a [healthchecks.io](https://healthchecks.io)
  check. It emails you if a night's backup doesn't arrive.
- **Take one now:** `deploy/deploy.sh backup-now`.
- **Restore drill:** do it before launch and every quarter
  ([runbooks/backups.md](../docs/runbooks/backups.md)). Copy the private key to the server just for the
  drill, then delete it:

  ```bash
  deploy/deploy.sh restore-check /path/to/quicksurf-YYYYMMDDTHHMMSSZ.dump.age /path/to/quicksurf-backup.key
  ```

  It restores into a throwaway database with no providers and sales switched off. It then checks every
  wallet against its ledger and prints how long the restore took, the counts and the newest entry.
  Afterwards it removes the throwaway database.

## Capacity

Each purchase holds a gunicorn thread while VTpass answers. With `WEB_CONCURRENCY=2` and
`GUNICORN_THREADS=8`, the [load test](../docs/load-testing.md) handled about 8.5 purchases a second.
That's more than 30,000 an hour. A 2 vCPU / 4 GB server is plenty for launch.

## If the server is lost

1. Set up a new server (steps 1, 3 and 4), with `PURCHASES_ENABLED=False` and `TOPUPS_ENABLED=False` in
   `deploy/.env`.
2. Download the newest backup from the off-server storage.
3. Restore it:

   ```bash
   deploy/deploy.sh restore quicksurf-YYYYMMDDTHHMMSSZ.dump.age quicksurf-backup.key   # asks you to type RESTORE
   ```

   It replaces the database, restarts the app and checks every wallet against its ledger.
4. Reconcile what happened after the backup, then turn sales back on (see "If production must be
   restored for real" in [runbooks/backups.md](../docs/runbooks/backups.md)).

This is why the off-server copy and the restore drill matter.
