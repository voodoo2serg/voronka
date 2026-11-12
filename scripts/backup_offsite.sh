#!/bin/sh
# Automated home-server backup: local dump + optional off-box copy (VOR-5).
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p backups
sh scripts/backup.sh
# backup.sh writes database-*.dump and media-*.tar.gz into backups/
latest_db=$(ls -1t backups/database-*.dump | head -1)
latest_media=$(ls -1t backups/media-*.tar.gz | head -1)
if [ -n "${BACKUP_OFFSITE_DIR:-}" ]; then
  mkdir -p "$BACKUP_OFFSITE_DIR"
  cp -f "$latest_db" "$BACKUP_OFFSITE_DIR/"
  cp -f "$latest_media" "$BACKUP_OFFSITE_DIR/"
  echo "Offsite copy written to $BACKUP_OFFSITE_DIR"
else
  echo "BACKUP_OFFSITE_DIR unset — local snapshots only. Copy off-box manually."
fi
# Optional encrypt if BACKUP_GPG_RECIPIENT is set.
if [ -n "${BACKUP_GPG_RECIPIENT:-}" ]; then
  gpg --batch --yes -e -r "$BACKUP_GPG_RECIPIENT" -o "$latest_db.gpg" "$latest_db"
  gpg --batch --yes -e -r "$BACKUP_GPG_RECIPIENT" -o "$latest_media.gpg" "$latest_media"
  echo "Encrypted snapshots created next to dumps."
fi
echo "backup_ok stamp=$stamp db=$latest_db media=$latest_media"
