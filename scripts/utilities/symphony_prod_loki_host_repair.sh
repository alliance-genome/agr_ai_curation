#!/usr/bin/env bash
# Restart the host-owned production Loki read-only endpoint.
#
# Prints a PREFLIGHT diagnostic (SSH key, VPN, listeners, prior state/logs, and
# the config it will use) before repairing, and a STATUS summary after, so a run
# self-explains the common failure modes (locked SSH key, VPN down, stale
# tunnel) instead of failing opaquely. Host-side only; does not touch Incus VM
# workloads.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/prod_loki_tunnel_common.sh
source "${SCRIPT_DIR}/../lib/prod_loki_tunnel_common.sh"

bind_port="${SYMPHONY_PROD_LOKI_PORT:-43100}"
raw_port="${SYMPHONY_PROD_LOKI_RAW_PORT:-43101}"
control_port="${SYMPHONY_PROD_LOKI_CONTROL_PORT:-43102}"
remote_host="${SYMPHONY_PROD_LOKI_REMOTE_HOST:-172.31.29.141}"
remote_port="${SYMPHONY_PROD_LOKI_REMOTE_PORT:-3100}"
pem_file="${SYMPHONY_PROD_LOKI_SSH_KEY:-${HOME}/pem_certs/AGR-ssl3.pem}"
vm_name="${SYMPHONY_INCUS_VM:-symphony-main}"

# up/down for a local listening port (match the Local-Address:Port column exactly).
# Capture ss output first; piping into `grep -q` under `set -o pipefail` makes the
# upstream SIGPIPE turn a real match into a non-zero pipeline (false "down").
_listener() {
  local addrs
  addrs="$(ss -ltnH 2>/dev/null | awk '{print $4}')" || true
  if grep -qE "[:.]${1}$" <<<"${addrs}"; then echo up; else echo down; fi
}

preflight_report() {
  local bind_ip tun route ssh22 state_file st sr f
  bind_ip="$(prod_loki_default_bind_ip 2>/dev/null || echo unknown)"

  echo "[preflight] ===== prod-loki host repair: preflight ====="
  echo "[preflight] config:    bind_ip=${bind_ip} proxy=:${bind_port} control=:${control_port} raw=:${raw_port} -> ubuntu@${remote_host}:${remote_port}"

  if [[ -f "${pem_file}" ]]; then
    echo "[preflight] ssh key:   PRESENT (${pem_file})"
  else
    echo "[preflight] ssh key:   LOCKED/MISSING (${pem_file})  ->  run 'unlock-ssl' on this workstation, then retry"
  fi

  tun="$(ip -4 addr show tun0 2>/dev/null | awk '/inet /{print $2; exit}' || true)"
  echo "[preflight] vpn tun0:  ${tun:-DOWN}"
  if ip route get "${remote_host}" >/dev/null 2>&1; then
    route="$(ip route get "${remote_host}" 2>/dev/null | head -1 || true)"
    echo "[preflight] route:     ${route}"
  else
    echo "[preflight] route:     NONE to ${remote_host} (VPN down?)"
  fi
  if timeout 5 bash -c "</dev/tcp/${remote_host}/22" 2>/dev/null; then ssh22="open"; else ssh22="NOT reachable"; fi
  echo "[preflight] prod :22:  ${ssh22}"

  echo "[preflight] listeners: ${bind_port}=$(_listener "${bind_port}")  ${raw_port}=$(_listener "${raw_port}")  ${control_port}=$(_listener "${control_port}")"

  state_file="$(prod_loki_state_file 2>/dev/null || true)"
  if [[ -n "${state_file}" && -f "${state_file}" ]]; then
    st="$(grep -E '^STARTED_AT=' "${state_file}" 2>/dev/null | cut -d= -f2- | tr -d "'\"" || true)"
    echo "[preflight] prior run: state present, STARTED_AT=${st:-unknown}"
  else
    echo "[preflight] prior run: no state file (fresh start)"
  fi

  # If the control endpoint is down, surface recent log tails to explain why.
  if [[ "$(_listener "${control_port}")" == "down" ]]; then
    sr="$(prod_loki_state_root 2>/dev/null || true)"
    if [[ -n "${sr}" ]]; then
      for log in ssh proxy control; do
        f="${sr}/${log}.log"
        if [[ -s "${f}" ]]; then
          echo "[preflight] last ${log}.log:"
          tail -n 3 "${f}" 2>/dev/null | sed 's/^/[preflight]   /' || true
        fi
      done
    fi
  fi
  echo "[preflight] ==============================================="
}

status_report() {
  local bind_ip url body i
  bind_ip="$(prod_loki_default_bind_ip 2>/dev/null || echo unknown)"
  url="http://${bind_ip}:${control_port}/prod-loki-tunnel/status"
  # the control server spawns detached; give it a moment to bind before reporting
  body=""
  for i in 1 2 3 4 5 6; do
    body="$(curl -fsS -m 5 "${url}" 2>/dev/null || true)"
    [[ -n "${body}" ]] && break
    sleep 0.5
  done
  echo "[status] listeners now: ${bind_port}=$(_listener "${bind_port}")  ${raw_port}=$(_listener "${raw_port}")  ${control_port}=$(_listener "${control_port}")"
  if [[ -n "${body}" ]]; then
    echo "[status] control ${url} -> ${body}"
  else
    echo "[status] control ${url} -> UNREACHABLE (see preflight above; if the ssh key was locked, run 'unlock-ssl' and re-run)"
  fi
  echo "[status] verify from the VM: incus exec ${vm_name} -- curl -fsS -m 6 ${url}"
}

preflight_report || true

bash "${SCRIPT_DIR}/symphony_prod_loki_host_stop.sh" >/dev/null 2>&1 || true

rc=0
bash "${SCRIPT_DIR}/symphony_prod_loki_host_start.sh" "$@" || rc=$?

status_report || true
exit "${rc}"
