#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CLOUD_INIT_GENERATOR="${SCRIPT_DIR}/symphony_print_incus_vm_cloud_init.sh"

VM_NAME="symphony-main"
INCUS_PROJECT="${SYMPHONY_INCUS_PROJECT:-default}"
VM_IMAGE="images:ubuntu/24.04/cloud"
VM_CPU="8"
VM_MEMORY="12GiB"
VM_DISK="120GiB"
VM_USER="ctabone"
VM_GECOS="Christopher Tabone"
SSH_KEY_FILE=""
REPLACE=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Usage:
  symphony_rebuild_incus_vm.sh [options]

Options:
  --project NAME       Incus project for the VM (default: default)
  --name NAME          Incus instance name (default: symphony-main)
  --image IMAGE        Incus image alias (default: images:ubuntu/24.04/cloud)
  --cpu COUNT          VM vCPU count (default: 8)
  --memory SIZE        VM memory, Incus format (default: 12GiB)
  --disk SIZE          Root disk size, Incus format (default: 120GiB)
  --user USER          Login user to create in cloud-init (default: ctabone)
  --gecos TEXT         GECOS/full-name field for the VM user
  --ssh-key-file PATH  SSH public key to authorize for the VM user
  --replace            Stop and delete an existing VM with the same name first
  --dry-run            Print the actions without changing Incus
  -h, --help           Show this help

This helper rebuilds only the Incus VM shell. It does not restore secrets,
clone the repo, or restart Symphony inside the guest.
EOF
}

instance_exists() {
  "${incus_cmd[@]}" info "${VM_NAME}" >/dev/null 2>&1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      INCUS_PROJECT="${2:?--project requires a value}"
      shift 2
      ;;
    --name)
      VM_NAME="${2:?--name requires a value}"
      shift 2
      ;;
    --image)
      VM_IMAGE="${2:?--image requires a value}"
      shift 2
      ;;
    --cpu)
      VM_CPU="${2:?--cpu requires a value}"
      shift 2
      ;;
    --memory)
      VM_MEMORY="${2:?--memory requires a value}"
      shift 2
      ;;
    --disk)
      VM_DISK="${2:?--disk requires a value}"
      shift 2
      ;;
    --user)
      VM_USER="${2:?--user requires a value}"
      shift 2
      ;;
    --gecos)
      VM_GECOS="${2:?--gecos requires a value}"
      shift 2
      ;;
    --ssh-key-file)
      SSH_KEY_FILE="${2:?--ssh-key-file requires a value}"
      shift 2
      ;;
    --replace)
      REPLACE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${INCUS_PROJECT}" ]]; then
  echo "Incus project cannot be empty; pass --project or set SYMPHONY_INCUS_PROJECT." >&2
  exit 2
fi

incus_cmd=(incus --project "${INCUS_PROJECT}")

if [[ ! -x "${CLOUD_INIT_GENERATOR}" ]]; then
  echo "Missing cloud-init generator: ${CLOUD_INIT_GENERATOR}" >&2
  exit 1
fi

cloud_init_file="$(mktemp)"
trap 'rm -f "${cloud_init_file}"' EXIT

generator_cmd=(
  bash
  "${CLOUD_INIT_GENERATOR}"
  --user "${VM_USER}"
  --gecos "${VM_GECOS}"
)

if [[ -n "${SSH_KEY_FILE}" ]]; then
  generator_cmd+=(--ssh-key-file "${SSH_KEY_FILE}")
fi

"${generator_cmd[@]}" > "${cloud_init_file}"

instance_already_exists=0
if instance_exists; then
  instance_already_exists=1
fi

if [[ "${DRY_RUN}" == "1" ]]; then
  printf 'Would rebuild Incus VM %s in project %s from %s\n' "${VM_NAME}" "${INCUS_PROJECT}" "${VM_IMAGE}"
  printf '  cpu=%s memory=%s disk=%s user=%s\n' "${VM_CPU}" "${VM_MEMORY}" "${VM_DISK}" "${VM_USER}"
  if [[ "${instance_already_exists}" == "1" ]]; then
    printf '  existing instance detected: %s\n' "${VM_NAME}"
  fi
  if [[ "${REPLACE}" == "1" ]] && [[ "${instance_already_exists}" == "1" ]]; then
    printf '  would stop/delete existing instance: %s\n' "${VM_NAME}"
  fi
  printf '  cloud-init source: %s\n' "${CLOUD_INIT_GENERATOR}"
  exit 0
fi

if [[ "${instance_already_exists}" == "1" ]] && [[ "${REPLACE}" != "1" ]]; then
  echo "Incus instance already exists in project ${INCUS_PROJECT}: ${VM_NAME}" >&2
  echo "Re-run with --replace to rebuild it." >&2
  exit 1
fi

if instance_exists; then
  "${incus_cmd[@]}" stop "${VM_NAME}" --force >/dev/null 2>&1 || true
  "${incus_cmd[@]}" delete "${VM_NAME}"
fi

"${incus_cmd[@]}" init "${VM_IMAGE}" "${VM_NAME}" --vm \
  -c "limits.cpu=${VM_CPU}" \
  -c "limits.memory=${VM_MEMORY}" \
  -d "root,size=${VM_DISK}"

"${incus_cmd[@]}" config set "${VM_NAME}" boot.autostart=true
"${incus_cmd[@]}" config set "${VM_NAME}" cloud-init.user-data="$(cat "${cloud_init_file}")"
"${incus_cmd[@]}" start "${VM_NAME}"

cat <<EOF
Rebuilt Incus VM ${VM_NAME} in project ${INCUS_PROJECT}.

Fresh-VM follow-up still required:
  1. Restore repo checkout and local .symphony runtime support.
  2. Run ./.symphony/run.sh --setup-only inside the VM checkout.
  3. Restore repo/user secrets and restart Symphony.
EOF
