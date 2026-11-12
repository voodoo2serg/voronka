#!/bin/sh
# Alert when /health is 503 or worker stale. Wire to cron/systemd + notify command.
set -eu
BASE_URL=${VORONKA_HEALTH_URL:-http://127.0.0.1:18080/health}
code=$(curl -s -o /tmp/voronka-health.json -w '%{http_code}' "$BASE_URL" || echo 000)
if [ "$code" != "200" ]; then
  echo "voronka_alert health_http=$code body=$(cat /tmp/voronka-health.json 2>/dev/null || true)"
  if [ -n "${VORONKA_ALERT_HOOK:-}" ]; then
    curl -sS -X POST -H 'Content-Type: application/json' \
      -d "{\"text\":\"voronka health $code\"}" "$VORONKA_ALERT_HOOK" >/dev/null || true
  fi
  exit 1
fi
echo "voronka_alert ok"
