#!/usr/bin/env bash

set -euo pipefail

DEFAULT_LINEAR_DIR="${HOME}/.linear"
DEFAULT_KEY_FILE="${DEFAULT_LINEAR_DIR}/api_key.txt"
DEFAULT_SLUG_FILE="${DEFAULT_LINEAR_DIR}/project_slug.txt"
DEFAULT_VAULT_DIR="${HOME}/.config/symphony"
DEFAULT_VAULT_KEY="${DEFAULT_VAULT_DIR}/vault.key"
DEFAULT_VAULT_LINEAR_KEY="${DEFAULT_VAULT_DIR}/linear_api_key.age"

usage() {
  cat <<'EOF'
Usage:
  symphony_materialize_linear_auth.sh [options]

Options:
  --linear-api-key VALUE   Linear API key to materialize (default: LINEAR_API_KEY,
                           then Symphony vault, then existing ~/.linear/api_key.txt)
  --project-slug VALUE     Linear project slug to materialize (default:
                           LINEAR_PROJECT_SLUG, then existing ~/.linear/project_slug.txt)
  --key-file PATH          Destination for the Linear API key file
                           (default: ~/.linear/api_key.txt)
  --slug-file PATH         Destination for the project slug file
                           (default: ~/.linear/project_slug.txt)
  --vault-dir PATH         Symphony vault directory containing vault.key and
                           linear_api_key.age (default: ~/.config/symphony)
  --quiet                  Suppress success output
  -h, --help               Show this help

This script is intended for the Symphony VM/user environment. It materializes
the low-risk Linear helper files expected by shell helpers so Codex runs do not
depend on env inheritance alone.
EOF
}

trim() {
  local value="${1-}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

resolve_api_key() {
  local explicit_key="${1-}"
  local key_file="${2-}"
  local vault_dir="${3-}"
  local env_key=""
  local vault_key=""
  local encrypted_key=""
  local decrypted=""

  explicit_key="$(trim "${explicit_key}")"
  if [[ -n "${explicit_key}" ]]; then
    printf '%s' "${explicit_key}"
    return 0
  fi

  env_key="$(trim "${LINEAR_API_KEY:-}")"
  if [[ -n "${env_key}" ]]; then
    printf '%s' "${env_key}"
    return 0
  fi

  vault_key="${vault_dir}/vault.key"
  encrypted_key="${vault_dir}/linear_api_key.age"
  if [[ -f "${vault_key}" ]] && [[ -f "${encrypted_key}" ]] && command -v age >/dev/null 2>&1; then
    if decrypted="$(age -d -i "${vault_key}" "${encrypted_key}" 2>/dev/null)"; then
      decrypted="$(trim "${decrypted}")"
      if [[ -n "${decrypted}" ]]; then
        printf '%s' "${decrypted}"
        return 0
      fi
    fi
  fi

  if [[ -r "${key_file}" ]]; then
    tr -d '[:space:]' < "${key_file}"
    return 0
  fi

  return 1
}

resolve_project_slug() {
  local explicit_slug="${1-}"
  local slug_file="${2-}"
  local env_slug=""

  explicit_slug="$(trim "${explicit_slug}")"
  if [[ -n "${explicit_slug}" ]]; then
    printf '%s' "${explicit_slug}"
    return 0
  fi

  env_slug="$(trim "${LINEAR_PROJECT_SLUG:-}")"
  if [[ -n "${env_slug}" ]]; then
    printf '%s' "${env_slug}"
    return 0
  fi

  if [[ -r "${slug_file}" ]]; then
    tr -d '[:space:]' < "${slug_file}"
    return 0
  fi

  return 1
}

write_secret_file() {
  local path="$1"
  local value="$2"
  local dir=""

  dir="$(dirname "${path}")"
  mkdir -p "${dir}"
  chmod 700 "${dir}"
  printf '%s\n' "${value}" > "${path}"
  chmod 600 "${path}"
}

LINEAR_API_KEY_INPUT=""
PROJECT_SLUG_INPUT=""
KEY_FILE="${DEFAULT_KEY_FILE}"
SLUG_FILE="${DEFAULT_SLUG_FILE}"
VAULT_DIR="${DEFAULT_VAULT_DIR}"
QUIET=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --linear-api-key)
      LINEAR_API_KEY_INPUT="${2-}"
      shift 2
      ;;
    --project-slug)
      PROJECT_SLUG_INPUT="${2-}"
      shift 2
      ;;
    --key-file)
      KEY_FILE="${2-}"
      shift 2
      ;;
    --slug-file)
      SLUG_FILE="${2-}"
      shift 2
      ;;
    --vault-dir)
      VAULT_DIR="${2-}"
      shift 2
      ;;
    --quiet)
      QUIET=1
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

KEY_FILE="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "${KEY_FILE}")"
SLUG_FILE="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "${SLUG_FILE}")"
VAULT_DIR="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "${VAULT_DIR}")"

resolved_key="$(resolve_api_key "${LINEAR_API_KEY_INPUT}" "${KEY_FILE}" "${VAULT_DIR}" || true)"
resolved_slug="$(resolve_project_slug "${PROJECT_SLUG_INPUT}" "${SLUG_FILE}" || true)"

if [[ -z "${resolved_key}" ]] && [[ -z "${resolved_slug}" ]]; then
  echo "No Linear auth or project slug available to materialize." >&2
  exit 1
fi

if [[ -n "${resolved_key}" ]]; then
  write_secret_file "${KEY_FILE}" "${resolved_key}"
fi

if [[ -n "${resolved_slug}" ]]; then
  write_secret_file "${SLUG_FILE}" "${resolved_slug}"
fi

if [[ "${QUIET}" != "1" ]]; then
  if [[ -n "${resolved_key}" ]] && [[ -n "${resolved_slug}" ]]; then
    echo "Materialized Linear API key and project slug under $(dirname "${KEY_FILE}")"
  elif [[ -n "${resolved_key}" ]]; then
    echo "Materialized Linear API key at ${KEY_FILE}"
  else
    echo "Materialized Linear project slug at ${SLUG_FILE}"
  fi
fi
