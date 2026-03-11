#!/usr/bin/env bash
# Stop a Symphony-managed local DB tunnel and clean up state.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/local_db_tunnel_common.sh
source "${SCRIPT_DIR}/../lib/local_db_tunnel_common.sh"

WORKSPACE_DIR="${PWD}"
KEEP_ENV_FILE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      WORKSPACE_DIR="$2"
      shift 2
      ;;
    --keep-env-file)
      KEEP_ENV_FILE=1
      shift
      ;;
    *)
      local_db_tunnel_error "Unknown argument: $1"
      exit 1
      ;;
  esac
done

STATE_DIR="${LOCAL_DB_TUNNEL_STATE_DIR:-$(local_db_tunnel_state_dir "${WORKSPACE_DIR}")}"
STATE_FILE="${STATE_DIR}/tunnel.state"

if [[ ! -f "${STATE_FILE}" ]]; then
  local_db_tunnel_warn "⚠️  No Symphony DB tunnel state found for ${WORKSPACE_DIR}"
  exit 0
fi

# shellcheck disable=SC1090
source "${STATE_FILE}"

if local_db_tunnel_pid_running "${SOCAT_PID:-}"; then
  kill "${SOCAT_PID}" 2>/dev/null || true
fi

if local_db_tunnel_pid_running "${SSM_PID:-}"; then
  kill "${SSM_PID}" 2>/dev/null || true
fi

if [[ -n "${SSM_SESSION_ID:-}" ]]; then
  aws ssm terminate-session --profile "${AWS_PROFILE:-ctabone}" --session-id "${SSM_SESSION_ID}" >/dev/null 2>&1 || true
fi

if [[ ${KEEP_ENV_FILE} -ne 1 && -n "${ENV_FILE_PATH:-}" ]]; then
  rm -f "${ENV_FILE_PATH}"
fi

rm -f "${STATE_FILE}" "${SSM_LOG_FILE:-}" "${SOCAT_LOG_FILE:-}" "${STATE_DIR}/ssm.pid" "${STATE_DIR}/socat.pid"
rmdir "${STATE_DIR}" >/dev/null 2>&1 || true

local_db_tunnel_info "✅ Stopped Symphony DB tunnel for ${WORKSPACE_DIR}"
