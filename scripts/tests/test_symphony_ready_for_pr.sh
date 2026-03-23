#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_ready_for_pr.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

test_no_pr_skips_lane() {
  local output
  output="$(
    bash "${SCRIPT_PATH}" \
      --delivery-mode no_pr \
      --issue-identifier ALL-46 \
      --branch all-46-no-pr
  )"

  assert_contains "READY_FOR_PR_STATUS=skip_no_pr" "${output}"
  assert_contains "READY_FOR_PR_NEXT_STATE=Human Review Prep" "${output}"
}

test_existing_pr_is_reported() {
  local temp_dir pr_json pr_view_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"

  cat > "${pr_json}" <<'EOF'
[{"number":42,"title":"ALL-42: Existing PR","url":"https://example.test/pr/42","headRefName":"all-42-branch"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"headRefName":"all-42-branch","baseRefName":"main","headRefOid":"abc123","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN"}
EOF

  output="$(
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-42 \
      --branch all-42-branch \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}"
  )"

  assert_contains "READY_FOR_PR_STATUS=existing_pr" "${output}"
  assert_contains "READY_FOR_PR_PR_NUMBER=42" "${output}"
  assert_contains "READY_FOR_PR_PR_HEAD_SHA=abc123" "${output}"
  assert_contains "READY_FOR_PR_PR_MERGEABLE=MERGEABLE" "${output}"
  assert_contains "READY_FOR_PR_PR_MERGE_STATE_STATUS=CLEAN" "${output}"
}

test_conflicted_pr_routes_back_to_in_progress() {
  local temp_dir pr_json pr_view_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"

  cat > "${pr_json}" <<'EOF'
[{"number":81,"title":"ALL-81: Existing PR","url":"https://example.test/pr/81","headRefName":"all-81-branch"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"headRefName":"all-81-branch","baseRefName":"main","headRefOid":"deadbeef","mergeable":"CONFLICTING","mergeStateStatus":"DIRTY"}
EOF

  output="$(
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-81 \
      --branch all-81-branch \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}"
  )"

  assert_contains "READY_FOR_PR_STATUS=existing_pr_conflicted" "${output}"
  assert_contains "READY_FOR_PR_NEXT_STATE=In Progress" "${output}"
  assert_contains "READY_FOR_PR_PR_HEAD_SHA=deadbeef" "${output}"
  assert_contains "READY_FOR_PR_PR_MERGEABLE=CONFLICTING" "${output}"
  assert_contains "READY_FOR_PR_PR_MERGE_STATE_STATUS=DIRTY" "${output}"
}

test_missing_pr_reports_nonzero() {
  local temp_dir pr_json output_file rc output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  output_file="${temp_dir}/out.txt"

  echo '[]' > "${pr_json}"

  set +e
  bash "${SCRIPT_PATH}" \
    --delivery-mode pr \
    --issue-identifier ALL-50 \
    --branch all-50-branch \
    --pr-json-file "${pr_json}" \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"

  [[ "${rc}" == "20" ]] || {
    echo "Expected exit code 20, got ${rc}" >&2
    exit 1
  }

  assert_contains "READY_FOR_PR_STATUS=missing_pr" "${output}"
}

test_base_branch_is_rejected() {
  local temp_dir output_file rc output
  temp_dir="$(mktemp -d)"
  output_file="${temp_dir}/out.txt"

  set +e
  bash "${SCRIPT_PATH}" \
    --delivery-mode pr \
    --issue-identifier ALL-53 \
    --branch main \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"

  [[ "${rc}" == "21" ]] || {
    echo "Expected exit code 21, got ${rc}" >&2
    exit 1
  }

  assert_contains "READY_FOR_PR_STATUS=invalid_branch" "${output}"
  assert_contains "READY_FOR_PR_NEXT_STATE=In Progress" "${output}"
}

test_dry_run_create_reports_title() {
  local temp_dir pr_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"

  echo '[]' > "${pr_json}"

  output="$(
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-51 \
      --branch all-51-branch \
      --repo alliance-genome/agr_ai_curation \
      --create-if-missing \
      --title "ALL-51: Example" \
      --pr-json-file "${pr_json}" \
      --dry-run
  )"

  assert_contains "READY_FOR_PR_STATUS=dry_run_create" "${output}"
  assert_contains "READY_FOR_PR_PR_TITLE=ALL-51: Example" "${output}"
}

test_create_pr_uses_plain_cli_output_and_view_json() {
  local temp_dir gh_stub log_file output
  temp_dir="$(mktemp -d)"
  gh_stub="${temp_dir}/gh"
  log_file="${temp_dir}/gh.log"

  cat > "${gh_stub}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${GH_STUB_LOG}"

if [[ "${1:-}" == "pr" && "${2:-}" == "list" ]]; then
  echo '[]'
  exit 0
fi

if [[ "${1:-}" == "pr" && "${2:-}" == "create" ]]; then
  if [[ " $* " == *" --json "* ]]; then
    echo "unexpected --json flag for gh pr create" >&2
    exit 97
  fi
  if [[ " $* " != *" --body "* && " $* " != *" --body-file "* ]]; then
    echo "expected non-interactive body handling" >&2
    exit 98
  fi
  echo 'https://example.test/alliance-genome/agr_ai_curation/pull/52'
  exit 0
fi

if [[ "${1:-}" == "pr" && "${2:-}" == "view" ]]; then
  echo '{"number":52,"title":"ALL-52: Example","url":"https://example.test/alliance-genome/agr_ai_curation/pull/52","headRefName":"all-52-branch","baseRefName":"main","headRefOid":"feedface","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN"}'
  exit 0
fi

echo "unexpected gh invocation: $*" >&2
exit 99
EOF
  chmod +x "${gh_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" \
    GH_STUB_LOG="${log_file}" \
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-52 \
      --branch all-52-branch \
      --repo alliance-genome/agr_ai_curation \
      --create-if-missing \
      --title "ALL-52: Example"
  )"

  assert_contains "READY_FOR_PR_STATUS=created_pr" "${output}"
  assert_contains "READY_FOR_PR_PR_NUMBER=52" "${output}"
  assert_contains "READY_FOR_PR_PR_TITLE=ALL-52: Example" "${output}"
  assert_contains "READY_FOR_PR_PR_URL=https://example.test/alliance-genome/agr_ai_curation/pull/52" "${output}"
}

test_no_pr_skips_lane
test_existing_pr_is_reported
test_conflicted_pr_routes_back_to_in_progress
test_missing_pr_reports_nonzero
test_base_branch_is_rejected
test_dry_run_create_reports_title
test_create_pr_uses_plain_cli_output_and_view_json

echo "symphony_ready_for_pr tests passed"
