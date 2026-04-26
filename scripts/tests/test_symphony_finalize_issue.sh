#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_finalize_issue.sh"

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

make_cleanup_stub() {
  local path="$1"
  cat > "${path}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
remove_workspace="false"
for arg in "$@"; do
  if [[ "${arg}" == "--remove-workspace" ]]; then
    remove_workspace="true"
  fi
done
if [[ -n "${CLEANUP_INVOCATION_LOG:-}" ]]; then
  printf 'remove_workspace=%s args=%s\n' "${remove_workspace}" "$*" >> "${CLEANUP_INVOCATION_LOG}"
fi
echo "CLEANUP_STATUS=success"
echo "CLEANUP_ATTEMPTS=1"
echo "CLEANUP_PROJECT=test-project"
echo "CLEANUP_REMOVE_WORKSPACE_REQUESTED=${remove_workspace}"
echo "CLEANUP_WORKSPACE_REMOVED=${remove_workspace}"
echo "CLEANUP_LEFTOVER_CONTAINERS=0"
echo "CLEANUP_LEFTOVER_VOLUMES=0"
echo "CLEANUP_LEFTOVER_NETWORKS=0"
echo "CLEANUP_FIXES=none"
echo "CLEANUP_FIRST_ERROR=none"
exit 0
EOF
  chmod +x "${path}"
}

make_gh_stub() {
  local path="$1"
  cat > "${path}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "pr" && "$2" == "list" ]]; then
  printf '%s\n' '[{"number":88,"title":"ALL-88: Ready","url":"https://example.test/pr/88","headRefName":"all-88-branch"}]'
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "merge" ]]; then
  echo "merged"
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "view" ]]; then
  printf '%s\n' '{"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[],"headRefOid":"abc123","baseRefName":"main"}'
  exit 0
fi
echo "Unexpected gh invocation: $*" >&2
exit 1
EOF
  chmod +x "${path}"
}

make_gh_stub_requires_repo() {
  local path="$1"
  cat > "${path}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

require_repo_arg() {
  local saw_repo="false"
  local previous=""
  for arg in "$@"; do
    if [[ "${previous}" == "--repo" && "${arg}" == "alliance-genome/agr_ai_curation" ]]; then
      saw_repo="true"
      break
    fi
    previous="${arg}"
  done
  if [[ "${saw_repo}" != "true" ]]; then
    echo "Expected --repo alliance-genome/agr_ai_curation in gh invocation: $*" >&2
    exit 1
  fi
}

require_repo_arg "$@"

if [[ "$1" == "pr" && "$2" == "list" ]]; then
  printf '%s\n' '[{"number":88,"title":"ALL-88: Ready","url":"https://example.test/pr/88","headRefName":"all-88-branch"}]'
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "merge" ]]; then
  echo "merged"
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "view" ]]; then
  printf '%s\n' '{"mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","files":[],"headRefOid":"abc123","baseRefName":"main"}'
  exit 0
fi
echo "Unexpected gh invocation: $*" >&2
exit 1
EOF
  chmod +x "${path}"
}

make_gh_stub_merge_fails_conflict() {
  local path="$1"
  cat > "${path}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "pr" && "$2" == "list" ]]; then
  printf '%s\n' '[{"number":99,"title":"ALL-99: Conflicted","url":"https://example.test/pr/99","headRefName":"all-99-branch"}]'
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "merge" ]]; then
  echo "Pull request #99 is not mergeable: the merge commit cannot be cleanly created." >&2
  exit 1
fi
if [[ "$1" == "pr" && "$2" == "view" ]]; then
  printf '%s\n' '{"mergeable":"CONFLICTING","mergeStateStatus":"DIRTY","files":[{"path":"backend/src/api/foo.py"},{"path":"frontend/src/bar.tsx"}],"headRefOid":"def456","baseRefName":"main"}'
  exit 0
fi
echo "Unexpected gh invocation: $*" >&2
exit 1
EOF
  chmod +x "${path}"
}

make_gh_stub_merge_fails_non_conflict() {
  local path="$1"
  cat > "${path}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "pr" && "$2" == "list" ]]; then
  printf '%s\n' '[{"number":77,"title":"ALL-77: Auth fail","url":"https://example.test/pr/77","headRefName":"all-77-branch"}]'
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "merge" ]]; then
  echo "GraphQL: Could not merge because of branch protection rules" >&2
  exit 1
fi
if [[ "$1" == "pr" && "$2" == "view" ]]; then
  printf '%s\n' '{"mergeable":"MERGEABLE","mergeStateStatus":"BLOCKED","files":[],"headRefOid":"ghi789","baseRefName":"main"}'
  exit 0
fi
echo "Unexpected gh invocation: $*" >&2
exit 1
EOF
  chmod +x "${path}"
}

