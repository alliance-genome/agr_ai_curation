#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${REPO_ROOT}/scripts/lib/symphony_microvm_common.sh"

usage() {
  cat <<'EOF'
Usage:
  symphony_microvm_worker_create.sh [--worker-id ID] [--worker-dir DIR] [--assets-root DIR] [--dry-run]

Behavior:
  - Ensures Firecracker assets exist.
  - Creates a per-worker Firecracker config, rootfs copy, and network allocation.
  - Boots a persistent microVM and waits for SSH reachability.
EOF
}

worker_id="${SYMPHONY_WORKER_ID:-${SYMPHONY_ISSUE_IDENTIFIER:-issue}}"
worker_dir="${SYMPHONY_WORKER_DIR:-$PWD}"
assets_root="$(symphony_microvm_assets_root)"
dry_run=0
auto_prepare=1

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
    --assets-root)
      assets_root="${2:-}"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --no-auto-prepare)
      auto_prepare=0
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

arch="$(symphony_microvm_arch)"
release_tag="${SYMPHONY_FIRECRACKER_VERSION:-$(symphony_microvm_latest_firecracker_version)}"
ci_version="$(symphony_microvm_ci_version_for_release "${release_tag}")"
firecracker_bin="${assets_root}/bin/${release_tag}/${arch}/firecracker"
jailer_bin="${assets_root}/bin/${release_tag}/${arch}/jailer"
kernel_path="${assets_root}/images/${ci_version}/${arch}/vmlinux.bin"
base_rootfs="${assets_root}/images/${ci_version}/${arch}/ubuntu.ext4"
ssh_key_path="${assets_root}/ssh/id_rsa"
required_rootfs_size_mb="${SYMPHONY_MICROVM_ROOTFS_SIZE_MB:-8192}"
network_json="$(symphony_microvm_network_json "${worker_id}")"
host_ip="$(symphony_microvm_json_get "${network_json}" host_ip)"
guest_ip="$(symphony_microvm_json_get "${network_json}" guest_ip)"
guest_cid="$(symphony_microvm_json_get "${network_json}" guest_cid)"
tap_name="$(symphony_microvm_json_get "${network_json}" tap_name)"
mac_address="$(symphony_microvm_json_get "${network_json}" mac_address)"
host_iface="${SYMPHONY_MICROVM_HOST_IFACE:-$(symphony_microvm_default_host_iface)}"
vcpu_count="${SYMPHONY_MICROVM_VCPU_COUNT:-2}"
mem_size_mib="${SYMPHONY_MICROVM_MEM_SIZE_MIB:-4096}"
api_socket="${worker_dir}/firecracker.socket"
config_path="${worker_dir}/firecracker-config.json"
log_path="${worker_dir}/firecracker.log"
pid_path="${worker_dir}/firecracker.pid"
net_state_path="${worker_dir}/network.env"
guest_rootfs="${worker_dir}/rootfs.ext4"
vsock_path="${worker_dir}/vsock.sock"
guest_bootstrap_script="/root/symphony/symphony_microvm_guest_bootstrap.sh"
review_ports_json_path="${worker_dir}/review_ports.json"
review_proxy_dir="${worker_dir}/review-port-proxies"
allocate_ports_script="${REPO_ROOT}/.symphony/allocate_issue_ports.sh"
public_review_bind_host="${SYMPHONY_PUBLIC_REVIEW_BIND_HOST:-0.0.0.0}"
internal_review_bind_host="${SYMPHONY_INTERNAL_REVIEW_BIND_HOST:-127.0.0.1}"

for cmd in python3 jq ip ssh scp iptables socat; do
  symphony_microvm_require_cmd "${cmd}"
done

mkdir -p "${worker_dir}"

cleanup_on_error=1
cleanup_worker_create_failure() {
  local exit_code=$?
  if [[ "${cleanup_on_error}" -eq 1 ]]; then
    bash "${SCRIPT_DIR}/symphony_microvm_worker_destroy.sh" --worker-id "${worker_id}" --worker-dir "${worker_dir}" >/dev/null 2>&1 || true
  fi
  exit "${exit_code}"
}

trap cleanup_worker_create_failure ERR

