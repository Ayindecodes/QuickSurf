#!/bin/sh
# One backup: pg_dump, encrypted to BACKUP_AGE_RECIPIENT (a public key: this server can't decrypt it),
# kept in /backups for BACKUP_KEEP_DAYS days and copied off the server with rclone.
set -eu
# shellcheck disable=SC3040 # Alpine's sh (busybox ash) supports pipefail
set -o pipefail
umask 077

: "${BACKUP_AGE_RECIPIENT:?set BACKUP_AGE_RECIPIENT (an age public key, age1...) in deploy/.env}"

ping() {
  if [ -n "${BACKUP_PING_URL:-}" ]; then wget -q -T 10 -O /dev/null "${BACKUP_PING_URL}$1" || true; fi
}

file="/backups/quicksurf-$(date -u +%Y%m%dT%H%M%SZ).dump.age"
if ! pg_dump --format=custom --no-owner --dbname="$DATABASE_URL" | age --recipient "$BACKUP_AGE_RECIPIENT" > "$file.partial"; then
  rm -f "$file.partial"
  echo "Backup failed: pg_dump or encryption error" >&2
  ping /fail
  exit 1
fi
mv "$file.partial" "$file"
echo "Backup written: $file ($(du -h "$file" | cut -f1))"

find /backups -name 'quicksurf-*.dump.age' -mtime +"${BACKUP_KEEP_DAYS:-14}" -delete

if [ -n "${BACKUP_RCLONE_REMOTE:-}" ]; then
  if ! rclone copy --quiet --no-traverse "$file" "$BACKUP_RCLONE_REMOTE"; then
    echo "Backup made but NOT copied off the server ($BACKUP_RCLONE_REMOTE)" >&2
    ping /fail
    exit 1
  fi
  echo "Copied to $BACKUP_RCLONE_REMOTE"
else
  echo "WARNING: no off-server copy (set BACKUP_RCLONE_REMOTE). If this server is lost, so are the backups." >&2
fi
ping ""
