#!/usr/bin/env bash
# Fire-and-forget tunnel launcher for Symphony/Human Review Prep.

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

ENV_FILE="${LOCAL_DB_TUNNEL_ENV_FILE:-${WORKSPACE_DIR}/scripts/local_db_tunnel_env.sh}"
STATE_DIR="${LOCAL_DB_TUNNEL_STATE_DIR:-$(local_db_tunnel_state_dir "${WORKSPACE_DIR}")}"
STATE_FILE="${STATE_DIR}/tunnel.state"
SSM_LOG_FILE="${STATE_DIR}/ssm.log"
SOCAT_LOG_FILE="${STATE_DIR}/socat.log"
SSM_PID_FILE="${STATE_DIR}/ssm.pid"
SOCAT_PID_FILE="${STATE_DIR}/socat.pid"
WATCHDOG_LOG_FILE="${STATE_DIR}/watchdog.log"
WATCHDOG_PID_FILE="${STATE_DIR}/watchdog.pid"
TUNNEL_WAIT_ITERATIONS="${LOCAL_DB_TUNNEL_WAIT_ITERATIONS:-60}"
TUNNEL_WAIT_SLEEP_SECONDS="${LOCAL_DB_TUNNEL_WAIT_SLEEP_SECONDS:-2}"
FORWARD_WAIT_ITERATIONS="${LOCAL_DB_TUNNEL_FORWARD_WAIT_ITERATIONS:-30}"
FORWARD_WAIT_SLEEP_SECONDS="${LOCAL_DB_TUNNEL_FORWARD_WAIT_SLEEP_SECONDS:-1}"
KEEPALIVE_INTERVAL_SECONDS="${LOCAL_DB_TUNNEL_KEEPALIVE_INTERVAL_SECONDS:-45}"

mkdir -p "${STATE_DIR}"

cleanup_on_error() {
  local status=$?
  if [[ ${status} -eq 0 ]]; then
    return
  fi

  if [[ -n "${WATCHDOG_PID:-}" ]] && local_db_tunnel_pid_running "${WATCHDOG_PID}"; then
    kill "${WATCHDOG_PID}" 2>/dev/null || true
  fi
  if [[ -n "${SOCAT_PID:-}" ]] && local_db_tunnel_pid_running "${SOCAT_PID}"; then
    kill "${SOCAT_PID}" 2>/dev/null || true
  fi
  if [[ -n "${SSM_PID:-}" ]] && local_db_tunnel_pid_running "${SSM_PID}"; then
    kill "${SSM_PID}" 2>/dev/null || true
  fi
  if [[ -n "${SSM_SESSION_ID:-}" ]]; then
    aws ssm terminate-session --profile "${AWS_PROFILE:-ctabone}" --session-id "${SSM_SESSION_ID}" >/dev/null 2>&1 || true
  fi
  rm -f "${STATE_FILE}" "${ENV_FILE}" "${SSM_LOG_FILE}" "${SOCAT_LOG_FILE}" "${WATCHDOG_LOG_FILE}" "${SSM_PID_FILE}" "${SOCAT_PID_FILE}" "${WATCHDOG_PID_FILE}"
  rmdir "${STATE_DIR}" >/dev/null 2>&1 || true
}
trap cleanup_on_error EXIT

write_state_file() {
  local state_tmp
  state_tmp="$(mktemp "${STATE_DIR}/tunnel.state.XXXXXX")"
  {
    printf 'WORKSPACE_DIR=%q\n' "${WORKSPACE_DIR}"
    printf 'ENV_FILE_PATH=%q\n' "${ENV_FILE}"
    printf 'STATE_DIR=%q\n' "${STATE_DIR}"
    printf 'SSM_PID=%q\n' "${SSM_PID}"
    printf 'SOCAT_PID=%q\n' "${SOCAT_PID}"
    printf 'WATCHDOG_PID=%q\n' "${WATCHDOG_PID:-}"
    printf 'SSM_SESSION_ID=%q\n' "${SSM_SESSION_ID}"
    printf 'LOCAL_PORT=%q\n' "${LOCAL_PORT}"
    printf 'DOCKER_PORT=%q\n' "${DOCKER_PORT}"
    printf 'FORWARD_BIND_IP=%q\n' "${FORWARD_BIND_IP}"
    printf 'FORWARD_HOST=%q\n' "${FORWARD_HOST}"
    printf 'SSM_LOG_FILE=%q\n' "${SSM_LOG_FILE}"
    printf 'SOCAT_LOG_FILE=%q\n' "${SOCAT_LOG_FILE}"
    printf 'WATCHDOG_LOG_FILE=%q\n' "${WATCHDOG_LOG_FILE}"
  } > "${state_tmp}"
  mv "${state_tmp}" "${STATE_FILE}"
}

