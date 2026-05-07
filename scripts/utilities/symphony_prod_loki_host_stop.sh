#!/usr/bin/env bash
# Stop the host-owned production Loki raw tunnel and read-only proxy.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prod_loki_tunnel_common.sh
source "${SCRIPT_DIR}/../lib/prod_loki_tunnel_common.sh"

keep_state=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-state)
      keep_state=1
      shift
      ;;
    --help|-h)
      echo "Usage: symphony_prod_loki_host_stop.sh [--keep-state]"
      exit 0
      ;;
    *)
      prod_loki_error "Unknown argument: $1"
      exit 2
      ;;
  esac
done

state_root="$(prod_loki_state_root)"
state_file="${state_root}/tunnel.state"
endpoint_file="$(prod_loki_endpoint_file "$(prod_loki_repo_root)")"

if [[ -f "${state_file}" ]]; then
  # shellcheck disable=SC1090
  source "${state_file}"
fi

stop_pid_if_expected() {
  local pid="${1:-}"
  local expected="$2"
  if ! prod_loki_pid_running "${pid}"; then
    return 0
  fi
  local cmd
  cmd="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  if [[ "${cmd}" != *"${expected}"* ]]; then
    prod_loki_warn "Refusing to kill PID ${pid}; command did not contain expected marker '${expected}'"
    prod_loki_warn "Command: ${cmd}"
    return 1
  fi
  kill "${pid}" 2>/dev/null || true
}

stop_pid_if_expected "${PROXY_PID:-}" "symphony_prod_loki_readonly_proxy.py" || true
stop_pid_if_expected "${SSH_PID:-}" "127.0.0.1:${RAW_PORT:-43101}:127.0.0.1:${REMOTE_PORT:-3100}" || true

if [[ "${keep_state}" -ne 1 ]]; then
  rm -f \
    "${state_file}" \
    "${state_root}/ssh.pid" \
    "${state_root}/proxy.pid" \
    "${SSH_LOG_FILE:-${state_root}/ssh.log}" \
    "${PROXY_LOG_FILE:-${state_root}/proxy.log}" \
    "${endpoint_file}"
fi

prod_loki_info "Production Loki tunnel/proxy stopped"
