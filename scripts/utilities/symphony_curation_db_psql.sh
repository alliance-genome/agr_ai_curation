#!/usr/bin/env bash
# Start/reuse the Symphony read-only curation DB tunnel and run real psql.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Usage:
  symphony_curation_db_psql.sh [options] -- [psql arguments...]

Purpose:
  Start or reuse the Symphony read-only AGR curation DB tunnel, source the
  workspace-local tunnel env file, and execute the real `psql` client with the
  read-only credentials from AWS Secrets Manager.

Options:
  --workspace-dir DIR      Workspace directory. Default: current directory.
  --env-file PATH          Tunnel env file. Default: <workspace>/scripts/local_db_tunnel_env.sh.
  --no-start-tunnel        Do not start the tunnel; require the env file to exist.
  --status                 Print tunnel status for the workspace and exit.
  --print-connection       Print non-secret connection details before running psql.
  --help                   Show this help.

Examples:
  bash scripts/utilities/symphony_curation_db_psql.sh -- \
    -c "select current_database(), current_user;"

  bash scripts/utilities/symphony_curation_db_psql.sh -- \
    -c "select table_schema, table_name from information_schema.tables where table_schema not in ('pg_catalog','information_schema') order by 1,2 limit 50;"

Notes:
  - This helper does not parse or rewrite SQL; after setup, it hands off to real psql.
  - Credentials come from the read-only `ai-curation/db/curation-readonly` secret.
  - Session defaults set `default_transaction_read_only=on`, statement timeout, and lock timeout.
  - Never print or paste the generated env file; it contains credentials.
EOF
}

workspace_dir="${PWD}"
env_file=""
start_tunnel=1
status_only=0
print_connection=0
psql_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      workspace_dir="${2:-}"
      shift 2
      ;;
    --env-file)
      env_file="${2:-}"
      shift 2
      ;;
    --no-start-tunnel)
      start_tunnel=0
      shift
      ;;
    --status)
      status_only=1
      shift
      ;;
    --print-connection)
      print_connection=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      psql_args=("$@")
      break
      ;;
    *)
      echo "Unknown argument before --: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${workspace_dir}" ]]; then
  echo "--workspace-dir cannot be empty" >&2
  exit 2
fi

workspace_dir="$(cd "${workspace_dir}" && pwd -P)"
env_file="${env_file:-${workspace_dir}/scripts/local_db_tunnel_env.sh}"

resolve_helper() {
  local rel_path="$1"
  local workspace_path="${workspace_dir}/${rel_path}"
  local source_path

  if [[ -f "${workspace_path}" ]]; then
    printf '%s\n' "${workspace_path}"
    return 0
  fi

  source_path="${SCRIPT_DIR}/$(basename "${rel_path}")"
  if [[ -f "${source_path}" ]]; then
    printf '%s\n' "${source_path}"
    return 0
  fi

  return 1
}

status_helper="$(resolve_helper "scripts/utilities/symphony_local_db_tunnel_status.sh" || true)"
start_helper="$(resolve_helper "scripts/utilities/symphony_local_db_tunnel_start.sh" || true)"

if [[ "${status_only}" -eq 1 ]]; then
  if [[ -z "${status_helper}" ]]; then
    echo "Missing symphony_local_db_tunnel_status.sh" >&2
    exit 2
  fi
  exec bash "${status_helper}" --workspace-dir "${workspace_dir}"
fi

if ! command -v psql >/dev/null 2>&1; then
  echo "psql is required but was not found in PATH" >&2
  exit 2
fi

if [[ "${start_tunnel}" -eq 1 ]]; then
  if [[ -z "${start_helper}" ]]; then
    echo "Missing symphony_local_db_tunnel_start.sh" >&2
    exit 2
  fi
  bash "${start_helper}" --workspace-dir "${workspace_dir}"
fi

if [[ ! -f "${env_file}" ]]; then
  echo "Missing tunnel env file: ${env_file}" >&2
  echo "Run: bash scripts/utilities/symphony_curation_db_psql.sh --workspace-dir \"${workspace_dir}\" -- -c \"select current_database(), current_user;\"" >&2
  exit 3
fi

# shellcheck disable=SC1090
source "${env_file}"

required_vars=(
  PERSISTENT_STORE_DB_HOST
  PERSISTENT_STORE_DB_PORT
  PERSISTENT_STORE_DB_NAME
  PERSISTENT_STORE_DB_USERNAME
  PERSISTENT_STORE_DB_PASSWORD
)

missing=()
for var in "${required_vars[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    missing+=("${var}")
  fi
done

if [[ "${#missing[@]}" -gt 0 ]]; then
  printf 'Tunnel env file is missing required values: %s\n' "${missing[*]}" >&2
  exit 3
fi

export PGPASSWORD="${PERSISTENT_STORE_DB_PASSWORD}"
export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-10}"
export PGAPPNAME="${PGAPPNAME:-symphony-codex-readonly}"
export PGOPTIONS="${PGOPTIONS:+${PGOPTIONS} }-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=5000"

if [[ "${print_connection}" -eq 1 ]]; then
  echo "curation_db_host=${PERSISTENT_STORE_DB_HOST}"
  echo "curation_db_port=${PERSISTENT_STORE_DB_PORT}"
  echo "curation_db_name=${PERSISTENT_STORE_DB_NAME}"
  echo "curation_db_user=${PERSISTENT_STORE_DB_USERNAME}"
  echo "curation_db_readonly=true"
fi

if [[ "${#psql_args[@]}" -eq 0 && ( ! -t 0 || ! -t 1 ) ]]; then
  psql_args=(-c "select current_database(), current_user;")
fi

exec psql \
  -h "${PERSISTENT_STORE_DB_HOST}" \
  -p "${PERSISTENT_STORE_DB_PORT}" \
  -U "${PERSISTENT_STORE_DB_USERNAME}" \
  -d "${PERSISTENT_STORE_DB_NAME}" \
  -v ON_ERROR_STOP=1 \
  -P pager=off \
  "${psql_args[@]}"
