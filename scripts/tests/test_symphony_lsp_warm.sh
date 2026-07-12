#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_lsp_warm.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "FAIL: Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

assert_exit_code() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != "${expected}" ]]; then
    echo "FAIL: Expected exit code ${expected}, got ${actual}" >&2
    exit 1
  fi
}

test_successful_warm() {
  local temp_dir helper rc output
  temp_dir="$(mktemp -d)"
  helper="${temp_dir}/agent-lsp-stub"

  cat > "${helper}" <<'EOF'
#!/usr/bin/env bash
cat <<JSON
{
  "status": "ready",
  "reason": "fingerprint_changed",
  "refreshed": true,
  "workspace_root": "/tmp/example-workspace",
  "cache_dir": "/tmp/example-cache",
  "languages": ["python", "typescript"],
  "language_status": {
    "python": {"status": "ready", "reason": "language_server_available"},
    "typescript": {"status": "dependencies_missing", "reason": "typescript_not_installed"}
  }
}
JSON
EOF
  chmod +x "${helper}"

  set +e
  output="$(SYMPHONY_AGENT_LSP_HELPER="${helper}" bash "${SCRIPT_PATH}" --root "${temp_dir}" --timeout 1 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "SYMPHONY_LSP_STATUS=ready" "${output}"
  assert_contains "SYMPHONY_LSP_REASON=fingerprint_changed" "${output}"
  assert_contains "SYMPHONY_LSP_REFRESHED=true" "${output}"
  assert_contains "SYMPHONY_LSP_LANGUAGES=python,typescript" "${output}"
  assert_contains "SYMPHONY_LSP_TYPESCRIPT_STATUS=dependencies_missing" "${output}"
  assert_contains "SYMPHONY_LSP_TYPESCRIPT_REASON=typescript_not_installed" "${output}"

  echo "  PASS: test_successful_warm"
  rm -rf "${temp_dir}"
}

test_failed_warm_is_non_blocking() {
  local temp_dir helper rc output
  temp_dir="$(mktemp -d)"
  helper="${temp_dir}/agent-lsp-failing-stub"

  cat > "${helper}" <<'EOF'
#!/usr/bin/env bash
echo "boom"
exit 17
EOF
  chmod +x "${helper}"

  set +e
  output="$(SYMPHONY_AGENT_LSP_HELPER="${helper}" bash "${SCRIPT_PATH}" --root "${temp_dir}" --timeout 1 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "SYMPHONY_LSP_STATUS=error" "${output}"
  assert_contains "SYMPHONY_LSP_REASON=warm_failed" "${output}"
  assert_contains "SYMPHONY_LSP_ERROR=boom" "${output}"

  echo "  PASS: test_failed_warm_is_non_blocking"
  rm -rf "${temp_dir}"
}

echo "Running symphony_lsp_warm tests..."
test_successful_warm
test_failed_warm_is_non_blocking
echo "symphony_lsp_warm tests passed (2/2)"
