#!/bin/bash
# Backup: PostgreSQL dump (encrypted) + reports volume tarball
set -euo pipefail

BACKUP_DIR="/opt/backups/intelligence-ita"
DATE=$(date +%Y%m%d_%H%M%S)
APP_DIR="/opt/intelligence-ita/repo"
COMPOSE_FILE="${APP_DIR}/docker-compose.yml"

# Load env vars
if [ -f "${APP_DIR}/.env.production" ]; then
    set -a
    source "${APP_DIR}/.env.production"
    set +a
fi

# Each dump is ~1.5 GB and grows with the DB. 30 days of dumps filled the
# 75 GB disk on 2026-10-28 and broke every deploy, so keep a week.
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
# Refuse to start below this much free space: a dump cut short by a full
# disk still looks like a backup.
MIN_FREE_GB="${BACKUP_MIN_FREE_GB:-5}"

mkdir -p "${BACKUP_DIR}/postgres" "${BACKUP_DIR}/reports"

echo "[$(date)] ========================================"
echo "[$(date)] Starting backup..."

FREE_GB=$(df -P -BG "${BACKUP_DIR}" | awk 'NR==2 {gsub("G", "", $4); print $4}')
if [ "${FREE_GB}" -lt "${MIN_FREE_GB}" ]; then
    echo "[$(date)] ERROR: only ${FREE_GB} GB free on ${BACKUP_DIR} (need ${MIN_FREE_GB}). Aborting."
    exit 1
fi

# Remove a half-written file if any step below fails.
IN_PROGRESS=""
trap 'if [ -n "${IN_PROGRESS}" ]; then rm -f "${IN_PROGRESS}"; fi' EXIT

# ------------------------------------------------------------------
# 1. PostgreSQL dump — compressed and encrypted on the host
# ------------------------------------------------------------------
DB_BACKUP="${BACKUP_DIR}/postgres/intelligence_ita_${DATE}.sql.gz.enc"
IN_PROGRESS="${DB_BACKUP}"

docker compose -p app -f "${COMPOSE_FILE}" --env-file "${APP_DIR}/.env.production" exec -T postgres \
    pg_dump -U "${POSTGRES_USER}" intelligence_ita \
    | gzip \
    | openssl enc -aes-256-cbc -salt -pbkdf2 -pass "pass:${BACKUP_PASSWORD}" \
    > "${DB_BACKUP}"

DB_SIZE=$(du -h "${DB_BACKUP}" | cut -f1)
if [ "$(du -k "${DB_BACKUP}" | cut -f1)" -lt 1 ]; then
    echo "[$(date)] ERROR: DB backup file is suspiciously small (${DB_SIZE}). Aborting."
    rm -f "${DB_BACKUP}"
    exit 1
fi
echo "[$(date)] DB backup: $(basename "${DB_BACKUP}") (${DB_SIZE})"

# ------------------------------------------------------------------
# 2. Reports volume — tar from the running backend container
# ------------------------------------------------------------------
REPORTS_BACKUP="${BACKUP_DIR}/reports/reports_${DATE}.tar.gz"
IN_PROGRESS="${REPORTS_BACKUP}"

docker compose -p app -f "${COMPOSE_FILE}" --env-file "${APP_DIR}/.env.production" exec -T backend \
    tar -czf - /app/reports \
    > "${REPORTS_BACKUP}"

REPORTS_SIZE=$(du -h "${REPORTS_BACKUP}" | cut -f1)
echo "[$(date)] Reports backup: $(basename "${REPORTS_BACKUP}") (${REPORTS_SIZE})"
IN_PROGRESS=""

# ------------------------------------------------------------------
# 3. Retention: keep the last RETENTION_DAYS days
# ------------------------------------------------------------------
find "${BACKUP_DIR}/postgres"  -name "*.sql.gz.enc" -mtime +"${RETENTION_DAYS}" -delete
find "${BACKUP_DIR}/reports"   -name "*.tar.gz"      -mtime +"${RETENTION_DAYS}" -delete
echo "[$(date)] Cleaned up backups older than ${RETENTION_DAYS} days"

# ------------------------------------------------------------------
# 4. Optional: sync to Hetzner Object Storage (uncomment after rclone setup)
# ------------------------------------------------------------------
# rclone sync "${BACKUP_DIR}" hetzner:intelligence-backups/ --max-age 14d

echo "[$(date)] Backup complete."
