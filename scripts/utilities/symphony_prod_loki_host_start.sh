#!/usr/bin/env bash
# Start/reuse the host-owned production Loki read-only tunnel and proxy.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prod_loki_tunnel_common.sh
source "${SCRIPT_DIR}/../lib/prod_loki_tunnel_common.sh"

usage() {
  cat <<'EOF'
Usage:
  symphony_prod_loki_host_start.sh [options]

Options:
  --transport ssh          Only supported transport in v1.
  --bind-ip IP             Host Incus IP reachable from symphony-main.
  --port PORT              VM-facing proxy port. Default: 43100.
  --raw-port PORT          Host-local raw SSH tunnel port. Default: 43101.
  --remote-host IP         Production host. Default: 172.31.29.141.
  --remote-port PORT       Production Loki port. Default: 3100.
  --foreground             Keep proxy in foreground; useful for systemd.
  --print-env              Print non-secret LOKI_URL exports.
  --help                   Show this help.
EOF
}

transport="ssh"
bind_ip=""
bind_port="${SYMPHONY_PROD_LOKI_PORT:-43100}"
raw_port="${SYMPHONY_PROD_LOKI_RAW_PORT:-43101}"
control_port="${SYMPHONY_PROD_LOKI_CONTROL_PORT:-43102}"
remote_host="${SYMPHONY_PROD_LOKI_REMOTE_HOST:-172.31.29.141}"
remote_port="${SYMPHONY_PROD_LOKI_REMOTE_PORT:-3100}"
foreground=0
print_env=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --transport)
      transport="${2:-}"
      shift 2
      ;;
    --bind-ip)
      bind_ip="${2:-}"
      shift 2
      ;;
    --port)
      bind_port="${2:-}"
      shift 2
      ;;
    --raw-port)
      raw_port="${2:-}"
      shift 2
      ;;
    --remote-host)
      remote_host="${2:-}"
      shift 2
      ;;
    --remote-port)
      remote_port="${2:-}"
      shift 2
      ;;
    --foreground)
      foreground=1
      shift
      ;;
    --print-env)
      print_env=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      prod_loki_error "Unknown argument: $1"
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${transport}" != "ssh" ]]; then
  prod_loki_error "Only --transport ssh is supported in v1."
  exit 2
fi
prod_loki_validate_port "${bind_port}" || { prod_loki_error "Invalid --port ${bind_port}"; exit 2; }
prod_loki_validate_port "${raw_port}" || { prod_loki_error "Invalid --raw-port ${raw_port}"; exit 2; }
prod_loki_validate_port "${control_port}" || { prod_loki_error "Invalid control port ${control_port}"; exit 2; }

repo_root="$(prod_loki_repo_root)"
endpoint_file="$(prod_loki_endpoint_file "${repo_root}")"
state_root="$(prod_loki_state_root)"
state_file="${state_root}/tunnel.state"
ssh_pid_file="${state_root}/ssh.pid"
proxy_pid_file="${state_root}/proxy.pid"
control_pid_file="${state_root}/control.pid"
ssh_log_file="${state_root}/ssh.log"
proxy_log_file="${state_root}/proxy.log"
control_log_file="${state_root}/control.log"
loki_url=""

if [[ -z "${bind_ip}" ]]; then
  bind_ip="$(prod_loki_default_bind_ip)"
fi
prod_loki_validate_bind_ip "${bind_ip}"
prod_loki_validate_incus_bind_ip "${bind_ip}"
loki_url="http://${bind_ip}:${bind_port}"
control_url="http://${bind_ip}:${control_port}"

