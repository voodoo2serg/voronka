#!/bin/sh
set -eu
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose stop api worker
trap 'docker compose start api worker >/dev/null' EXIT INT TERM
docker compose exec -T db pg_dump -U funnel -d funnel -Fc > "backups/database-$stamp.dump"
docker compose run --rm --no-deps --entrypoint tar api -C /data/media -czf - . > "backups/media-$stamp.tar.gz"
echo "Database and media snapshots created; copy both to off-server encrypted storage."
