#!/usr/bin/env bash
set -euo pipefail

# Load local test/runtime secrets from a user-managed env file outside the repo.
# Supported locations (first found wins):
# 1) $TEST_SECRETS_ENV_FILE          — explicit override
# 2) ~/.config/agr_ai_curation/.env  — XDG convention
# 3) ~/.agr_ai_curation/.env         — simple home-dir convention
#
# This script is intended to be sourced by other scripts in this directory.
# It does not print secret values.

candidate_files=()

if [[ -n "${TEST_SECRETS_ENV_FILE:-}" ]]; then
  candidate_files+=("${TEST_SECRETS_ENV_FILE}")
fi

candidate_files+=(
  "${XDG_CONFIG_HOME:-${HOME}/.config}/agr_ai_curation/.env"
  "${HOME}/.agr_ai_curation/.env"
)

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
langfuse_env_repair_script="${repo_root}/scripts/utilities/ensure_local_langfuse_env.sh"

loaded_env_file=""
for env_file in "${candidate_files[@]}"; do
  if [[ -f "${env_file}" ]]; then
    loaded_env_file="${env_file}"
    break
  fi
done

if [[ -z "${loaded_env_file}" ]]; then
  echo "No home test env file found; continuing with current shell environment."
  if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    return 0
  fi
  exit 0
fi

if [[ -x "${langfuse_env_repair_script}" ]]; then
  bash "${langfuse_env_repair_script}" "${loaded_env_file}" >/dev/null
fi

# Parse only KEY=VALUE (or export KEY=VALUE) lines.
# This avoids executing arbitrary shell content in personal .env files.
loaded_count=0
ignored_count=0
while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
  line="${raw_line%$'\r'}"

  # Skip blanks and comments
  if [[ -z "${line//[[:space:]]/}" || "${line}" =~ ^[[:space:]]*# ]]; then
    continue
  fi

  if [[ "${line}" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
    key="${BASH_REMATCH[2]}"
    value="${BASH_REMATCH[3]}"

    # Trim leading whitespace from value
    value="${value#"${value%%[![:space:]]*}"}"

    # Strip surrounding single/double quotes when present.
    if [[ "${value}" =~ ^\"(.*)\"[[:space:]]*$ ]]; then
      value="${BASH_REMATCH[1]}"
    elif [[ "${value}" =~ ^\'(.*)\'[[:space:]]*$ ]]; then
      value="${BASH_REMATCH[1]}"
    else
      # Remove inline comments for unquoted values.
      value="${value%%[[:space:]]#*}"
      value="${value%"${value##*[![:space:]]}"}"
    fi

    export "${key}=${value}"
    loaded_count=$((loaded_count + 1))
  else
    ignored_count=$((ignored_count + 1))
  fi
done < "${loaded_env_file}"

echo "Loaded ${loaded_count} env vars from ${loaded_env_file} (values redacted)."
if [[ "${ignored_count}" -gt 0 ]]; then
  echo "Ignored ${ignored_count} non-env lines in ${loaded_env_file}."
fi
