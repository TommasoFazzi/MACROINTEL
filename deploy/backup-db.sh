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

mkdir -p "${BACKUP_DIR}/postgres" "${BACKUP_DIR}/reports"

echo "[$(date)] ========================================"
echo "[$(date)] Starting backup..."

# ------------------------------------------------------------------
# 1. PostgreSQL dump — compressed and encrypted on the host
# ------------------------------------------------------------------
DB_BACKUP="${BACKUP_DIR}/postgres/intelligence_ita_${DATE}.sql.gz.enc"

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

docker compose -p app -f "${COMPOSE_FILE}" --env-file "${APP_DIR}/.env.production" exec -T backend \
    tar -czf - /app/reports \
    > "${REPORTS_BACKUP}"

REPORTS_SIZE=$(du -h "${REPORTS_BACKUP}" | cut -f1)
echo "[$(date)] Reports backup: $(basename "${REPORTS_BACKUP}") (${REPORTS_SIZE})"

# ------------------------------------------------------------------
# 3. Retention: keep last 14 days
# ------------------------------------------------------------------
find "${BACKUP_DIR}/postgres"  -name "*.sql.gz.enc" -mtime +30 -delete
find "${BACKUP_DIR}/reports"   -name "*.tar.gz"      -mtime +30 -delete
echo "[$(date)] Cleaned up backups older than 30 days"

# ------------------------------------------------------------------
# 4. Optional: sync to Hetzner Object Storage (uncomment after rclone setup)
# ------------------------------------------------------------------
# rclone sync "${BACKUP_DIR}" hetzner:intelligence-backups/ --max-age 14d

echo "[$(date)] Backup complete."
