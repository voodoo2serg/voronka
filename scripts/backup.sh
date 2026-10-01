#!/bin/sh
set -eu
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose stop api worker
trap 'docker compose start api worker >/dev/null' EXIT INT TERM
docker compose exec -T db pg_dump -U funnel -d funnel -Fc > "backups/database-$stamp.dump"
docker compose run --rm --no-deps --entrypoint tar api -C /data/media -czf - . > "backups/media-$stamp.tar.gz"
find backups -name 'database-*.dump' -mtime +7 -delete
find backups -name 'media-*.tar.gz' -mtime +7 -delete
echo "Database and media snapshots created. Copy both off this server; local copies older than 7 days are removed."