cat > "${net_state_path}" <<EOF
WORKER_ID=${worker_id}
HOST_IP=${host_ip}
GUEST_IP=${guest_ip}
GUEST_CID=${guest_cid}
TAP_NAME=${tap_name}
MAC_ADDRESS=${mac_address}
HOST_IFACE=${host_iface}
EOF

if [[ "${dry_run}" -eq 1 ]]; then
  if [[ -x "${allocate_ports_script}" ]]; then
    # shellcheck disable=SC1090,SC2046
    eval "$("${allocate_ports_script}" "${worker_id}")"
    symphony_microvm_output_kv "MICROVM_CREATE_REVIEW_FRONTEND_URL" "${REVIEW_FRONTEND_URL:-}"
    symphony_microvm_output_kv "MICROVM_CREATE_REVIEW_BACKEND_URL" "${REVIEW_BACKEND_URL:-}"
  fi
  symphony_microvm_output_kv "MICROVM_CREATE_STATUS" "dry_run"
  symphony_microvm_output_kv "MICROVM_CREATE_WORKER_ID" "${worker_id}"
  symphony_microvm_output_kv "MICROVM_CREATE_WORKER_DIR" "${worker_dir}"
  symphony_microvm_output_kv "MICROVM_CREATE_FIRECRACKER_BIN" "${firecracker_bin}"
  symphony_microvm_output_kv "MICROVM_CREATE_JAILER_BIN" "${jailer_bin}"
  symphony_microvm_output_kv "MICROVM_CREATE_HOST_IP" "${host_ip}"
  symphony_microvm_output_kv "MICROVM_CREATE_GUEST_IP" "${guest_ip}"
  symphony_microvm_output_kv "MICROVM_CREATE_TAP" "${tap_name}"
  symphony_microvm_output_kv "MICROVM_CREATE_VSOCK" "${vsock_path}"
  cleanup_on_error=0
  trap - ERR
  exit 0
fi

if [[ ! -f "${firecracker_bin}" || ! -f "${kernel_path}" || ! -f "${base_rootfs}" || ! -f "${ssh_key_path}" ]]; then
  if [[ "${auto_prepare}" -eq 1 ]]; then
    bash "${SCRIPT_DIR}/symphony_microvm_prepare_assets.sh" --assets-root "${assets_root}"
  else
    echo "MicroVM assets missing under ${assets_root}" >&2
    exit 1
  fi
fi

if [[ -f "${base_rootfs}" ]]; then
  base_rootfs_size_mb="$(( $(stat -c '%s' "${base_rootfs}") / 1024 / 1024 ))"
  if (( base_rootfs_size_mb < required_rootfs_size_mb )); then
    if [[ "${auto_prepare}" -eq 1 ]]; then
      bash "${SCRIPT_DIR}/symphony_microvm_prepare_assets.sh" --assets-root "${assets_root}" --force
    else
      echo "MicroVM rootfs image is smaller than required size (${base_rootfs_size_mb}MB < ${required_rootfs_size_mb}MB)" >&2
      exit 1
    fi
  fi
fi

symphony_microvm_require_sudo_noninteractive

launch_mode="direct"
if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
  launch_mode="sudo"
fi

if [[ -f "${pid_path}" ]] && kill -0 "$(cat "${pid_path}")" >/dev/null 2>&1; then
  symphony_microvm_output_kv "MICROVM_CREATE_STATUS" "already_running"
  symphony_microvm_output_kv "MICROVM_CREATE_WORKER_ID" "${worker_id}"
  symphony_microvm_output_kv "MICROVM_CREATE_GUEST_IP" "${guest_ip}"
  symphony_microvm_output_kv "MICROVM_CREATE_TAP" "${tap_name}"
  exit 0
elif [[ -f "${pid_path}" ]] && command -v sudo >/dev/null 2>&1 && sudo -n kill -0 "$(cat "${pid_path}")" >/dev/null 2>&1; then
  symphony_microvm_output_kv "MICROVM_CREATE_STATUS" "already_running"
  symphony_microvm_output_kv "MICROVM_CREATE_WORKER_ID" "${worker_id}"
  symphony_microvm_output_kv "MICROVM_CREATE_GUEST_IP" "${guest_ip}"
  symphony_microvm_output_kv "MICROVM_CREATE_TAP" "${tap_name}"
  exit 0
