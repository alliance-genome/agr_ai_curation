#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_materialize_linear_auth.sh"

assert_equals() {
  local expected="$1"
  local actual="$2"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "FAIL: Expected '${expected}', got '${actual}'" >&2
    exit 1
  fi
}

test_materializes_from_env() {
  local temp_dir
  temp_dir="$(mktemp -d)"

  HOME="${temp_dir}/home" \
  LINEAR_API_KEY=" env-linear-key " \
  LINEAR_PROJECT_SLUG=" project-slug " \
  bash "${SCRIPT_PATH}" --quiet

  assert_equals "env-linear-key" "$(tr -d '[:space:]' < "${temp_dir}/home/.linear/api_key.txt")"
  assert_equals "project-slug" "$(tr -d '[:space:]' < "${temp_dir}/home/.linear/project_slug.txt")"
  rm -rf "${temp_dir}"
}

test_materializes_key_from_vault_when_env_missing() {
  local temp_dir recipient
  temp_dir="$(mktemp -d)"
  mkdir -p "${temp_dir}/home/.config/symphony"

  age-keygen -o "${temp_dir}/identity.txt" >/dev/null 2>&1
  recipient="$(awk '/^# public key: / {print $4}' "${temp_dir}/identity.txt")"
  printf 'vault-linear-key\n' \
    | age -r "${recipient}" -o "${temp_dir}/home/.config/symphony/linear_api_key.age" >/dev/null 2>&1
  cp "${temp_dir}/identity.txt" "${temp_dir}/home/.config/symphony/vault.key"

  HOME="${temp_dir}/home" \
  LINEAR_API_KEY="" \
  LINEAR_PROJECT_SLUG="" \
  bash "${SCRIPT_PATH}" --quiet

  assert_equals "vault-linear-key" "$(tr -d '[:space:]' < "${temp_dir}/home/.linear/api_key.txt")"
  rm -rf "${temp_dir}"
}

test_materializes_slug_without_key() {
  local temp_dir
  temp_dir="$(mktemp -d)"

  HOME="${temp_dir}/home" \
  LINEAR_API_KEY="" \
  LINEAR_PROJECT_SLUG=" slug-only " \
  bash "${SCRIPT_PATH}" --quiet

  if [[ -f "${temp_dir}/home/.linear/api_key.txt" ]]; then
    echo "FAIL: api_key.txt should not exist when no key is available" >&2
    exit 1
  fi

  assert_equals "slug-only" "$(tr -d '[:space:]' < "${temp_dir}/home/.linear/project_slug.txt")"
  rm -rf "${temp_dir}"
}

test_materializes_from_env
test_materializes_key_from_vault_when_env_missing
test_materializes_slug_without_key

echo "symphony_materialize_linear_auth tests passed"
