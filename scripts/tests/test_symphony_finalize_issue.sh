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

test_no_pr_finalizes_without_merge
test_pr_dry_run_reports_merge
test_pr_missing_pr_blocks

echo "symphony_finalize_issue tests passed"
