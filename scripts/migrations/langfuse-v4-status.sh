#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: langfuse-v4-status.sh COMPOSE_PROJECT [-f COMPOSE_FILE ...]

Read-only status check for an explicitly selected AI Curation Langfuse v4
Compose deployment. If no Compose files are supplied, docker-compose.yml is
used. Run from the repository root, with sudo when Docker requires it.

This helper does not detect versions, edit .env, start migrations, restart
services, or drop tables.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if (( $# < 1 )); then
  usage >&2
  exit 64
fi

compose_project="$1"
shift

# Avoid Compose warning noise for deployments that intentionally leave this
# optional source selector blank.
export CURATION_DB_CREDENTIALS_SOURCE="${CURATION_DB_CREDENTIALS_SOURCE:-}"

compose_files=("$@")
if (( ${#compose_files[@]} == 0 )); then
  compose_files=(-f docker-compose.yml)
fi

compose=(docker compose -p "${compose_project}" "${compose_files[@]}")

require_container() {
  local service="$1"
  local container_id
  container_id="$("${compose[@]}" ps -q "${service}")"
  if [[ -z "${container_id}" || "${container_id}" == *$'\n'* ]]; then
    echo "Expected exactly one running ${service} container in project ${compose_project}." >&2
    exit 1
  fi
  printf '%s' "${container_id}"
}

"${compose[@]}" config --services >/dev/null

postgres_id="$(require_container postgres)"
worker_id="$(require_container langfuse-worker)"
postgres_user="$(docker exec "${postgres_id}" printenv POSTGRES_USER)"

echo "Langfuse v4 mode"
for key in \
  LANGFUSE_MIGRATION_V4_WRITE_MODE \
  LANGFUSE_MIGRATION_V4_NATIVE_OTEL_BEHAVIOUR \
  LANGFUSE_MIGRATION_V4_ALLOW_PREVIEW_OPT_IN \
  LANGFUSE_BACKGROUND_MIGRATION_V4_ENABLE_HISTORIC_BACKFILL \
  LANGFUSE_BACKGROUND_MIGRATION_V4_DROP_PID_TID_SORTING_TABLES \
  LANGFUSE_EVENT_PROPAGATION_STUCK_THRESHOLD_MINUTES
do
  value="$(docker exec "${worker_id}" printenv "${key}" 2>/dev/null || true)"
  printf '%s=%s\n' "${key}" "${value:-<unset>}"
done

echo
echo "Worker propagation health"
docker exec "${worker_id}" sh -c \
  'wget -qO- "http://${HOSTNAME}:3030/api/health?failIfEventPropagationStuck=true"'
echo

echo
echo "Langfuse v4 background migrations"
docker exec -i "${postgres_id}" \
  psql -v ON_ERROR_STOP=1 -U "${postgres_user}" -d langfuse -P pager=off <<'SQL'
SELECT
    name,
    CASE
        WHEN failed_at IS NOT NULL THEN 'failed'
        WHEN finished_at IS NOT NULL THEN 'finished'
        ELSE 'pending'
    END AS status,
    COALESCE(finished_at::text, '') AS finished_at,
    COALESCE(failed_at::text, '') AS failed_at,
    COALESCE(state->>'phase', '') AS phase
FROM background_migrations
WHERE name LIKE '%v4%'
ORDER BY name;
SQL

echo
echo "Compose services"
"${compose[@]}" ps

echo
echo "Host filesystem containing the checkout"
df -h .