test_no_pr_finalizes_without_merge() {
  local temp_dir cleanup_stub workspace output
  temp_dir="$(mktemp -d)"
  cleanup_stub="${temp_dir}/cleanup.sh"
  workspace="${temp_dir}/ALL-46"
  mkdir -p "${workspace}"
  make_cleanup_stub "${cleanup_stub}"

  output="$(
    bash "${SCRIPT_PATH}" \
      --delivery-mode no_pr \
      --workspace-dir "${workspace}" \
      --issue-identifier ALL-46 \
      --compose-project all-46 \
      --cleanup-script "${cleanup_stub}"
  )"

  assert_contains "FINALIZE_STATUS=finalized_no_pr" "${output}"
  assert_contains "FINALIZE_MERGE_STATUS=skipped_no_pr" "${output}"
  assert_contains "FINALIZE_NEXT_STATE=Done" "${output}"
  assert_contains "FINALIZE_WORKSPACE_REMOVAL=deferred_to_terminal_cleanup" "${output}"
}

test_pr_dry_run_reports_merge() {
  local temp_dir cleanup_stub gh_stub workspace output
  temp_dir="$(mktemp -d)"
  cleanup_stub="${temp_dir}/cleanup.sh"
  gh_stub="${temp_dir}/gh"
  workspace="${temp_dir}/ALL-88"
  mkdir -p "${workspace}"
  make_cleanup_stub "${cleanup_stub}"
  make_gh_stub "${gh_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --workspace-dir "${workspace}" \
      --issue-identifier ALL-88 \
      --compose-project all-88 \
      --repo alliance-genome/agr_ai_curation \
      --branch all-88-branch \
      --cleanup-script "${cleanup_stub}" \
      --dry-run
  )"

  assert_contains "FINALIZE_STATUS=dry_run" "${output}"
  assert_contains "FINALIZE_PR_NUMBER=88" "${output}"
  assert_contains "FINALIZE_MERGE_STATUS=dry_run" "${output}"
}

test_successful_pr_finalization_defers_workspace_removal() {
  local temp_dir cleanup_stub gh_stub workspace cleanup_log output cleanup_invocations
  temp_dir="$(mktemp -d)"
  cleanup_stub="${temp_dir}/cleanup.sh"
  gh_stub="${temp_dir}/gh"
  workspace="${temp_dir}/ALL-88"
  cleanup_log="${temp_dir}/cleanup.log"
  mkdir -p "${workspace}"
  make_cleanup_stub "${cleanup_stub}"
  make_gh_stub "${gh_stub}"

  output="$(
    CLEANUP_INVOCATION_LOG="${cleanup_log}" PATH="${temp_dir}:${PATH}" bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --workspace-dir "${workspace}" \
      --issue-identifier ALL-88 \
      --compose-project all-88 \
      --repo alliance-genome/agr_ai_curation \
      --branch all-88-branch \
      --cleanup-script "${cleanup_stub}"
  )"

  cleanup_invocations="$(cat "${cleanup_log}")"

  assert_contains "FINALIZE_STATUS=merged" "${output}"
  assert_contains "FINALIZE_NEXT_STATE=Done" "${output}"
  assert_contains "FINALIZE_WORKSPACE_REMOVAL=deferred_to_terminal_cleanup" "${output}"
  assert_not_contains "remove_workspace=true" "${cleanup_invocations}"
}

test_pr_dry_run_infers_repo_from_origin() {
  local temp_dir cleanup_stub gh_stub workspace output
  temp_dir="$(mktemp -d)"
  cleanup_stub="${temp_dir}/cleanup.sh"
  gh_stub="${temp_dir}/gh"
  workspace="${temp_dir}/ALL-88"
  mkdir -p "${workspace}"
  make_cleanup_stub "${cleanup_stub}"
  make_gh_stub_requires_repo "${gh_stub}"
  git -C "${workspace}" init -q
  git -C "${workspace}" remote add origin git@github.com:alliance-genome/agr_ai_curation.git

  output="$(
    PATH="${temp_dir}:${PATH}" bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --workspace-dir "${workspace}" \
      --issue-identifier ALL-88 \
      --compose-project all-88 \
      --branch all-88-branch \
      --cleanup-script "${cleanup_stub}" \
      --dry-run
  )"

  assert_contains "FINALIZE_STATUS=dry_run" "${output}"
  assert_contains "FINALIZE_PR_NUMBER=88" "${output}"
  assert_contains "FINALIZE_MERGE_STATUS=dry_run" "${output}"
}

test_pr_missing_pr_blocks() {
  local temp_dir cleanup_stub workspace pr_json output_file rc output
  temp_dir="$(mktemp -d)"
  cleanup_stub="${temp_dir}/cleanup.sh"
  workspace="${temp_dir}/ALL-90"
  pr_json="${temp_dir}/prs.json"
  output_file="${temp_dir}/out.txt"
  mkdir -p "${workspace}"
  make_cleanup_stub "${cleanup_stub}"
  echo '[]' > "${pr_json}"

  set +e
  bash "${SCRIPT_PATH}" \
    --delivery-mode pr \
    --workspace-dir "${workspace}" \
    --issue-identifier ALL-90 \
    --compose-project all-90 \
    --repo alliance-genome/agr_ai_curation \
    --branch all-90-branch \
    --cleanup-script "${cleanup_stub}" \
    --pr-json-file "${pr_json}" \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"

  [[ "${rc}" == "20" ]] || {
    echo "Expected exit code 20, got ${rc}" >&2
    exit 1
  }

  assert_contains "FINALIZE_STATUS=blocked_missing_pr" "${output}"
}

