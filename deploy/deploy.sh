#!/usr/bin/env bash
# Run QuickSurf on this server. See deploy/README.md.
#
#   deploy/deploy.sh deploy [git-ref]     build and start a version (default: origin/main), migrations included
#   deploy/deploy.sh rollback [tag]       go back to the previous deployed version (or a given one)
#   deploy/deploy.sh restart              re-read deploy/.env (e.g. after flipping a kill switch)
#   deploy/deploy.sh status | logs [svc]  what's running / follow logs
#   deploy/deploy.sh manage <command>     run a manage.py command, e.g. "manage check_wallets"
#   deploy/deploy.sh backup-now           take a backup immediately
#   deploy/deploy.sh restore-check <file.dump.age> <age-identity-file>
#                                         restore a backup into a throwaway database and check it
#   deploy/deploy.sh restore <file.dump.age> <age-identity-file>
#                                         REPLACE the production database with a backup (disaster recovery)
set -euo pipefail
cd "$(dirname "$0")"

HISTORY=.deploy-history # every deployed image tag, oldest first
CURRENT=.current-tag    # the tag running now

current_tag() { cat "$CURRENT" 2>/dev/null || true; }
compose() { APP_IMAGE_TAG="${APP_IMAGE_TAG:-$(current_tag)}" docker compose --env-file .env -f compose.yml "$@"; }
die() { echo "$*" >&2; exit 1; }

[ -f .env ] || die "deploy/.env is missing: copy deploy/.env.example to deploy/.env and fill it in."
[ "$(stat -c %a .env)" = 600 ] || { chmod 600 .env; echo "Made deploy/.env readable by its owner only."; }

wait_healthy() {
  local id status
  id=$(compose ps -q api)
  for _ in $(seq 1 40); do
    status=$(docker inspect --format '{{.State.Health.Status}}' "$id" 2>/dev/null || echo starting)
    if [ "$status" = healthy ]; then echo "API is healthy."; return 0; fi
    sleep 3
  done
  compose logs --tail=50 api >&2
  die "The API didn't become healthy (logs above)."
}

keep_recent_images() {
  local keep
  keep=$(tail -n 5 "$HISTORY" 2>/dev/null || true)
  for tag in $(docker image ls quicksurf-api --format '{{.Tag}}'); do
    grep -qx "$tag" <<<"$keep" || [ "$tag" = "$(current_tag)" ] || docker image rm "quicksurf-api:$tag" >/dev/null || true
  done
}

switch_to() {
  echo "$1" >"$CURRENT"
  APP_IMAGE_TAG=$1 compose up -d --remove-orphans
  wait_healthy
}

case "${1:-}" in
  deploy)
    ref="${2:-origin/main}"
    git -C .. fetch --quiet --tags origin
    git -C .. checkout --quiet --detach "$ref"
    tag=$(git -C .. rev-parse --short=12 HEAD)
    echo "Deploying $ref ($tag)"
    APP_IMAGE_TAG=$tag compose build --pull api backup
    APP_IMAGE_TAG=$tag compose up -d db redis
    APP_IMAGE_TAG=$tag compose run --rm --no-deps api python manage.py migrate --noinput
    switch_to "$tag"
    echo "$tag" >>"$HISTORY"
    keep_recent_images
    ;;
  rollback)
    now=$(current_tag)
    target="${2:-$(grep -vx "$now" "$HISTORY" 2>/dev/null | tail -n 1 || true)}"
    [ -n "$target" ] || die "No earlier version to roll back to."
    docker image inspect "quicksurf-api:$target" >/dev/null 2>&1 || die "Image quicksurf-api:$target is no longer on this server."
    echo "Rolling back from $now to $target. Database migrations are NOT undone (see docs/runbooks/rollback.md)."
    switch_to "$target"
    ;;
  restart)
    compose up -d --force-recreate api scheduler
    wait_healthy
    ;;
  status) compose ps ;;
  logs) compose logs -f --tail=200 ${2:+"$2"} ;;
  manage) shift; compose exec api python manage.py "$@" ;;
  backup-now) compose exec backup backup.sh ;;
  restore-check)
    file="${2:?usage: restore-check <file.dump.age> <age-identity-file>}"
    identity="${3:?usage: restore-check <file.dump.age> <age-identity-file>}"
    scratch="quicksurf-restore-check-$$"
    network="${scratch}-net"
    trap 'docker rm -f "$scratch" >/dev/null 2>&1; docker network rm "$network" >/dev/null 2>&1 || true' EXIT
    docker network create "$network" >/dev/null
    docker run -d --name "$scratch" --network "$network" -e POSTGRES_PASSWORD=check -e POSTGRES_DB=quicksurf postgres:16-alpine >/dev/null
    until docker exec "$scratch" pg_isready -U postgres -q; do sleep 1; done
    started=$(date +%s)
    docker run --rm -i --network "$network" -v "$(realpath "$identity")":/key:ro quicksurf-backup:latest \
      sh -c "age --decrypt --identity /key | pg_restore --no-owner --dbname=postgres://postgres:check@$scratch:5432/quicksurf" \
      <"$file"
    echo "Restored in $(($(date +%s) - started))s. Checking it (no providers, sales off):"
    docker run --rm --network "$network" \
      -e ENV=staging -e DEBUG=False -e SECRET_KEY="restore-check-$(openssl rand -hex 32)" -e PROVIDER_MODE=SANDBOX \
      -e PURCHASES_ENABLED=False -e TOPUPS_ENABLED=False -e DB_SSL_REQUIRE=False \
      -e DATABASE_URL="postgres://postgres:check@$scratch:5432/quicksurf" "quicksurf-api:$(current_tag)" \
      sh -c 'python manage.py check_wallets && python manage.py shell -c "
from django.db.models import Max
from services.models import AirtimeTransaction, DataTransaction
from users.models import User
from wallets.models import Transaction
print(\"users:\", User.objects.count(), \"| purchases:\", AirtimeTransaction.objects.count() + DataTransaction.objects.count())
print(\"newest ledger entry:\", Transaction.objects.aggregate(t=Max(\"timestamp\"))[\"t\"])"'
    ;;
  restore)
    file="${2:?usage: restore <file.dump.age> <age-identity-file>}"
    identity="${3:?usage: restore <file.dump.age> <age-identity-file>}"
    [ -f "$file" ] && [ -f "$identity" ] || die "Can't read $file or $identity."
    echo "This REPLACES the production database with $file."
    echo "Everything recorded after that backup is lost (docs/runbooks/backups.md). Type RESTORE to continue:"
    read -r answer
    [ "$answer" = RESTORE ] || die "Cancelled."
    compose stop api scheduler backup
    compose exec -T db psql -q -U quicksurf -d postgres \
      -c "DROP DATABASE IF EXISTS quicksurf WITH (FORCE);" -c "CREATE DATABASE quicksurf OWNER quicksurf;"
    docker run --rm -i -v "$(realpath "$identity")":/key:ro quicksurf-backup:latest age --decrypt --identity /key <"$file" |
      compose exec -T db pg_restore --no-owner -U quicksurf --dbname=quicksurf
    compose start api scheduler backup
    wait_healthy
    compose exec api python manage.py check_wallets
    echo "Restored. Reconcile Paystack and VTpass activity since the backup before turning sales back on."
    ;;
  *) sed -n '2,17p' "$0"; exit 1 ;;
esac