fi

cp --reflink=auto -f "${base_rootfs}" "${guest_rootfs}" 2>/dev/null || cp -f "${base_rootfs}" "${guest_rootfs}"

sudo -n ip tuntap add "${tap_name}" mode tap 2>/dev/null || true
sudo -n ip addr replace "${host_ip}/30" dev "${tap_name}"
sudo -n ip link set "${tap_name}" up
sudo -n iptables -t nat -C POSTROUTING -s "${guest_ip}/32" -o "${host_iface}" -m comment --comment "symphony:${worker_id}:nat" -j MASQUERADE >/dev/null 2>&1 \
  || sudo -n iptables -t nat -I POSTROUTING 1 -s "${guest_ip}/32" -o "${host_iface}" -m comment --comment "symphony:${worker_id}:nat" -j MASQUERADE
sudo -n iptables -C FORWARD -i "${tap_name}" -o "${host_iface}" -m comment --comment "symphony:${worker_id}:fwd" -j ACCEPT >/dev/null 2>&1 \
  || sudo -n iptables -I FORWARD 1 -i "${tap_name}" -o "${host_iface}" -m comment --comment "symphony:${worker_id}:fwd" -j ACCEPT
sudo -n iptables -C FORWARD -i "${host_iface}" -o "${tap_name}" -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment "symphony:${worker_id}:return" -j ACCEPT >/dev/null 2>&1 \
  || sudo -n iptables -I FORWARD 1 -i "${host_iface}" -o "${tap_name}" -m conntrack --ctstate RELATED,ESTABLISHED -m comment --comment "symphony:${worker_id}:return" -j ACCEPT

python3 - "${config_path}" "${kernel_path}" "${guest_rootfs}" "${tap_name}" "${mac_address}" "${vsock_path}" "${guest_cid}" "${vcpu_count}" "${mem_size_mib}" "${log_path}" <<'PY'
import json
import os
import sys

