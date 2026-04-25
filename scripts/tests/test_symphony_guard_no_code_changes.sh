#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_guard_no_code_changes.sh"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/assertions.sh"

make_repo() {
  local repo_dir="$1"

  git init -b main "${repo_dir}" >/dev/null
  git -C "${repo_dir}" config user.name "Symphony Test"
  git -C "${repo_dir}" config user.email "symphony@example.com"
  printf 'seed\n' > "${repo_dir}/README.md"
  mkdir -p "${repo_dir}/scripts"
  printf 'keep\n' > "${repo_dir}/scripts/keep.txt"
  git -C "${repo_dir}" add README.md scripts/keep.txt
  git -C "${repo_dir}" commit -m "seed" >/dev/null
}

test_clean_no_code_state_passes() {
  local temp_dir repo_dir output
  temp_dir="$(mktemp -d)"
  repo_dir="${temp_dir}/repo"
  make_repo "${repo_dir}"

  output="${temp_dir}/output.txt"
  bash "${SCRIPT_PATH}" \
    --workspace-dir "${repo_dir}" \
    --state "Todo" \
    --issue-identifier ALL-100 \
    > "${output}"

  assert_contains "NO_CODE_GUARD_STATUS=ok" "${output}"
  assert_contains "NO_CODE_GUARD_APPLIES=true" "${output}"
  rm -rf "${temp_dir}"
}

test_write_allowed_state_is_skipped() {
  local temp_dir repo_dir output
  temp_dir="$(mktemp -d)"
  repo_dir="${temp_dir}/repo"
  make_repo "${repo_dir}"
  printf 'implementation\n' >> "${repo_dir}/README.md"

  output="${temp_dir}/output.txt"
  bash "${SCRIPT_PATH}" \
    --workspace-dir "${repo_dir}" \
    --state "In Progress" \
    --issue-identifier ALL-101 \
    > "${output}"

  assert_contains "NO_CODE_GUARD_STATUS=skipped_allowed_state" "${output}"
  assert_contains "NO_CODE_GUARD_APPLIES=false" "${output}"
  rm -rf "${temp_dir}"
}

test_dirty_no_code_state_is_blocked_with_artifacts() {
  local temp_dir repo_dir output status artifact_dir
  temp_dir="$(mktemp -d)"
  repo_dir="${temp_dir}/repo"
  make_repo "${repo_dir}"
  printf 'bad write\n' >> "${repo_dir}/README.md"

  output="${temp_dir}/output.txt"
  set +e
  bash "${SCRIPT_PATH}" \
    --workspace-dir "${repo_dir}" \
    --state "In Review" \
    --issue-identifier ALL-102 \
    > "${output}"
  status=$?
  set -e

  [[ "${status}" -eq 20 ]] || {
    echo "Expected exit code 20, got ${status}" >&2
    exit 1
  }
  assert_contains "NO_CODE_GUARD_STATUS=dirty" "${output}"
  assert_contains "NO_CODE_GUARD_MESSAGE=No-code lane left repository changes behind." "${output}"
  artifact_dir="$(sed -n 's/^NO_CODE_GUARD_ARTIFACT_DIR=//p' "${output}" | tail -n 1)"
  [[ -f "${artifact_dir}/status.txt" ]] || {
    echo "Expected violation status artifact" >&2
    exit 1
  }
  assert_contains "M README.md" "${artifact_dir}/status.txt"
  assert_contains "bad write" "${artifact_dir}/diff.patch"
  rm -rf "${temp_dir}"
}

test_runtime_noise_is_ignored() {
  local temp_dir repo_dir output
  temp_dir="$(mktemp -d)"
  repo_dir="${temp_dir}/repo"
  make_repo "${repo_dir}"

  mkdir -p \
    "${repo_dir}/.symphony" \
    "${repo_dir}/.symphony-docker-config" \
    "${repo_dir}/scripts/utilities"
  printf 'workflow\n' > "${repo_dir}/.symphony/WORKFLOW.md"
  printf '{}\n' > "${repo_dir}/.symphony-docker-config/config.json"
  printf 'export TUNNEL=1\n' > "${repo_dir}/scripts/local_db_tunnel_env.sh"
  printf 'runtime\n' > "${repo_dir}/scripts/utilities/symphony_main_sandbox.sh"

  output="${temp_dir}/output.txt"
  bash "${SCRIPT_PATH}" \
    --workspace-dir "${repo_dir}" \
    --state "Human Review Prep" \
    --issue-identifier ALL-103 \
    > "${output}"

  assert_contains "NO_CODE_GUARD_STATUS=ok" "${output}"
  rm -rf "${temp_dir}"
}

test_snapshot_fails_when_starting_dirty() {
  local temp_dir repo_dir output status
  temp_dir="$(mktemp -d)"
  repo_dir="${temp_dir}/repo"
  make_repo "${repo_dir}"
  printf 'dirty\n' >> "${repo_dir}/README.md"

  output="${temp_dir}/output.txt"
  set +e
  bash "${SCRIPT_PATH}" snapshot \
    --workspace-dir "${repo_dir}" \
    --state "Needs Review" \
    --issue-identifier ALL-104 \
    > "${output}"
  status=$?
  set -e

  [[ "${status}" -eq 20 ]] || {
    echo "Expected exit code 20, got ${status}" >&2
    exit 1
  }
  assert_contains "NO_CODE_GUARD_STATUS=dirty" "${output}"
  assert_contains "already has code changes before the no-code lane" "${output}"
  rm -rf "${temp_dir}"
}

test_check_head_detects_clean_commit() {
  local temp_dir repo_dir snapshot output status
  temp_dir="$(mktemp -d)"
  repo_dir="${temp_dir}/repo"
  make_repo "${repo_dir}"
  snapshot="${temp_dir}/snapshot.env"

  bash "${SCRIPT_PATH}" snapshot \
    --workspace-dir "${repo_dir}" \
    --state "Todo" \
    --issue-identifier ALL-105 \
    --snapshot-file "${snapshot}" \
    > "${temp_dir}/snapshot-output.txt"

  printf 'committed in wrong lane\n' >> "${repo_dir}/README.md"
  git -C "${repo_dir}" add README.md
  git -C "${repo_dir}" commit -m "wrong lane change" >/dev/null

  output="${temp_dir}/output.txt"
  set +e
  bash "${SCRIPT_PATH}" verify \
    --workspace-dir "${repo_dir}" \
    --state "Todo" \
    --issue-identifier ALL-105 \
    --snapshot-file "${snapshot}" \
    --check-head \
    > "${output}"
  status=$?
  set -e

  [[ "${status}" -eq 21 ]] || {
    echo "Expected exit code 21, got ${status}" >&2
    exit 1
  }
  assert_contains "NO_CODE_GUARD_STATUS=head_changed" "${output}"
  rm -rf "${temp_dir}"
}

test_clean_no_code_state_passes
test_write_allowed_state_is_skipped
test_dirty_no_code_state_is_blocked_with_artifacts
test_runtime_noise_is_ignored
test_snapshot_fails_when_starting_dirty
test_check_head_detects_clean_commit

echo "symphony_guard_no_code_changes tests passed"
