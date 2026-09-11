#!/bin/bash
# Triggers the Daily Intelligence Pipeline on GitHub Actions via workflow_dispatch.
# Run by crontab at 07:00 UTC (09:00 CEST) every day.
# Requires GITHUB_TOKEN with actions:write scope (fine-grained PAT: Actions R/W).
#
# On failure this also emails PIPELINE_ALERT_EMAIL. Without that, a failure here is
# invisible: no run is created, so GitHub shows nothing to notice — an expired PAT
# cost two silent days (2026-08-10, 08-11) before anyone spotted the missing reports.

set -euo pipefail

REPO="TommasoFazzi/MACROINTEL"
WORKFLOW="pipeline.yml"
REF="main"
LOG_PREFIX="[$(date -u '+%Y-%m-%d %H:%M:%S UTC')]"

ENV_FILE="/opt/intelligence-ita/repo/.env.production"

# Reads a key from .env.production. Never fails the script when the key is absent:
# under `set -euo pipefail` a bare `grep | cut` returns non-zero on no-match, which
# would kill the run before it could report *which* key is missing.
read_env_var() {
  [ -f "$ENV_FILE" ] || return 0
  grep -m1 "^$1=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' || true
}

# Best-effort ops alert over the Brevo SMTP relay already configured for reports.
# Never allowed to mask the underlying failure, hence the `|| true` at the call site.
notify_failure() {
  local reason="$1" detail="${2:-}"

  local smtp_host smtp_port smtp_user smtp_pass from_email to_email
  smtp_host=$(read_env_var BREVO_SMTP_HOST); smtp_host=${smtp_host:-smtp-relay.brevo.com}
  smtp_port=$(read_env_var BREVO_SMTP_PORT); smtp_port=${smtp_port:-587}
  smtp_user=$(read_env_var BREVO_SMTP_USER)
  smtp_pass=$(read_env_var BREVO_SMTP_PASS)
  from_email=$(read_env_var BREVO_FROM_EMAIL)
  to_email=$(read_env_var PIPELINE_ALERT_EMAIL); to_email=${to_email:-$from_email}

  if [ -z "$smtp_user" ] || [ -z "$smtp_pass" ] || [ -z "$to_email" ]; then
    echo "$LOG_PREFIX WARN: alert email not sent (SMTP credentials or PIPELINE_ALERT_EMAIL missing)" >&2
    return 0
  fi

  curl --silent --show-error --ssl-reqd --max-time 30 \
    --url "smtp://${smtp_host}:${smtp_port}" \
    --user "${smtp_user}:${smtp_pass}" \
    --mail-from "$from_email" \
    --mail-rcpt "$to_email" \
    --upload-file <(cat <<EOF
From: Intelligence ITA Ops <${from_email}>
To: <${to_email}>
Subject: [ALERT] Daily pipeline NOT triggered — ${reason}
Content-Type: text/plain; charset=utf-8

The 07:00 UTC cron could not start the daily pipeline.

  When:   $(date -u '+%Y-%m-%d %H:%M:%S UTC')
  Reason: ${reason}
  Detail: ${detail}

No GitHub Actions run was created, so nothing will appear in the Actions tab.
Today's report will be missing unless the pipeline is started by hand.

If this is "Bad credentials", the GITHUB_TOKEN in .env.production has expired.
Issue a new fine-grained PAT on ${REPO} with Actions: Read and write, update
GITHUB_TOKEN, then verify with:

  bash /opt/intelligence-ita/repo/deploy/trigger_pipeline.sh   # expect HTTP 204

To run today's pipeline immediately:

  gh workflow run ${WORKFLOW} --ref ${REF}
EOF
    ) && echo "$LOG_PREFIX Alert email sent to $to_email" \
      || echo "$LOG_PREFIX WARN: alert email failed to send" >&2
}

if [ -z "${GITHUB_TOKEN:-}" ]; then
  GITHUB_TOKEN=$(read_env_var GITHUB_TOKEN)
fi

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "$LOG_PREFIX ERROR: GITHUB_TOKEN is not set" >&2
  notify_failure "GITHUB_TOKEN is not set" "Neither the environment nor $ENV_FILE provides it." || true
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
  echo >&2
  notify_failure "GitHub returned HTTP $HTTP_STATUS" \
    "$(head -c 400 /tmp/pipeline_trigger_response.json 2>/dev/null | tr -d '\n')" || true
  exit 1
fi