config_path, kernel_path, rootfs_path, tap_name, mac_address, vsock_path, guest_cid, vcpu_count, mem_size_mib, log_path = sys.argv[1:]
payload = {
    "boot-source": {
        "kernel_image_path": kernel_path,
        "boot_args": "console=ttyS0 reboot=k panic=1",
        "initrd_path": None,
    },
    "drives": [
        {
            "drive_id": "rootfs",
            "partuuid": None,
            "is_root_device": True,
            "cache_type": "Unsafe",
            "is_read_only": False,
            "path_on_host": rootfs_path,
            "io_engine": "Sync",
            "rate_limiter": None,
            "socket": None,
        }
    ],
    "machine-config": {
        "vcpu_count": int(vcpu_count),
        "mem_size_mib": int(mem_size_mib),
        "smt": False,
        "track_dirty_pages": False,
        "huge_pages": "None",
    },
    "network-interfaces": [
        {
            "iface_id": "net1",
            "guest_mac": mac_address,
            "host_dev_name": tap_name,
        }
    ],
    "vsock": {
        "guest_cid": int(guest_cid),
        "uds_path": vsock_path,
    },
    "logger": {
        "log_path": log_path,
        "level": "Info",
        "show_level": True,
        "show_log_origin": True,
    },
}
with open(config_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
PY

rm -f "${api_socket}" "${vsock_path}"
if [[ "${launch_mode}" == "sudo" ]]; then
  firecracker_cmd="$(printf '%q ' "${firecracker_bin}" --api-sock "${api_socket}" --config-file "${config_path}")"
  stdout_log_quoted="$(printf '%q' "${worker_dir}/firecracker.stdout.log")"
  pid_path_quoted="$(printf '%q' "${pid_path}")"
  sudo -n bash -lc "nohup ${firecracker_cmd}>${stdout_log_quoted} 2>&1 & printf '%s\n' \$! > ${pid_path_quoted}"
  firecracker_pid="$(cat "${pid_path}")"
else
  nohup "${firecracker_bin}" --api-sock "${api_socket}" --config-file "${config_path}" >"${worker_dir}/firecracker.stdout.log" 2>&1 &
  firecracker_pid=$!
  printf '%s\n' "${firecracker_pid}" > "${pid_path}"
fi

sleep 2

if [[ "${launch_mode}" == "sudo" ]]; then
  firecracker_running=0
  if sudo -n kill -0 "${firecracker_pid}" >/dev/null 2>&1; then
    firecracker_running=1
  fi
else
  firecracker_running=0
  if kill -0 "${firecracker_pid}" >/dev/null 2>&1; then
    firecracker_running=1
  fi
fi

if [[ "${firecracker_running}" -ne 1 ]]; then
  echo "Firecracker failed to stay running; check ${worker_dir}/firecracker.stdout.log" >&2
  exit 1
fi

ssh_opts="$(symphony_microvm_ssh_opts "${ssh_key_path}")"
for _attempt in $(seq 1 30); do
  if ssh ${ssh_opts} "root@${guest_ip}" "true" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! ssh ${ssh_opts} "root@${guest_ip}" "true" >/dev/null 2>&1; then
  echo "Guest SSH did not become ready for ${guest_ip}" >&2
  exit 1
fi

ssh ${ssh_opts} "root@${guest_ip}" "ip route replace default via ${host_ip} dev eth0 && printf 'nameserver 8.8.8.8\n' > /etc/resolv.conf" >/dev/null
ssh ${ssh_opts} "root@${guest_ip}" "mkdir -p /root/symphony /root/workspace"
scp ${ssh_opts} "${SCRIPT_DIR}/symphony_microvm_guest_bootstrap.sh" "root@${guest_ip}:${guest_bootstrap_script}" >/dev/null
ssh ${ssh_opts} "root@${guest_ip}" "chmod +x ${guest_bootstrap_script} && ${guest_bootstrap_script}" >/dev/null

if [[ -x "${allocate_ports_script}" ]]; then
  # shellcheck disable=SC1090,SC2046
  eval "$("${allocate_ports_script}" "${worker_id}")"
  mkdir -p "${review_proxy_dir}"

  start_review_proxy() {
    local name="$1"
    local port="$2"
    local pid_file="${review_proxy_dir}/${name}.pid"
    local log_file="${review_proxy_dir}/${name}.log"
    local bind_host="${internal_review_bind_host}"

    case "${name}" in
      FRONTEND_HOST_PORT|BACKEND_HOST_PORT)
        bind_host="${public_review_bind_host}"
        ;;
    esac

    if [[ -f "${pid_file}" ]] && kill -0 "$(cat "${pid_file}")" >/dev/null 2>&1; then
      return 0
    fi

    local proxy_pid
    proxy_pid="$(python3 - "${log_file}" "${port}" "${guest_ip}" "${bind_host}" <<'PY'
import os
import subprocess
import sys

log_path, port, guest_ip, bind_host = sys.argv[1:5]

with open(log_path, "ab", buffering=0) as log_file:
    proc = subprocess.Popen(
        [
            "socat",
            f"TCP-LISTEN:{port},bind={bind_host},reuseaddr,fork",
            f"TCP:{guest_ip}:{port}",
        ],
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
        close_fds=True,
    )
    print(proc.pid)
PY
)"
    printf '%s\n' "${proxy_pid}" > "${pid_file}"
    sleep 0.1
    if ! kill -0 "${proxy_pid}" >/dev/null 2>&1; then
      echo "Review port proxy failed to start for ${name} on port ${port}; see ${log_file}" >&2
      exit 1
    fi
  }

  while IFS='=' read -r env_name env_value; do
    case "${env_name}" in
      LANGFUSE_HOST_PORT|FRONTEND_HOST_PORT|LOKI_HOST_PORT|POSTGRES_HOST_PORT|REDIS_HOST_PORT|BACKEND_HOST_PORT|WEAVIATE_HTTP_HOST_PORT|CLICKHOUSE_HTTP_HOST_PORT|CLICKHOUSE_NATIVE_HOST_PORT|MINIO_API_HOST_PORT|MINIO_CONSOLE_HOST_PORT|WEAVIATE_GRPC_HOST_PORT|CURATION_DB_TUNNEL_LOCAL_PORT|CURATION_DB_TUNNEL_DOCKER_PORT)
        start_review_proxy "${env_name}" "${env_value}"
        ;;
    esac
  done < <(env | grep -E '^(LANGFUSE_HOST_PORT|FRONTEND_HOST_PORT|LOKI_HOST_PORT|POSTGRES_HOST_PORT|REDIS_HOST_PORT|BACKEND_HOST_PORT|WEAVIATE_HTTP_HOST_PORT|CLICKHOUSE_HTTP_HOST_PORT|CLICKHOUSE_NATIVE_HOST_PORT|MINIO_API_HOST_PORT|MINIO_CONSOLE_HOST_PORT|WEAVIATE_GRPC_HOST_PORT|CURATION_DB_TUNNEL_LOCAL_PORT|CURATION_DB_TUNNEL_DOCKER_PORT)=' | sort)

  GUEST_IP="${guest_ip}" python3 - "${review_ports_json_path}" <<'PY'
