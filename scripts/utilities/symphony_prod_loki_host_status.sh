#!/usr/bin/env bash
# Report host-side production Loki tunnel/proxy status.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prod_loki_tunnel_common.sh
source "${SCRIPT_DIR}/../lib/prod_loki_tunnel_common.sh"

json=0
shell_env=0
quiet=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)
      json=1
      shift
      ;;
    --shell-env)
      shell_env=1
      shift
      ;;
    --quiet)
      quiet=1
      shift
      ;;
    --help|-h)
      echo "Usage: symphony_prod_loki_host_status.sh [--json] [--shell-env] [--quiet]"
      exit 0
      ;;
    *)
      prod_loki_error "Unknown argument: $1"
      exit 2
      ;;
  esac
done

state_file="$(prod_loki_state_file)"
status="offline"
raw_tunnel_status="missing"
proxy_status="missing"
ready_status="unknown"
LOKI_URL=""
BIND_IP=""
BIND_PORT=""
TRANSPORT=""

if [[ -f "${state_file}" ]]; then
  # shellcheck disable=SC1090
  source "${state_file}"
  if prod_loki_pid_running "${SSH_PID:-}"; then
    raw_tunnel_status="running"
  else
    raw_tunnel_status="stopped"
  fi
  if prod_loki_pid_running "${PROXY_PID:-}"; then
    proxy_status="running"
  else
    proxy_status="stopped"
  fi
  if [[ -n "${LOKI_URL:-}" ]] && curl -fsS -m 3 "${LOKI_URL%/}/ready" >/dev/null 2>&1; then
    ready_status="ready"
  else
    ready_status="not_ready"
  fi
  if [[ "${raw_tunnel_status}" == "running" && "${proxy_status}" == "running" && "${ready_status}" == "ready" ]]; then
    status="online"
  elif [[ "${raw_tunnel_status}" == "running" || "${proxy_status}" == "running" ]]; then
    status="degraded"
  fi
fi

if [[ "${shell_env}" -eq 1 ]]; then
  [[ -n "${LOKI_URL:-}" ]] && printf 'export LOKI_URL=%q\n' "${LOKI_URL}"
  exit 0
fi

if [[ "${json}" -eq 1 ]]; then
  python3 - "$status" "${LOKI_URL:-}" "${TRANSPORT:-}" "${BIND_IP:-}" "${BIND_PORT:-}" "${raw_tunnel_status}" "${proxy_status}" "${ready_status}" <<'PY'
import json
import sys
from datetime import datetime, timezone

status, url, transport, bind_ip, bind_port, raw, proxy, ready = sys.argv[1:]
payload = {
    "status": status,
    "loki_url": url,
    "transport": transport,
    "bind_ip": bind_ip,
    "bind_port": int(bind_port) if bind_port.isdigit() else None,
    "raw_tunnel_status": raw,
    "proxy_status": proxy,
    "ready_status": ready,
    "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
}
print(json.dumps(payload, sort_keys=True))
PY
else
  if [[ "${quiet}" -ne 1 ]]; then
    echo "status=${status}"
    echo "loki_url=${LOKI_URL:-}"
    echo "transport=${TRANSPORT:-}"
    echo "bind_ip=${BIND_IP:-}"
    echo "bind_port=${BIND_PORT:-}"
    echo "raw_tunnel_status=${raw_tunnel_status}"
    echo "proxy_status=${proxy_status}"
    echo "ready_status=${ready_status}"
  fi
fi

if [[ "${status}" == "online" ]]; then
  exit 0
fi
exit 1
