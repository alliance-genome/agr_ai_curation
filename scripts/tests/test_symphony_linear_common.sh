#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
LIB_PATH="${REPO_ROOT}/scripts/lib/symphony_linear_common.sh"

assert_equals() {
  local expected="$1"
  local actual="$2"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "FAIL: Expected '${expected}', got '${actual}'" >&2
    exit 1
  fi
}

test_reads_linear_api_key_from_env_when_file_missing() {
  local temp_dir output
  temp_dir="$(mktemp -d)"
  mkdir -p "${temp_dir}/home"

  output="$(
    HOME="${temp_dir}/home" \
    LINEAR_API_KEY=" env-linear-key " \
    bash -lc 'source "'"${LIB_PATH}"'"; symphony_linear_read_api_key ""'
  )"

  assert_equals "env-linear-key" "${output}"
  rm -rf "${temp_dir}"
}

test_falls_back_to_file_when_env_missing() {
  local temp_dir output
  temp_dir="$(mktemp -d)"
  mkdir -p "${temp_dir}/home/.linear"
  printf 'file-linear-key\n' > "${temp_dir}/home/.linear/api_key.txt"

  output="$(
    HOME="${temp_dir}/home" \
    LINEAR_API_KEY="" \
    bash -lc 'source "'"${LIB_PATH}"'"; symphony_linear_read_api_key ""'
  )"

  assert_equals "file-linear-key" "${output}"
  rm -rf "${temp_dir}"
}

test_explicit_key_beats_env_and_file() {
  local temp_dir output
  temp_dir="$(mktemp -d)"
  mkdir -p "${temp_dir}/home/.linear"
  printf 'file-linear-key\n' > "${temp_dir}/home/.linear/api_key.txt"

  output="$(
    HOME="${temp_dir}/home" \
    LINEAR_API_KEY="env-linear-key" \
    bash -lc 'source "'"${LIB_PATH}"'"; symphony_linear_read_api_key " explicit-linear-key "'
  )"

  assert_equals "explicit-linear-key" "${output}"
  rm -rf "${temp_dir}"
}

test_reads_linear_api_key_from_env_when_file_missing
test_falls_back_to_file_when_env_missing
test_explicit_key_beats_env_and_file

echo "symphony_linear_common tests passed"