import json
import os
import sys

payload = {
    "review_host": os.environ.get("REVIEW_HOST"),
    "guest_ip": os.environ.get("GUEST_IP"),
    "urls": {
        "frontend": os.environ.get("REVIEW_FRONTEND_URL"),
        "backend": os.environ.get("REVIEW_BACKEND_URL"),
    },
    "ports": {
        "langfuse": os.environ.get("LANGFUSE_HOST_PORT"),
        "frontend": os.environ.get("FRONTEND_HOST_PORT"),
        "loki": os.environ.get("LOKI_HOST_PORT"),
        "postgres": os.environ.get("POSTGRES_HOST_PORT"),
        "redis": os.environ.get("REDIS_HOST_PORT"),
        "backend": os.environ.get("BACKEND_HOST_PORT"),
        "weaviate_http": os.environ.get("WEAVIATE_HTTP_HOST_PORT"),
        "clickhouse_http": os.environ.get("CLICKHOUSE_HTTP_HOST_PORT"),
        "clickhouse_native": os.environ.get("CLICKHOUSE_NATIVE_HOST_PORT"),
        "minio_api": os.environ.get("MINIO_API_HOST_PORT"),
        "minio_console": os.environ.get("MINIO_CONSOLE_HOST_PORT"),
        "weaviate_grpc": os.environ.get("WEAVIATE_GRPC_HOST_PORT"),
        "db_tunnel_local": os.environ.get("CURATION_DB_TUNNEL_LOCAL_PORT"),
        "db_tunnel_docker": os.environ.get("CURATION_DB_TUNNEL_DOCKER_PORT"),
    },
}

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
PY
fi

symphony_microvm_output_kv "MICROVM_CREATE_STATUS" "created"
symphony_microvm_output_kv "MICROVM_CREATE_WORKER_ID" "${worker_id}"
symphony_microvm_output_kv "MICROVM_CREATE_WORKER_DIR" "${worker_dir}"
symphony_microvm_output_kv "MICROVM_CREATE_FIRECRACKER_PID" "${firecracker_pid}"
symphony_microvm_output_kv "MICROVM_CREATE_LAUNCH_MODE" "${launch_mode}"
symphony_microvm_output_kv "MICROVM_CREATE_GUEST_IP" "${guest_ip}"
symphony_microvm_output_kv "MICROVM_CREATE_TAP" "${tap_name}"
symphony_microvm_output_kv "MICROVM_CREATE_SSH_KEY" "${ssh_key_path}"
symphony_microvm_output_kv "MICROVM_CREATE_VSOCK" "${vsock_path}"
if [[ -f "${review_ports_json_path}" ]]; then
  symphony_microvm_output_kv "MICROVM_CREATE_REVIEW_PORTS" "${review_ports_json_path}"
  symphony_microvm_output_kv "MICROVM_CREATE_REVIEW_FRONTEND_URL" "${REVIEW_FRONTEND_URL:-}"
  symphony_microvm_output_kv "MICROVM_CREATE_REVIEW_BACKEND_URL" "${REVIEW_BACKEND_URL:-}"
fi
cleanup_on_error=0
trap - ERR
