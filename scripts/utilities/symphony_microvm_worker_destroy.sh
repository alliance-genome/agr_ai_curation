#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${REPO_ROOT}/scripts/lib/symphony_microvm_common.sh"

usage() {
  cat <<'EOF'
Usage:
  symphony_microvm_worker_destroy.sh [--worker-id ID] [--worker-dir DIR] [--dry-run]

Behavior:
  - Stops the Firecracker process for a worker if present.
  - Removes the worker tap device and nftables rules.
  - Leaves final worker-dir removal to the caller.
EOF
}

worker_id="${SYMPHONY_WORKER_ID:-${SYMPHONY_ISSUE_IDENTIFIER:-issue}}"
worker_dir="${SYMPHONY_WORKER_DIR:-$PWD}"
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --worker-id)
      worker_id="${2:-}"
      shift 2
      ;;
    --worker-dir)
      worker_dir="${2:-}"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

network_json="$(symphony_microvm_network_json "${worker_id}")"
tap_name="$(symphony_microvm_json_get "${network_json}" tap_name)"
guest_ip="$(symphony_microvm_json_get "${network_json}" guest_ip)"
pid_path="${worker_dir}/firecracker.pid"
host_iface="${SYMPHONY_MICROVM_HOST_IFACE:-}"
review_proxy_dir="${worker_dir}/review-port-proxies"

if [[ -z "${host_iface}" && -f "${worker_dir}/network.env" ]]; then
  # shellcheck disable=SC1090
  source "${worker_dir}/network.env"
  host_iface="${HOST_IFACE:-${host_iface}}"
fi

if [[ "${dry_run}" -eq 1 ]]; then
  symphony_microvm_output_kv "MICROVM_DESTROY_STATUS" "dry_run"
  symphony_microvm_output_kv "MICROVM_DESTROY_WORKER_ID" "${worker_id}"
  symphony_microvm_output_kv "MICROVM_DESTROY_TAP" "${tap_name}"
  exit 0
fi

if [[ -f "${pid_path}" ]]; then
  firecracker_pid="$(cat "${pid_path}")"
  pid_live=0
  kill_with_sudo=0
  if kill -0 "${firecracker_pid}" >/dev/null 2>&1; then
    pid_live=1
  elif command -v sudo >/dev/null 2>&1 && sudo -n kill -0 "${firecracker_pid}" >/dev/null 2>&1; then
    pid_live=1
    kill_with_sudo=1
  fi

  if [[ "${pid_live}" -eq 1 ]]; then
    if [[ "${kill_with_sudo}" -eq 1 ]]; then
      sudo -n kill "${firecracker_pid}" >/dev/null 2>&1 || true
    else
      kill "${firecracker_pid}" >/dev/null 2>&1 || true
    fi
    for _attempt in $(seq 1 10); do
      if [[ "${kill_with_sudo}" -eq 1 ]]; then
        if ! sudo -n kill -0 "${firecracker_pid}" >/dev/null 2>&1; then
          break
        fi
      elif ! kill -0 "${firecracker_pid}" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    if [[ "${kill_with_sudo}" -eq 1 ]]; then
      sudo -n kill -9 "${firecracker_pid}" >/dev/null 2>&1 || true
    else
      kill -9 "${firecracker_pid}" >/dev/null 2>&1 || true
    fi
  fi
  rm -f "${pid_path}"
fi

if [[ -d "${review_proxy_dir}" ]]; then
  while IFS= read -r pid_file; do
    [[ -f "${pid_file}" ]] || continue
    proxy_pid="$(cat "${pid_file}")"
    kill "${proxy_pid}" >/dev/null 2>&1 || true
    rm -f "${pid_file}"
  done < <(find "${review_proxy_dir}" -type f -name '*.pid' | sort)
  rm -rf "${review_proxy_dir}"
fi

privileged_cleanup_available=0
if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  privileged_cleanup_available=1
fi

if [[ "${privileged_cleanup_available}" -eq 1 ]]; then
  if command -v iptables >/dev/null 2>&1 && [[ -n "${host_iface}" ]]; then
    while sudo -n iptables -t nat -C POSTROUTING -s "${guest_ip}/32" -o "${host_iface}" -m comment --comment "symphony:${worker_id}:nat" -j MASQUERADE >/dev/null 2>&1; do
      sudo -n iptables -t nat -D POSTROUTING -s "${guest_ip}/32" -o "${host_iface}" -m comment --comment "symphony:${worker_id}:nat" -j MASQUERADE >/dev/null 2>&1 || true
    done

    while sudo -n iptables -C FORWARD -i "${tap_name}" -o "${host_iface}" -m comment --comment "symphony:${worker_id}:fwd" -j ACCEPT >/dev/null 2>&1; do
      sudo -n iptables -D FORWARD -i "${tap_name}" -o "${host_iface}" -m comment --comment "symphony:${worker_id}:fwd" -j ACCEPT >/dev/null 2>&1 || true
    done

    while sudo -n iptables -C FORWARD -i "${host_iface}" -o "${tap_name}" -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment "symphony:${worker_id}:return" -j ACCEPT >/dev/null 2>&1; do
      sudo -n iptables -D FORWARD -i "${host_iface}" -o "${tap_name}" -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment "symphony:${worker_id}:return" -j ACCEPT >/dev/null 2>&1 || true
    done
  fi

  if sudo -n nft list table ip symphony_firecracker >/dev/null 2>&1; then
    while read -r handle; do
      [[ -n "${handle}" ]] || continue
      sudo -n nft delete rule ip symphony_firecracker postrouting handle "${handle}" >/dev/null 2>&1 || true
    done < <(sudo -n nft -a list chain ip symphony_firecracker postrouting 2>/dev/null | awk -v marker="symphony:${worker_id}:nat" '$0 ~ marker {print $NF}')

    while read -r handle; do
      [[ -n "${handle}" ]] || continue
      sudo -n nft delete rule ip symphony_firecracker filter handle "${handle}" >/dev/null 2>&1 || true
    done < <(sudo -n nft -a list chain ip symphony_firecracker filter 2>/dev/null | awk -v marker="symphony:${worker_id}:fwd" '$0 ~ marker {print $NF}')
  fi

  sudo -n ip link del "${tap_name}" >/dev/null 2>&1 || true
elif [[ -n "${host_iface}" ]]; then
  echo "Unable to perform privileged network cleanup for ${worker_id}; sudo is required" >&2
  exit 1
fi

rm -f "${worker_dir}/firecracker.socket" "${worker_dir}/vsock.sock" "${worker_dir}/firecracker.log" "${worker_dir}/firecracker.stdout.log" "${worker_dir}/review_ports.json"

symphony_microvm_output_kv "MICROVM_DESTROY_STATUS" "destroyed"
symphony_microvm_output_kv "MICROVM_DESTROY_WORKER_ID" "${worker_id}"
symphony_microvm_output_kv "MICROVM_DESTROY_TAP" "${tap_name}"
