#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_ensure_workspace_runtime.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

assert_not_contains() {
  local unexpected="$1"
  local actual="$2"
  if [[ "${actual}" == *"${unexpected}"* ]]; then
    echo "Expected output not to contain '${unexpected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

make_source_root() {
  local source_root="$1"
  git init -q "${source_root}" >/dev/null 2>&1
  mkdir -p \
    "${source_root}/.symphony" \
    "${source_root}/scripts/lib" \
    "${source_root}/scripts/requirements" \
    "${source_root}/scripts/utilities"

  cat > "${source_root}/.git/hooks/pre-commit" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  cat > "${source_root}/.git/hooks/pre-push" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "${source_root}/.git/hooks/pre-commit" "${source_root}/.git/hooks/pre-push"

  cat > "${source_root}/.symphony/WORKFLOW.md" <<'EOF'
workflow-v1
EOF
  cat > "${source_root}/.symphony/github_pat_env.sh" <<'EOF'
github-pat-env-v1
EOF
  cat > "${source_root}/.symphony/with_github_pat.sh" <<'EOF'
#!/usr/bin/env bash
echo with-github-pat-v1
EOF
  cat > "${source_root}/.symphony/configure_github_pat_git.sh" <<'EOF'
#!/usr/bin/env bash
echo configure-github-pat-git-v1
EOF
  chmod +x \
    "${source_root}/.symphony/with_github_pat.sh" \
    "${source_root}/.symphony/configure_github_pat_git.sh"

  cat > "${source_root}/scripts/lib/local_db_tunnel_common.sh" <<'EOF'
common-v1
EOF
  cat > "${source_root}/scripts/lib/symphony_linear_common.sh" <<'EOF'
symphony-linear-common-v1
EOF
  cat > "${source_root}/scripts/requirements/python-tools.txt" <<'EOF'
ruff
pyyaml
EOF

  for helper in \
    ensure_python_tools_venv.sh \
    symphony_pre_merge_cleanup.sh \
    symphony_prepare_docker_config.sh \
    symphony_guard_workspace_repo.sh \
    symphony_guard_no_code_changes.sh \
    symphony_human_review_prep.sh \
    symphony_main_sandbox.sh \
    symphony_ready_for_pr.sh \
    symphony_claude_review_loop.sh \
    symphony_in_review.sh \
    symphony_in_progress.sh \
    symphony_issue_branch.sh \
    symphony_finalize_issue.sh \
    symphony_request_claude_rereview.sh \
    symphony_wait_for_claude_review.sh \
    symphony_claude_review_rounds.sh \
    symphony_linear_issue_context.sh \
    symphony_linear_workpad.sh \
    symphony_linear_issue_state.sh \
    symphony_local_db_tunnel_start.sh \
    symphony_local_db_tunnel_status.sh \
    symphony_local_db_tunnel_stop.sh \
    symphony_microvm_worker_run.sh
  do
    cat > "${source_root}/scripts/utilities/${helper}" <<EOF
#!/usr/bin/env bash
echo ${helper}
EOF
    chmod +x "${source_root}/scripts/utilities/${helper}"
  done

  cat > "${source_root}/docker-compose.yml" <<'EOF'
version: "3"
services:
  backend:
    volumes:
      - ./packages:/runtime/packages:ro
      - ./config:/runtime/config:ro
EOF
}

seed_workspace_repo() {
  local source_root="$1"
  local workspace="$2"

  mkdir -p "${workspace}/scripts"
  cp -R "${source_root}/scripts/." "${workspace}/scripts/"
  cp "${source_root}/docker-compose.yml" "${workspace}/docker-compose.yml"
}

test_default_mode_preserves_existing_overlay_and_tracked_files() {
  local temp_root workspace source_root output
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/workspace"
  source_root="${temp_root}/source"
  mkdir -p "${workspace}"
  make_source_root "${source_root}"
  seed_workspace_repo "${source_root}" "${workspace}"

  mkdir -p "${workspace}/.symphony"
  cat > "${workspace}/docker-compose.yml" <<'EOF'
stale-compose
EOF
  cat > "${workspace}/.symphony/WORKFLOW.md" <<'EOF'
stale-workflow
EOF

  output="$(
    SYMPHONY_LOCAL_SOURCE_ROOT="${source_root}" \
    SYMPHONY_HOOKS_SOURCE="${source_root}/.git/hooks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace}"
  )"

  assert_contains "SYNC_ENV_STATUS=ready" "${output}"
  assert_contains "SYNC_ENV_COPIED=5" "${output}"
  assert_contains "SYNC_ENV_REFRESHED=0" "${output}"
  assert_contains "SYNC_ENV_SKIPPED_EXISTING=1" "${output}"
  [[ "$(cat "${workspace}/docker-compose.yml")" == "stale-compose" ]] || {
    echo "Expected default mode to preserve tracked docker-compose.yml" >&2
    exit 1
  }
  [[ "$(cat "${workspace}/.symphony/WORKFLOW.md")" == "stale-workflow" ]] || {
    echo "Expected default mode to preserve an existing runtime overlay file" >&2
    exit 1
  }
  [[ "$(bash "${workspace}/.symphony/with_github_pat.sh")" == "with-github-pat-v1" ]] || {
    echo "Expected default mode to seed .symphony/with_github_pat.sh" >&2
    exit 1
  }
  [[ -x "${workspace}/.git/hooks/pre-commit" ]] || {
    echo "Expected default mode to seed the pre-commit hook" >&2
    exit 1
  }
  [[ -x "${workspace}/scripts/utilities/ensure_python_tools_venv.sh" ]] || {
    echo "Expected default mode to preserve Git-owned helpers from the workspace checkout" >&2
    exit 1
  }
  [[ "$(cat "${workspace}/scripts/requirements/python-tools.txt")" == $'ruff\npyyaml' ]] || {
    echo "Expected default mode to preserve the Python tools requirements file" >&2
    exit 1
  }
}