write_state_file() {
  local tmp
  tmp="$(mktemp "${state_root}/tunnel.state.XXXXXX")"
  {
    printf 'LOKI_URL=%q\n' "${loki_url}"
    printf 'BIND_IP=%q\n' "${bind_ip}"
    printf 'BIND_PORT=%q\n' "${bind_port}"
    printf 'RAW_PORT=%q\n' "${raw_port}"
    printf 'REMOTE_HOST=%q\n' "${remote_host}"
    printf 'REMOTE_PORT=%q\n' "${remote_port}"
    printf 'TRANSPORT=%q\n' "${transport}"
    printf 'SSH_PID=%q\n' "${SSH_PID:-}"
    printf 'PROXY_PID=%q\n' "${PROXY_PID:-}"
    printf 'SSH_LOG_FILE=%q\n' "${ssh_log_file}"
    printf 'PROXY_LOG_FILE=%q\n' "${proxy_log_file}"
    printf 'CONTROL_LOG_FILE=%q\n' "${control_log_file}"
    printf 'STARTED_AT=%q\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "${tmp}"
  mv "${tmp}" "${state_file}"
}

ensure_control_server() {
  local url="$1"
  local args=(
    python3 "${SCRIPT_DIR}/symphony_prod_loki_host_control_server.py"
    --bind-ip "${bind_ip}"
    --bind-port "${control_port}"
    --repo-root "${repo_root}"
  )

  if ! curl -fsS -m 2 "${url}/prod-loki-tunnel/status" >/dev/null 2>&1; then
    prod_loki_spawn_detached \
      "${control_pid_file}" \
      "${control_log_file}" \
      "${args[@]}" >/dev/null
  fi
}

cleanup_foreground() {
  if [[ -n "${PROXY_PID:-}" ]] && prod_loki_pid_running "${PROXY_PID}"; then
    kill "${PROXY_PID}" 2>/dev/null || true
  fi
  if [[ -n "${SSH_PID:-}" ]] && prod_loki_pid_running "${SSH_PID}"; then
    kill "${SSH_PID}" 2>/dev/null || true
  fi
}

cleanup_on_error() {
  local status=$?
  if [[ "${status}" -eq 0 ]]; then
    return
  fi
  if [[ -n "${PROXY_PID:-}" ]] && prod_loki_pid_running "${PROXY_PID}"; then
    kill "${PROXY_PID}" 2>/dev/null || true
  fi
  if [[ -n "${SSH_PID:-}" ]] && prod_loki_pid_running "${SSH_PID}"; then
    kill "${SSH_PID}" 2>/dev/null || true
  fi
  rm -f "${endpoint_file}"
}

if [[ -f "${state_file}" && "${foreground}" -eq 0 ]]; then
  # shellcheck disable=SC1090
  source "${state_file}"
  if prod_loki_pid_running "${SSH_PID:-}" && prod_loki_pid_running "${PROXY_PID:-}" && prod_loki_wait_for_http_ready "${LOKI_URL:-}" 1 0; then
    ensure_control_server "${control_url}"
    prod_loki_write_endpoint_file "${endpoint_file}" "${LOKI_URL}" "${control_url}"
    prod_loki_sync_endpoint_file_to_vm "${endpoint_file}" || true
    prod_loki_info "Production Loki read-only tunnel already running: ${LOKI_URL}"
    if [[ "${print_env}" -eq 1 ]]; then
      printf 'export LOKI_URL=%q\n' "${LOKI_URL}"
    fi
    exit 0
  fi
  bash "${SCRIPT_DIR}/symphony_prod_loki_host_stop.sh" >/dev/null 2>&1 || true
fi

trap cleanup_on_error EXIT

for required in ssh curl python3; do
  if ! command -v "${required}" >/dev/null 2>&1; then
    prod_loki_error "Missing required command: ${required}"
    exit 2
  fi
done

pem_file="${SYMPHONY_PROD_LOKI_SSH_KEY:-${HOME}/pem_certs/AGR-ssl3.pem}"
if [[ ! -f "${pem_file}" ]]; then
  prod_loki_error "Production SSH key appears locked or unavailable. Ask Chris to run unlock-ssl, then retry."
  exit 3
fi

ssh_args=(
  ssh
  -i "${pem_file}"
  -N
  -o BatchMode=yes
  -o ConnectTimeout=8
  -o ExitOnForwardFailure=yes
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
  -L "127.0.0.1:${raw_port}:127.0.0.1:${remote_port}"
  "ubuntu@${remote_host}"
)

if [[ "${foreground}" -eq 1 ]]; then
  "${ssh_args[@]}" >>"${ssh_log_file}" 2>&1 &
  SSH_PID="$!"
  trap cleanup_foreground EXIT
else
  SSH_PID="$(
    prod_loki_spawn_detached \
      "${ssh_pid_file}" \
      "${ssh_log_file}" \
      "${ssh_args[@]}"
  )"