spawn_watchdog() {
  WATCHDOG_PID="$(
    local_db_tunnel_spawn_detached \
      "${WATCHDOG_PID_FILE}" \
      "${WATCHDOG_LOG_FILE}" \
      bash -lc '
        set -euo pipefail
        script_dir="$1"
        workspace_dir="$2"
        state_file="$3"
        interval_seconds="$4"
        while true; do
          if [[ ! -f "${state_file}" ]]; then
            exit 0
          fi

          # shellcheck disable=SC1090
          source "${state_file}"

          if ! "${script_dir}/symphony_local_db_tunnel_status.sh" --workspace-dir "${workspace_dir}" >/dev/null 2>&1; then
            echo "watchdog: tunnel unhealthy, requesting restart"
            bash "${script_dir}/symphony_local_db_tunnel_start.sh" --workspace-dir "${workspace_dir}" >/dev/null 2>&1 &
            exit 0
          fi

          if command -v pg_isready >/dev/null 2>&1; then
            pg_isready -h localhost -p "${LOCAL_PORT}" -t 2 >/dev/null 2>&1 || true
          else
            (exec 3<>"/dev/tcp/127.0.0.1/${LOCAL_PORT}") >/dev/null 2>&1 || true
          fi

          sleep "${interval_seconds}"
        done
      ' bash "${SCRIPT_DIR}" "${WORKSPACE_DIR}" "${STATE_FILE}" "${KEEPALIVE_INTERVAL_SECONDS}"
  )"
}

if [[ -f "${STATE_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${STATE_FILE}"
  if local_db_tunnel_local_probe_ready "${LOCAL_PORT:-0}" 1 0 && \
     local_db_tunnel_wait_for_listener "${FORWARD_BIND_IP:-127.0.0.1}" "${DOCKER_PORT:-0}" 1 0; then
    if [[ -z "${WATCHDOG_PID:-}" ]] || ! local_db_tunnel_pid_running "${WATCHDOG_PID}"; then
      spawn_watchdog
      write_state_file
    fi
    local_db_tunnel_info "✅ Symphony DB tunnel already running for ${WORKSPACE_DIR}"
    local_db_tunnel_info "   Env file: ${ENV_FILE_PATH:-${ENV_FILE}}"
    exit 0
  fi

  "${SCRIPT_DIR}/symphony_local_db_tunnel_stop.sh" --workspace-dir "${WORKSPACE_DIR}" >/dev/null 2>&1 || true
fi

local_db_tunnel_info "🔧 Starting Symphony local DB tunnel..."
local_db_tunnel_load_private_env
local_db_tunnel_require_prereqs
local_db_tunnel_load_remote_config
local_db_tunnel_choose_ports

FORWARD_BIND_IP="$(local_db_tunnel_resolve_docker_gateway_ip)"
FORWARD_HOST="$(local_db_tunnel_forward_host)"

local_db_tunnel_info "🔒 Tunnel forward bind address: ${FORWARD_BIND_IP}"
local_db_tunnel_info "🔌 Using local port: ${LOCAL_PORT}"
local_db_tunnel_info "🔄 Docker-forward port: ${DOCKER_PORT}"

local_db_tunnel_write_env_file \
  "${ENV_FILE}" \
  "${DB_NAME}" \
  "${DB_USER}" \
  "${DB_PASSWORD}" \
  "${LOCAL_PORT}" \
  "${DOCKER_PORT}" \
  "${FORWARD_BIND_IP}" \
  "${FORWARD_HOST}"

local_db_tunnel_info "✅ Created environment file: ${ENV_FILE}"

if command -v timeout >/dev/null 2>&1; then
  SSM_PID="$(
    local_db_tunnel_spawn_detached \
      "${SSM_PID_FILE}" \
      "${SSM_LOG_FILE}" \
      timeout --foreground "${LOCAL_DB_TUNNEL_MAX_RUNTIME:-4h}" \
      aws ssm start-session \
        --profile "${AWS_PROFILE:-ctabone}" \
        --target "${SSM_INSTANCE_ID}" \
        --document-name AWS-StartPortForwardingSessionToRemoteHost \
        --parameters "{\"host\":[\"${DB_HOST}\"],\"portNumber\":[\"${DB_PORT}\"],\"localPortNumber\":[\"${LOCAL_PORT}\"]}"
  )"
else
  SSM_PID="$(
    local_db_tunnel_spawn_detached \
      "${SSM_PID_FILE}" \
      "${SSM_LOG_FILE}" \
      aws ssm start-session \
        --profile "${AWS_PROFILE:-ctabone}" \
        --target "${SSM_INSTANCE_ID}" \
        --document-name AWS-StartPortForwardingSessionToRemoteHost \
        --parameters "{\"host\":[\"${DB_HOST}\"],\"portNumber\":[\"${DB_PORT}\"],\"localPortNumber\":[\"${LOCAL_PORT}\"]}"
  )"