test_refresh_managed_only_overwrites_overlay_files() {
  local temp_root workspace source_root output
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/workspace"
  source_root="${temp_root}/source"
  mkdir -p "${workspace}/scripts/utilities" "${workspace}/.symphony"
  make_source_root "${source_root}"
  seed_workspace_repo "${source_root}" "${workspace}"

  cat > "${workspace}/docker-compose.yml" <<'EOF'
stale-compose
EOF
  cat > "${workspace}/scripts/utilities/symphony_wait_for_claude_review.sh" <<'EOF'
#!/usr/bin/env bash
echo stale-helper
EOF
  chmod +x "${workspace}/scripts/utilities/symphony_wait_for_claude_review.sh"
  cat > "${workspace}/.symphony/WORKFLOW.md" <<'EOF'
stale-workflow
EOF

  output="$(
    SYMPHONY_LOCAL_SOURCE_ROOT="${source_root}" \
    SYMPHONY_HOOKS_SOURCE="${source_root}/.git/hooks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" --refresh-managed
  )"

  assert_contains "SYNC_ENV_STATUS=ready" "${output}"
  assert_contains "SYNC_ENV_REFRESHED=1" "${output}"
  assert_contains "SYNC_ENV_COPIED=5" "${output}"
  [[ "$(cat "${workspace}/docker-compose.yml")" == "stale-compose" ]] || {
    echo "Expected refresh mode to leave tracked docker-compose.yml alone" >&2
    exit 1
  }
  [[ "$(bash "${workspace}/scripts/utilities/symphony_wait_for_claude_review.sh")" == "stale-helper" ]] || {
    echo "Expected refresh mode to leave tracked helper content alone" >&2
    exit 1
  }
  [[ "$(cat "${workspace}/.symphony/WORKFLOW.md")" == "workflow-v1" ]] || {
    echo "Expected refresh mode to overwrite .symphony/WORKFLOW.md" >&2
    exit 1
  }
  [[ "$(bash "${workspace}/.symphony/with_github_pat.sh")" == "with-github-pat-v1" ]] || {
    echo "Expected refresh mode to seed optional runtime overlay helpers" >&2
    exit 1
  }
}

test_invalid_hooks_source_falls_back_to_local_source_git_dir() {
  local temp_root workspace source_root output
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/workspace"
  source_root="${temp_root}/source"
  mkdir -p "${workspace}"
  make_source_root "${source_root}"
  seed_workspace_repo "${source_root}" "${workspace}"

  output="$(
    SYMPHONY_LOCAL_SOURCE_ROOT="${source_root}" \
    SYMPHONY_HOOKS_SOURCE="${temp_root}/missing-hooks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace}"
  )"

  assert_contains "SYNC_ENV_STATUS=ready" "${output}"
  [[ -x "${workspace}/.git/hooks/pre-commit" ]] || {
    echo "Expected fallback hook sync to seed pre-commit" >&2
    exit 1
  }
  [[ -x "${workspace}/.git/hooks/pre-push" ]] || {
    echo "Expected fallback hook sync to seed pre-push" >&2
    exit 1
  }
}

test_missing_required_git_owned_files_are_reported() {
  local temp_root workspace source_root output status
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/workspace"
  source_root="${temp_root}/source"
  mkdir -p "${workspace}"
  make_source_root "${source_root}"
  seed_workspace_repo "${source_root}" "${workspace}"
  rm -f "${workspace}/scripts/utilities/symphony_human_review_prep.sh" "${workspace}/docker-compose.yml"

  set +e
  output="$(
    SYMPHONY_LOCAL_SOURCE_ROOT="${source_root}" \
    SYMPHONY_HOOKS_SOURCE="${source_root}/.git/hooks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" 2>&1
  )"
  status=$?
  set -e

  [[ "${status}" -eq 3 ]] || {
    echo "Expected missing required Git-owned files to fail closed" >&2
    exit 1
  }
  assert_contains "SYNC_ENV_STATUS=missing_required" "${output}"
  assert_contains "SYNC_ENV_MISSING_REQUIRED=scripts/utilities/symphony_human_review_prep.sh" "${output}"
  assert_contains "SYNC_ENV_MISSING_OPTIONAL=docker-compose.yml" "${output}"
}

test_missing_no_code_guard_helper_is_optional_for_existing_workspaces() {
  local temp_root workspace source_root output
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/workspace"
  source_root="${temp_root}/source"
  mkdir -p "${workspace}"
  make_source_root "${source_root}"
  seed_workspace_repo "${source_root}" "${workspace}"
  rm -f "${workspace}/scripts/utilities/symphony_guard_no_code_changes.sh"

  output="$(
    SYMPHONY_LOCAL_SOURCE_ROOT="${source_root}" \
    SYMPHONY_HOOKS_SOURCE="${source_root}/.git/hooks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace}" 2>&1
  )"

  assert_contains "SYNC_ENV_STATUS=ready" "${output}"
  assert_not_contains "SYNC_ENV_MISSING_REQUIRED=scripts/utilities/symphony_guard_no_code_changes.sh" "${output}"
  assert_contains "SYNC_ENV_MISSING_OPTIONAL=scripts/utilities/symphony_guard_no_code_changes.sh" "${output}"
}

test_default_mode_preserves_existing_overlay_and_tracked_files
test_refresh_managed_only_overwrites_overlay_files
test_invalid_hooks_source_falls_back_to_local_source_git_dir
test_missing_required_git_owned_files_are_reported
test_missing_no_code_guard_helper_is_optional_for_existing_workspaces

echo "symphony_ensure_workspace_runtime tests passed"