fi

ssh_ready=0
for _ in $(seq 1 12); do
  if prod_loki_tcp_ready "127.0.0.1" "${raw_port}"; then
    ssh_ready=1
    break
  fi
  if ! prod_loki_pid_running "${SSH_PID}"; then
    break
  fi
  sleep 1
done

if ! prod_loki_pid_running "${SSH_PID}"; then
  prod_loki_error "Production SSH tunnel failed to start."
  if rg -i "permission denied|publickey" "${ssh_log_file}" >/dev/null 2>&1; then
    prod_loki_error "SSH reported Permission denied (publickey). Ask Chris to run unlock-ssh, then retry."
  elif rg -i "connection timed out|no route to host|network is unreachable|operation timed out" "${ssh_log_file}" >/dev/null 2>&1; then
    prod_loki_error "Production host ${remote_host}:22 is unreachable. Check the VPN/network route to the Alliance production VPC, then retry."
  else
    prod_loki_error "If ${pem_file} is locked or unavailable, ask Chris to run unlock-ssl."
  fi
  tail -n 20 "${ssh_log_file}" 2>/dev/null || true
  exit 3
fi

if [[ "${ssh_ready}" -ne 1 ]]; then
  prod_loki_error "Timed out waiting for raw Loki tunnel on 127.0.0.1:${raw_port}"
  tail -n 20 "${ssh_log_file}" 2>/dev/null || true
  exit 1
fi

proxy_args=(
  python3 "${SCRIPT_DIR}/symphony_prod_loki_readonly_proxy.py"
  --bind-ip "${bind_ip}"
  --bind-port "${bind_port}"
  --upstream "http://127.0.0.1:${raw_port}"
)

ensure_control_server "${control_url}"

prod_loki_write_endpoint_file "${endpoint_file}" "${loki_url}" "${control_url}"
prod_loki_sync_endpoint_file_to_vm "${endpoint_file}" || true

if [[ "${foreground}" -eq 1 ]]; then
  "${proxy_args[@]}" >>"${proxy_log_file}" 2>&1 &
  PROXY_PID="$!"
  if ! prod_loki_wait_for_http_ready "${loki_url}" 30 1; then
    prod_loki_error "Timed out waiting for production Loki read-only proxy: ${loki_url}"
    tail -n 20 "${proxy_log_file}" 2>/dev/null || true
    exit 1
  fi
  write_state_file
  prod_loki_info "Production Loki read-only proxy starting: ${loki_url}"
  wait "${PROXY_PID}"
  exit $?
fi

PROXY_PID="$(
  prod_loki_spawn_detached \
    "${proxy_pid_file}" \
    "${proxy_log_file}" \
    "${proxy_args[@]}"
)"

if ! prod_loki_wait_for_http_ready "${loki_url}" 30 1; then
  prod_loki_error "Timed out waiting for production Loki read-only proxy: ${loki_url}"
  tail -n 20 "${proxy_log_file}" 2>/dev/null || true
  bash "${SCRIPT_DIR}/symphony_prod_loki_host_stop.sh" >/dev/null 2>&1 || true
  exit 1
fi

write_state_file

prod_loki_info "Production Loki read-only endpoint is ready: ${loki_url}"
prod_loki_info "Endpoint env file: ${endpoint_file}"
if [[ "${print_env}" -eq 1 ]]; then
  printf 'export LOKI_URL=%q\n' "${loki_url}"
fi

trap - EXIT
