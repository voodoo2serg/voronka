#!/bin/sh
set -eu
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T db pg_dump -U funnel -d funnel -Fc > "backups/database-$stamp.dump"
echo "Database snapshot created; copy it to off-server encrypted storage."