test_merge_conflict_bounces_to_in_progress() {
  local temp_dir cleanup_stub gh_stub workspace output_file rc output
  temp_dir="$(mktemp -d)"
  cleanup_stub="${temp_dir}/cleanup.sh"
  gh_stub="${temp_dir}/gh"
  workspace="${temp_dir}/ALL-99"
  output_file="${temp_dir}/out.txt"
  mkdir -p "${workspace}"
  make_cleanup_stub "${cleanup_stub}"
  make_gh_stub_merge_fails_conflict "${gh_stub}"

  # Create a minimal git repo so conflict analysis can run (fetch/merge will fail gracefully)
  git -C "${workspace}" init -q 2>/dev/null

  set +e
  PATH="${temp_dir}:${PATH}" bash "${SCRIPT_PATH}" \
    --delivery-mode pr \
    --workspace-dir "${workspace}" \
    --issue-identifier ALL-99 \
    --compose-project all-99 \
    --repo alliance-genome/agr_ai_curation \
    --branch all-99-branch \
    --cleanup-script "${cleanup_stub}" \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"

  [[ "${rc}" == "22" ]] || {
    echo "Expected exit code 22 (merge_conflict), got ${rc}" >&2
    printf 'Output:\n%s\n' "${output}" >&2
    exit 1
  }

  assert_contains "FINALIZE_STATUS=merge_conflict" "${output}"
  assert_contains "FINALIZE_NEXT_STATE=In Progress" "${output}"
  assert_contains "CONFLICT_DETECTED=true" "${output}"
  assert_contains "FINALIZE_PR_NUMBER=99" "${output}"
}

test_merge_conflict_exceeds_bounce_limit_blocks() {
  local temp_dir cleanup_stub gh_stub workspace output_file rc output
  temp_dir="$(mktemp -d)"
  cleanup_stub="${temp_dir}/cleanup.sh"
  gh_stub="${temp_dir}/gh"
  workspace="${temp_dir}/ALL-99b"
  output_file="${temp_dir}/out.txt"
  mkdir -p "${workspace}"
  make_cleanup_stub "${cleanup_stub}"
  make_gh_stub_merge_fails_conflict "${gh_stub}"

  git -C "${workspace}" init -q 2>/dev/null

  set +e
  PATH="${temp_dir}:${PATH}" bash "${SCRIPT_PATH}" \
    --delivery-mode pr \
    --workspace-dir "${workspace}" \
    --issue-identifier ALL-99 \
    --compose-project all-99 \
    --repo alliance-genome/agr_ai_curation \
    --branch all-99-branch \
    --cleanup-script "${cleanup_stub}" \
    --conflict-bounce-count 1 \
    --max-conflict-bounces 1 \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"

  [[ "${rc}" == "21" ]] || {
    echo "Expected exit code 21 (blocked), got ${rc}" >&2
    printf 'Output:\n%s\n' "${output}" >&2
    exit 1
  }

  assert_contains "FINALIZE_STATUS=blocked_merge_failed" "${output}"
  assert_contains "FINALIZE_NEXT_STATE=Blocked" "${output}"
}

test_non_conflict_merge_failure_blocks() {
  local temp_dir cleanup_stub gh_stub workspace output_file rc output
  temp_dir="$(mktemp -d)"
  cleanup_stub="${temp_dir}/cleanup.sh"
  gh_stub="${temp_dir}/gh"
  workspace="${temp_dir}/ALL-77"
  output_file="${temp_dir}/out.txt"
  mkdir -p "${workspace}"
  make_cleanup_stub "${cleanup_stub}"
  make_gh_stub_merge_fails_non_conflict "${gh_stub}"

  git -C "${workspace}" init -q 2>/dev/null

  set +e
  PATH="${temp_dir}:${PATH}" bash "${SCRIPT_PATH}" \
    --delivery-mode pr \
    --workspace-dir "${workspace}" \
    --issue-identifier ALL-77 \
    --compose-project all-77 \
    --repo alliance-genome/agr_ai_curation \
    --branch all-77-branch \
    --cleanup-script "${cleanup_stub}" \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"

  [[ "${rc}" == "21" ]] || {
    echo "Expected exit code 21 (blocked), got ${rc}" >&2
    printf 'Output:\n%s\n' "${output}" >&2
    exit 1
  }

  assert_contains "FINALIZE_STATUS=blocked_merge_failed" "${output}"
  assert_contains "FINALIZE_NEXT_STATE=Blocked" "${output}"
}

test_no_pr_finalizes_without_merge
test_pr_dry_run_reports_merge
test_successful_pr_finalization_defers_workspace_removal
test_pr_dry_run_infers_repo_from_origin
test_pr_missing_pr_blocks
test_merge_conflict_bounces_to_in_progress
test_merge_conflict_exceeds_bounce_limit_blocks
test_non_conflict_merge_failure_blocks

echo "symphony_finalize_issue tests passed"
