#!/usr/bin/env bash

set -euo pipefail

symphony_microvm_repo_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "${script_dir}/../.." && pwd
}

symphony_microvm_assets_root() {
  printf '%s\n' "${SYMPHONY_MICROVM_ASSETS_ROOT:-${HOME}/.symphony/microvm_assets/agr_ai_curation}"
}

symphony_microvm_arch() {
  local arch
  arch="$(uname -m)"
  case "${arch}" in
    x86_64|aarch64)
      printf '%s\n' "${arch}"
      ;;
    *)
      echo "Unsupported architecture: ${arch}" >&2
      return 1
      ;;
  esac
}

symphony_microvm_latest_firecracker_version() {
  curl -fsSL https://api.github.com/repos/firecracker-microvm/firecracker/releases/latest | jq -r '.tag_name'
}

symphony_microvm_ci_version_for_release() {
  local release_tag="$1"
  printf '%s\n' "${release_tag%.*}"
}

symphony_microvm_tap_name() {
  local worker_id="$1"
  python3 - "$worker_id" <<'PY'
import hashlib, sys
worker_id = sys.argv[1] or "issue"
print("fc" + hashlib.sha1(worker_id.encode()).hexdigest()[:11])
PY
}

symphony_microvm_network_json() {
  local worker_id="$1"
  python3 - "$worker_id" <<'PY'
import hashlib, json, sys

worker_id = sys.argv[1] or "issue"
digest = hashlib.sha1(worker_id.encode()).digest()
idx = int.from_bytes(digest[:2], "big") % (256 * 64)
third = idx // 64
network_base = (idx % 64) * 4
host_ip = f"172.19.{third}.{network_base + 1}"
guest_ip = f"172.19.{third}.{network_base + 2}"
guest_cid = 10000 + idx
tap_name = "fc" + hashlib.sha1(worker_id.encode()).hexdigest()[:11]
mac = "06:00:%02x:%02x:%02x:%02x" % tuple(int(part) for part in guest_ip.split("."))
print(json.dumps({
    "host_ip": host_ip,
    "guest_ip": guest_ip,
    "guest_cid": guest_cid,
    "tap_name": tap_name,
    "mac_address": mac,
    "netmask_bits": 30,
}))
PY
}

symphony_microvm_default_host_iface() {
  ip route show default 2>/dev/null | awk '/default/ {print $5; exit}'
}

symphony_microvm_require_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "Missing required command: ${cmd}" >&2
    return 1
  fi
}

symphony_microvm_require_sudo_noninteractive() {
  if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    return 0
  fi

  cat >&2 <<'EOF'
This script needs non-interactive sudo for networking or image preparation.
Required remediation:
  1. Ensure your user can run sudo without an interactive prompt for these commands, or
  2. Pre-stage the needed network/image resources manually.
EOF
  return 1
}

symphony_microvm_output_kv() {
  local key="$1"
  local value="${2:-}"
  printf '%s=%s\n' "${key}" "${value}"
}

symphony_microvm_json_get() {
  local json="$1"
  local key="$2"
  python3 - "$json" "$key" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
print(payload[sys.argv[2]])
PY
}

symphony_microvm_ssh_opts() {
  local key_path="$1"
  printf '%s\n' "-i ${key_path} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes -o ConnectTimeout=5"
}