fi
sleep 2
if ! local_db_tunnel_pid_running "${SSM_PID}"; then
  local_db_tunnel_error "❌ SSM session failed to start"
  tail -n 20 "${SSM_LOG_FILE}" 2>/dev/null || true
  exit 1
fi

if ! local_db_tunnel_wait_for_tunnel "${LOCAL_PORT}" "${TUNNEL_WAIT_ITERATIONS}" "${TUNNEL_WAIT_SLEEP_SECONDS}"; then
  local_db_tunnel_error "❌ Timeout waiting for tunnel on localhost:${LOCAL_PORT}"
  tail -n 20 "${SSM_LOG_FILE}" 2>/dev/null || true
  exit 1
fi

SSM_SESSION_ID="$(local_db_tunnel_extract_session_id "${SSM_LOG_FILE}")"

SOCAT_PID="$(
  local_db_tunnel_spawn_detached \
    "${SOCAT_PID_FILE}" \
    "${SOCAT_LOG_FILE}" \
    socat \
      "TCP-LISTEN:${DOCKER_PORT},bind=${FORWARD_BIND_IP},fork,reuseaddr" \
      "TCP:127.0.0.1:${LOCAL_PORT}"
)"
sleep 1

if ! local_db_tunnel_pid_running "${SOCAT_PID}"; then
  local_db_tunnel_error "❌ socat forwarding failed to start"
  tail -n 20 "${SOCAT_LOG_FILE}" 2>/dev/null || true
  exit 1
fi

if ! local_db_tunnel_wait_for_listener "${FORWARD_BIND_IP}" "${DOCKER_PORT}" "${FORWARD_WAIT_ITERATIONS}" "${FORWARD_WAIT_SLEEP_SECONDS}"; then
  local_db_tunnel_error "❌ Forwarded listener did not become ready on ${FORWARD_BIND_IP}:${DOCKER_PORT}"
  tail -n 20 "${SOCAT_LOG_FILE}" 2>/dev/null || true
  exit 1
fi

if command -v psql >/dev/null 2>&1; then
  if ! PGPASSWORD="${DB_PASSWORD}" psql \
    -h localhost -p "${LOCAL_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
    -c "SELECT current_database();" >/dev/null 2>&1; then
    local_db_tunnel_error "❌ Database connection verification failed"
    exit 1
  fi
fi

write_state_file
spawn_watchdog
write_state_file

local_db_tunnel_info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
local_db_tunnel_info "✅ Symphony DB tunnel is ready"
local_db_tunnel_info "   Workspace: ${WORKSPACE_DIR}"
local_db_tunnel_info "   Host shell: localhost:${LOCAL_PORT}"
local_db_tunnel_info "   Docker path: ${FORWARD_HOST}:${DOCKER_PORT}"
local_db_tunnel_info "   Env file: ${ENV_FILE}"
local_db_tunnel_info "   Status: ./scripts/utilities/symphony_local_db_tunnel_status.sh --workspace-dir \"${WORKSPACE_DIR}\""
local_db_tunnel_info "   Stop:   ./scripts/utilities/symphony_local_db_tunnel_stop.sh --workspace-dir \"${WORKSPACE_DIR}\""
local_db_tunnel_info "   Watchdog PID: ${WATCHDOG_PID}"
local_db_tunnel_info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

trap - EXIT
