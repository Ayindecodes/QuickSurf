#!/bin/sh
# Runs backup.sh once a day at BACKUP_HOUR_UTC:00 (default 02:00 UTC, 03:00 in Lagos).
set -eu
hour="${BACKUP_HOUR_UTC:-2}"
echo "Backups run daily at ${hour}:00 UTC into /backups"
while true; do
  now=$(date -u +%s)
  next=$(date -u -d "$(date -u +%Y-%m-%d) ${hour}:00:00" +%s)
  if [ "$next" -le "$now" ]; then next=$((next + 86400)); fi
  sleep $((next - now))
  backup.sh || echo "BACKUP FAILED" >&2
done
