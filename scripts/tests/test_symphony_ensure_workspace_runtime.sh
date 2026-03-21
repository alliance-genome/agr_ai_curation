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

make_source_root() {
  local source_root="$1"
  mkdir -p \
    "${source_root}/.git/hooks" \
    "${source_root}/.symphony" \
    "${source_root}/scripts/lib" \
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

  for helper in \
    symphony_pre_merge_cleanup.sh \
    symphony_prepare_docker_config.sh \
    symphony_guard_workspace_repo.sh \
    symphony_human_review_prep.sh \
    symphony_ready_for_pr.sh \
    symphony_claude_review_loop.sh \
    symphony_request_claude_rereview.sh \
    symphony_wait_for_claude_review.sh \
    symphony_claude_review_rounds.sh \
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

test_default_mode_preserves_existing_files() {
  local temp_root workspace source_root output
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/workspace"
  source_root="${temp_root}/source"
  mkdir -p "${workspace}"
  make_source_root "${source_root}"

  cat > "${workspace}/docker-compose.yml" <<'EOF'
stale-compose
EOF

  output="$(
    SYMPHONY_LOCAL_SOURCE_ROOT="${source_root}" \
    SYMPHONY_HOOKS_SOURCE="${source_root}/.git/hooks" \
    bash "${SCRIPT_PATH}" --workspace-dir "${workspace}"
  )"

  assert_contains "SYNC_ENV_COPIED=20" "${output}"
  assert_contains "SYNC_ENV_REFRESHED=0" "${output}"
  assert_contains "SYNC_ENV_SKIPPED_EXISTING=1" "${output}"
  [[ "$(cat "${workspace}/docker-compose.yml")" == "stale-compose" ]] || {
    echo "Expected default mode to preserve existing docker-compose.yml" >&2
    exit 1
  }
  [[ "$(cat "${workspace}/.symphony/WORKFLOW.md")" == "workflow-v1" ]] || {
    echo "Expected default mode to seed .symphony/WORKFLOW.md" >&2
    exit 1
  }
  [[ "$(bash "${workspace}/.symphony/with_github_pat.sh")" == "with-github-pat-v1" ]] || {
    echo "Expected default mode to seed .symphony/with_github_pat.sh" >&2
    exit 1
  }
}

test_refresh_managed_overwrites_existing_files() {
  local temp_root workspace source_root output
  temp_root="$(mktemp -d)"
  workspace="${temp_root}/workspace"
  source_root="${temp_root}/source"
  mkdir -p "${workspace}/scripts/utilities" "${workspace}/.symphony"
  make_source_root "${source_root}"

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
  assert_contains "SYNC_ENV_REFRESHED=3" "${output}"
  assert_contains "SYNC_ENV_COPIED=18" "${output}"
  [[ "$(cat "${workspace}/docker-compose.yml")" == *"/runtime/packages"* ]] || {
    echo "Expected refresh mode to overwrite docker-compose.yml" >&2
    exit 1
  }
  [[ "$(bash "${workspace}/scripts/utilities/symphony_wait_for_claude_review.sh")" == "symphony_wait_for_claude_review.sh" ]] || {
    echo "Expected refresh mode to overwrite managed helper content" >&2
    exit 1
  }
  [[ "$(cat "${workspace}/.symphony/WORKFLOW.md")" == "workflow-v1" ]] || {
    echo "Expected refresh mode to overwrite .symphony/WORKFLOW.md" >&2
    exit 1
  }
}

test_default_mode_preserves_existing_files
test_refresh_managed_overwrites_existing_files

echo "symphony_ensure_workspace_runtime tests passed"
