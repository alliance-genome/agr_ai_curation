#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  symphony_sync_codex_auth_to_vm.sh [options]

Options:
  --vm-name NAME     Incus VM name (default: symphony-main)
  --vm-user USER     VM user that owns ~/.codex/auth.json (default: current host user)
  --host-auth PATH   Host auth.json path (default: ~/.codex/auth.json)
  --force            Push even when the VM file hash matches
  --dry-run          Print the planned sync without mutating the VM
  -h, --help         Show this help

This script is intended to run on the host. It compares the host Codex auth
file against the copy inside the Symphony VM and only pushes when needed,
making it safe to call from cron or a user-level systemd timer.
EOF
}

resolve_default_vm_user() {
  if [[ -n "${SYMPHONY_VM_USER:-}" ]]; then
    printf '%s\n' "${SYMPHONY_VM_USER}"
    return 0
  fi

  if [[ -n "${SUDO_USER:-}" ]]; then
    printf '%s\n' "${SUDO_USER}"
    return 0
  fi

  if host_user="$(id -un 2>/dev/null)" && [[ -n "${host_user}" ]]; then
    printf '%s\n' "${host_user}"
    return 0
  fi

  if [[ -n "${USER:-}" ]]; then
    printf '%s\n' "${USER}"
    return 0
  fi

  return 1
}

VM_NAME="${SYMPHONY_VM_NAME:-symphony-main}"
VM_USER="$(resolve_default_vm_user || true)"
HOST_AUTH="${SYMPHONY_HOST_CODEX_AUTH:-${HOME}/.codex/auth.json}"
FORCE=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vm-name)
      VM_NAME="${2:-}"
      shift 2
      ;;
    --vm-user)
      VM_USER="${2:-}"
      shift 2
      ;;
    --host-auth)
      HOST_AUTH="${2:-}"
      shift 2
      ;;
    --force)
      FORCE=1
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

if [[ -z "${VM_USER}" ]]; then
  echo "Could not determine VM user; pass --vm-user or set SYMPHONY_VM_USER." >&2
  exit 2
fi

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 2
  fi
}

require_cmd incus
require_cmd sha256sum

HOST_AUTH="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "${HOST_AUTH}")"

if [[ ! -f "${HOST_AUTH}" ]]; then
  echo "Host Codex auth file not found: ${HOST_AUTH}" >&2
  exit 1
fi

vm_state="$(
  incus list "${VM_NAME}" --format csv -c ns 2>/dev/null \
    | awk -F, 'NR == 1 { print $2 }'
)"

if [[ -z "${vm_state}" ]]; then
  echo "Incus VM not found: ${VM_NAME}" >&2
  exit 1
fi

if [[ "${vm_state}" != "RUNNING" ]]; then
  echo "Incus VM is not running: ${VM_NAME} (${vm_state})" >&2
  exit 1
fi

vm_home="$(
  incus exec "${VM_NAME}" -- getent passwd "${VM_USER}" 2>/dev/null \
    | cut -d: -f6
)"

if [[ -z "${vm_home}" ]]; then
  echo "Could not resolve home directory for ${VM_USER} inside ${VM_NAME}" >&2
  exit 1
fi

vm_uid="$(incus exec "${VM_NAME}" -- id -u "${VM_USER}")"
vm_gid="$(incus exec "${VM_NAME}" -- id -g "${VM_USER}")"
vm_auth_path="${vm_home}/.codex/auth.json"

host_sha="$(sha256sum "${HOST_AUTH}" | awk '{print $1}')"
vm_sha="$(
  incus exec "${VM_NAME}" -- bash -lc \
    "if [[ -f '${vm_auth_path}' ]]; then sha256sum '${vm_auth_path}' | awk '{print \$1}'; fi"
)"

if [[ "${FORCE}" -ne 1 && -n "${vm_sha}" && "${vm_sha}" == "${host_sha}" ]]; then
  echo "Codex auth already in sync for ${VM_NAME}:${vm_auth_path}"
  exit 0
fi

if [[ "${DRY_RUN}" -eq 1 ]]; then
  if [[ -n "${vm_sha}" ]]; then
    echo "Would update ${VM_NAME}:${vm_auth_path}"
  else
    echo "Would create ${VM_NAME}:${vm_auth_path}"
  fi
  exit 0
fi

incus file push \
  --create-dirs \
  --uid "${vm_uid}" \
  --gid "${vm_gid}" \
  --mode 600 \
  "${HOST_AUTH}" \
  "${VM_NAME}${vm_auth_path}"

new_vm_sha="$(
  incus exec "${VM_NAME}" -- bash -lc \
    "sha256sum '${vm_auth_path}' | awk '{print \$1}'"
)"

if [[ "${new_vm_sha}" != "${host_sha}" ]]; then
  echo "Codex auth sync verification failed for ${VM_NAME}:${vm_auth_path}" >&2
  exit 1
fi

echo "Synced Codex auth to ${VM_NAME}:${vm_auth_path}"
