#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_guard_workspace_repo.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

make_repo() {
  local repo_dir="$1"
  mkdir -p "${repo_dir}"
  git -C "${repo_dir}" init -b main >/dev/null
  git -C "${repo_dir}" config user.name "Test User"
  git -C "${repo_dir}" config user.email "test@example.com"
  echo "hello" > "${repo_dir}/README.md"
  git -C "${repo_dir}" add README.md
  git -C "${repo_dir}" commit -m "initial" >/dev/null
}

test_guard_fails_closed_when_required_runtime_files_are_missing() {
  local temp_root source_repo workspace_root workspace source_root output status
  temp_root="$(mktemp -d)"
  source_repo="${temp_root}/source-repo"
  workspace_root="${HOME}/.symphony/workspaces/test-guard-runtime-$$"
  workspace="${workspace_root}/MT-1000"
  source_root="${temp_root}/source-root"

  mkdir -p "${workspace_root}" "${source_root}/scripts/utilities"
  make_repo "${source_repo}"
  git clone --depth 1 --branch main "${source_repo}" "${workspace}" >/dev/null 2>&1

  cat > "${source_root}/scripts/utilities/symphony_ensure_workspace_runtime.sh" <<'EOF'
#!/usr/bin/env bash
echo "SYNC_ENV_STATUS=missing_required"
echo "SYNC_ENV_MISSING_REQUIRED=.symphony/WORKFLOW.md"
exit 3
EOF
  chmod +x "${source_root}/scripts/utilities/symphony_ensure_workspace_runtime.sh"

  set +e
  output="$(
    bash "${SCRIPT_PATH}" \
      --workspace-dir "${workspace}" \
      --expected-repo "${source_repo}" \
      --expected-ref main \
      --source-root "${source_root}" 2>&1
  )"
  status=$?
  set -e

  [[ "${status}" -ne 0 ]] || {
    echo "Expected guard script to fail when required runtime files are missing" >&2
    exit 1
  }
  assert_contains "SYNC_ENV_STATUS=missing_required" "${output}"
  assert_contains "SYNC_ENV_MISSING_REQUIRED=.symphony/WORKFLOW.md" "${output}"
  assert_contains "GUARD_REPO_STATUS=error" "${output}"
  assert_contains "GUARD_REPO_REASON=runtime_missing_required" "${output}"

  rm -rf "${workspace_root}" "${temp_root}"
}

test_guard_prefers_workspace_runtime_sync_helper() {
  local temp_root source_repo workspace_root workspace source_root output
  temp_root="$(mktemp -d)"
  source_repo="${temp_root}/source-repo"
  workspace_root="${HOME}/.symphony/workspaces/test-guard-prefer-workspace-$$"
  workspace="${workspace_root}/MT-1001"
  source_root="${temp_root}/source-root"

  mkdir -p "${workspace_root}" "${source_root}/scripts/utilities"
  make_repo "${source_repo}"
  git clone --depth 1 --branch main "${source_repo}" "${workspace}" >/dev/null 2>&1
  mkdir -p "${workspace}/scripts/utilities"

  cat > "${source_root}/scripts/utilities/symphony_ensure_workspace_runtime.sh" <<'EOF'
#!/usr/bin/env bash
echo "SYNC_ENV_STATUS=missing_required"
echo "SYNC_ENV_MISSING_REQUIRED=.symphony/WORKFLOW.md"
exit 3
EOF
  chmod +x "${source_root}/scripts/utilities/symphony_ensure_workspace_runtime.sh"

  cat > "${workspace}/scripts/utilities/symphony_ensure_workspace_runtime.sh" <<'EOF'
#!/usr/bin/env bash
echo "SYNC_ENV_STATUS=ready"
exit 0
EOF
  chmod +x "${workspace}/scripts/utilities/symphony_ensure_workspace_runtime.sh"

  output="$(
    bash "${SCRIPT_PATH}" \
      --workspace-dir "${workspace}" \
      --expected-repo "${source_repo}" \
      --expected-ref main \
      --source-root "${source_root}" 2>&1
  )"

  assert_contains "SYNC_ENV_STATUS=ready" "${output}"
  assert_contains "GUARD_REPO_STATUS=ok" "${output}"
  assert_contains "GUARD_REPO_REASON=match" "${output}"

  rm -rf "${workspace_root}" "${temp_root}"
}

test_guard_fails_closed_when_required_runtime_files_are_missing
test_guard_prefers_workspace_runtime_sync_helper

echo "symphony_guard_workspace_repo tests passed"
