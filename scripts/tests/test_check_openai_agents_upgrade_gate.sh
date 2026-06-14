#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/testing/check_openai_agents_upgrade_gate.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

assert_exit_code() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "Expected exit code ${expected}, got ${actual}" >&2
    exit 1
  fi
}

make_repo() {
  local repo_dir="$1"

  git init -b main "${repo_dir}" >/dev/null
  git -C "${repo_dir}" config user.name "Smoke Gate Test"
  git -C "${repo_dir}" config user.email "smoke-gate@example.test"
  mkdir -p "${repo_dir}/backend"
  cat > "${repo_dir}/backend/requirements.txt" <<'EOF'
openai-agents[litellm]==0.17.4  # Upgrade-gated exact pin.
EOF
  cat > "${repo_dir}/backend/requirements.lock.txt" <<'EOF'
openai-agents==0.17.4
openinference-instrumentation-openai-agents==1.6.1
EOF
  git -C "${repo_dir}" add backend/requirements.txt backend/requirements.lock.txt
  git -C "${repo_dir}" commit -m "seed openai agents pin" >/dev/null
  git -C "${repo_dir}" switch -c feature >/dev/null
}

run_gate() {
  local repo_dir="$1"
  local pr_body_file="$2"
  local diff_range="${3:-main...HEAD}"

  set +e
  output="$(
    bash "${SCRIPT_PATH}" \
      --repo-root "${repo_dir}" \
      --diff-range "${diff_range}" \
      --pr-body-file "${pr_body_file}" \
      2>&1
  )"
  status=$?
  set -e
}

test_skips_when_pin_is_unchanged() {
  local temp_dir body output status
  temp_dir="$(mktemp -d)"
  make_repo "${temp_dir}/repo"
  printf 'No smoke evidence needed.\n' > "${temp_dir}/body.md"

  run_gate "${temp_dir}/repo" "${temp_dir}/body.md"

  assert_exit_code "0" "${status}"
  assert_contains "SDK_UPGRADE_GATE_STATUS=skipped" "${output}"
  assert_contains "openai-agents pin unchanged" "${output}"
}

test_skips_when_pin_line_comment_changes_without_version_change() {
  local temp_dir body output status
  temp_dir="$(mktemp -d)"
  make_repo "${temp_dir}/repo"
  sed -i 's/Upgrade-gated exact pin/Upgrade-gated by dev_release_smoke/' \
    "${temp_dir}/repo/backend/requirements.txt"
  git -C "${temp_dir}/repo" add backend/requirements.txt
  git -C "${temp_dir}/repo" commit -m "document pin" >/dev/null
  printf 'No smoke evidence needed.\n' > "${temp_dir}/body.md"

  run_gate "${temp_dir}/repo" "${temp_dir}/body.md"

  assert_exit_code "0" "${status}"
  assert_contains "SDK_UPGRADE_GATE_STATUS=skipped" "${output}"
  assert_contains "version unchanged" "${output}"
}

test_fails_when_pin_version_changes_without_smoke_evidence() {
  local temp_dir body output status
  temp_dir="$(mktemp -d)"
  make_repo "${temp_dir}/repo"
  sed -i 's/0.17.4/0.18.0/g' "${temp_dir}/repo/backend/requirements.txt" \
    "${temp_dir}/repo/backend/requirements.lock.txt"
  git -C "${temp_dir}/repo" add backend/requirements.txt backend/requirements.lock.txt
  git -C "${temp_dir}/repo" commit -m "bump openai agents" >/dev/null
  printf 'No smoke evidence yet.\n' > "${temp_dir}/body.md"

  run_gate "${temp_dir}/repo" "${temp_dir}/body.md"

  assert_exit_code "1" "${status}"
  assert_contains "SDK_UPGRADE_GATE_STATUS=fail" "${output}"
  assert_contains "SDK_UPGRADE_GATE_OLD_VERSION=0.17.4" "${output}"
  assert_contains "SDK_UPGRADE_GATE_NEW_VERSION=0.18.0" "${output}"
  assert_contains "SDK-Smoke-Evidence: dev_release_smoke PASS" "${output}"
}

test_passes_when_pin_version_changes_with_smoke_evidence() {
  local temp_dir body output status
  temp_dir="$(mktemp -d)"
  make_repo "${temp_dir}/repo"
  sed -i 's/0.17.4/0.18.0/g' "${temp_dir}/repo/backend/requirements.txt" \
    "${temp_dir}/repo/backend/requirements.lock.txt"
  git -C "${temp_dir}/repo" add backend/requirements.txt backend/requirements.lock.txt
  git -C "${temp_dir}/repo" commit -m "bump openai agents" >/dev/null
  printf 'SDK-Smoke-Evidence: dev_release_smoke PASS /tmp/evidence.json\n' > "${temp_dir}/body.md"

  run_gate "${temp_dir}/repo" "${temp_dir}/body.md"

  assert_exit_code "0" "${status}"
  assert_contains "SDK_UPGRADE_GATE_STATUS=pass" "${output}"
  assert_contains "dev_release_smoke PASS evidence marker present" "${output}"
}

test_fails_when_diff_range_is_invalid() {
  local temp_dir body output status
  temp_dir="$(mktemp -d)"
  make_repo "${temp_dir}/repo"
  printf 'No smoke evidence needed.\n' > "${temp_dir}/body.md"

  run_gate "${temp_dir}/repo" "${temp_dir}/body.md" "missing-ref...HEAD"

  assert_exit_code "2" "${status}"
  assert_contains "Invalid --diff-range or git diff failed: missing-ref...HEAD" "${output}"
}

test_skips_when_pin_is_unchanged
test_skips_when_pin_line_comment_changes_without_version_change
test_fails_when_pin_version_changes_without_smoke_evidence
test_passes_when_pin_version_changes_with_smoke_evidence
test_fails_when_diff_range_is_invalid

echo "check_openai_agents_upgrade_gate tests passed"
