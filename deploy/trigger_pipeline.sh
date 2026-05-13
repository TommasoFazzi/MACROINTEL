#!/bin/bash
# Triggers the Daily Intelligence Pipeline on GitHub Actions via workflow_dispatch.
# Run by crontab at 07:00 UTC (09:00 CEST) every day.
# Requires GITHUB_TOKEN env var with actions:write scope.

set -euo pipefail

REPO="TommasoFazzi/MACROINTEL"
WORKFLOW="pipeline.yml"
REF="main"
LOG_PREFIX="[$(date -u '+%Y-%m-%d %H:%M:%S UTC')]"

ENV_FILE="/opt/intelligence-ita/repo/.env.production"
if [ -z "${GITHUB_TOKEN:-}" ] && [ -f "$ENV_FILE" ]; then
  GITHUB_TOKEN=$(grep ^GITHUB_TOKEN "$ENV_FILE" | cut -d= -f2)
fi

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "$LOG_PREFIX ERROR: GITHUB_TOKEN is not set" >&2
  exit 1
fi

HTTP_STATUS=$(curl -s -o /tmp/pipeline_trigger_response.json -w "%{http_code}" \
  -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches" \
  -d "{\"ref\":\"${REF}\"}")

if [ "$HTTP_STATUS" = "204" ]; then
  echo "$LOG_PREFIX Pipeline triggered successfully (HTTP 204)"
else
  echo "$LOG_PREFIX ERROR: unexpected HTTP status $HTTP_STATUS" >&2
  cat /tmp/pipeline_trigger_response.json >&2
  exit 1
fi
