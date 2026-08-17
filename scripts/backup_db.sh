#!/usr/bin/env bash
# Dumps the career_copilot MySQL database to a timestamped, gzip-compressed
# local file and prunes old backups beyond a retention count.
#
# No explicit table list - mysqldump covers the whole database, so this
# stays correct as new tables get added. As of the application-tracking/
# tailored-artifacts work, that now includes real personal data beyond
# just the profile/vacancies/ResumeVersion: Application.notes (freeform
# notes on a tracked posting) and GeneratedArtifact.content (full
# generated cover letters/tailoring suggestions, no longer request-time-
# only - see docs/DEPLOYMENT.md's Privacy and security notes).
#
# Run: ./scripts/backup_db.sh
# Cron example (adjust path):
#   0 3 * * *  cd /path/to/career-copilot && ./scripts/backup_db.sh >> backups/backup.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/.."

# shellcheck disable=SC1091
source .env

RETENTION_COUNT=14
BACKUP_DIR="backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="${BACKUP_DIR}/career_copilot_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

docker compose exec -T mysql mysqldump \
  -u root -p"${MYSQL_ROOT_PASSWORD}" \
  --single-transaction --routines \
  "${MYSQL_DATABASE}" | gzip > "$OUT_FILE"

chmod 600 "$OUT_FILE"
echo "Backed up ${MYSQL_DATABASE} to ${OUT_FILE} ($(du -h "$OUT_FILE" | cut -f1))"

# Prune: keep only the RETENTION_COUNT most recent backups.
# shellcheck disable=SC2012
ls -1t "${BACKUP_DIR}"/career_copilot_*.sql.gz 2>/dev/null | tail -n "+$((RETENTION_COUNT + 1))" | while read -r old; do
  rm -f "$old"
  echo "Pruned old backup: ${old}"
done
