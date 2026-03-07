#!/usr/bin/env bash
# Status check for Symphony-managed local DB tunnels.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/local_db_tunnel_common.sh
source "${SCRIPT_DIR}/../lib/local_db_tunnel_common.sh"

WORKSPACE_DIR="${PWD}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      WORKSPACE_DIR="$2"
      shift 2
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
  exit 1
fi

# shellcheck disable=SC1090
source "${STATE_FILE}"

ssm_status="down"
socat_status="down"
local_listener="down"
docker_listener="down"

if local_db_tunnel_pid_running "${SSM_PID:-}"; then
  ssm_status="running"
fi
if local_db_tunnel_pid_running "${SOCAT_PID:-}"; then
  socat_status="running"
fi
if local_db_tunnel_local_probe_ready "${LOCAL_PORT:-0}" 1 0; then
  local_listener="ready"
fi
if local_db_tunnel_tcp_ready "${FORWARD_BIND_IP:-127.0.0.1}" "${DOCKER_PORT:-0}"; then
  docker_listener="ready"
fi

echo "workspace=${WORKSPACE_DIR}"
echo "state_file=${STATE_FILE}"
echo "env_file=${ENV_FILE_PATH:-}"
echo "ssm_pid=${SSM_PID:-}"
echo "ssm_status=${ssm_status}"
echo "socat_pid=${SOCAT_PID:-}"
echo "socat_status=${socat_status}"
echo "local_port=${LOCAL_PORT:-}"
echo "local_listener=${local_listener}"
echo "docker_port=${DOCKER_PORT:-}"
echo "forward_bind_ip=${FORWARD_BIND_IP:-}"
echo "forward_host=${FORWARD_HOST:-}"
echo "docker_listener=${docker_listener}"
echo "ssm_session_id=${SSM_SESSION_ID:-}"

if [[ "${ssm_status}" == "running" && "${socat_status}" == "running" && \
      "${local_listener}" == "ready" && "${docker_listener}" == "ready" ]]; then
  exit 0
fi

exit 1
