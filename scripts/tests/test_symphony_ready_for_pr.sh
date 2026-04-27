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

assert_not_contains() {
  local unexpected="$1"
  local actual="$2"
  if [[ "${actual}" == *"${unexpected}"* ]]; then
    echo "Expected output not to contain '${unexpected}'" >&2
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
{"headRefName":"all-42-branch","baseRefName":"main","headRefOid":"abc123","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","statusCheckRollup":[{"__typename":"CheckRun","name":"Agent PR Gate","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://example.test/checks/agent"}]}
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

test_dry_run_create_infers_title() {
  local temp_dir pr_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"

  echo '[]' > "${pr_json}"

  output="$(
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-54 \
      --branch all-54-branch \
      --repo alliance-genome/agr_ai_curation \
      --create-if-missing \
      --pr-json-file "${pr_json}" \
      --dry-run
  )"

  assert_contains "READY_FOR_PR_STATUS=dry_run_create" "${output}"
  assert_contains "READY_FOR_PR_PR_TITLE=ALL-54:" "${output}"
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
  echo '{"number":52,"title":"ALL-52: Example","url":"https://example.test/alliance-genome/agr_ai_curation/pull/52","headRefName":"all-52-branch","baseRefName":"main","headRefOid":"feedface","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","statusCheckRollup":[{"__typename":"CheckRun","name":"Agent PR Gate","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://example.test/checks/agent"}]}'
  exit 0
fi

echo "unexpected gh invocation: $*" >&2
exit 99
EOF
  chmod +x "${gh_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" \
    GH_STUB_LOG="${log_file}" \
    SYMPHONY_READY_FOR_PR_CLAUDE_LOOP_HELPER="${temp_dir}/missing-claude-loop" \
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

test_claude_detected_auto_bounces_to_in_progress() {
  local temp_dir pr_json pr_view_json loop_stub workpad_stub state_stub report_file workpad_log state_log section_log output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  workpad_stub="${temp_dir}/workpad"
  state_stub="${temp_dir}/state"
  report_file="${temp_dir}/claude-report.md"
  workpad_log="${temp_dir}/workpad.log"
  state_log="${temp_dir}/state.log"
  section_log="${temp_dir}/section.log"

  cat > "${pr_json}" <<'EOF'
[{"number":269,"title":"ALL-293: Existing PR","url":"https://example.test/pr/269","headRefName":"all-293"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"number":269,"title":"ALL-293: Existing PR","url":"https://example.test/pr/269","headRefName":"all-293","baseRefName":"main","headRefOid":"abc293","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-04-25T16:45:00Z"}
EOF

  printf 'latest feedback\n' > "${report_file}"
  cat > "${loop_stub}" <<EOF
#!/usr/bin/env bash
cat <<'OUT'
CLAUDE_LOOP_STATUS=detected
CLAUDE_LOOP_REPORT_FILE=${report_file}
CLAUDE_LOOP_ROUND=2
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
exit 10
EOF
  chmod +x "${loop_stub}"

  cat > "${workpad_stub}" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" > "${workpad_log}"
while [[ \$# -gt 0 ]]; do
  if [[ "\$1" == "--section-file" ]]; then
    cat "\$2" > "${section_log}"
    break
  fi
  shift
done
echo WORKPAD_STATUS=updated
EOF
  chmod +x "${workpad_stub}"

  cat > "${state_stub}" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" > "${state_log}"
echo LINEAR_STATE_STATUS=ok
EOF
  chmod +x "${state_stub}"

  output="$(
    SYMPHONY_READY_FOR_PR_CLAUDE_LOOP_HELPER="${loop_stub}" \
    SYMPHONY_READY_FOR_PR_WORKPAD_HELPER="${workpad_stub}" \
    SYMPHONY_READY_FOR_PR_STATE_HELPER="${state_stub}" \
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-293 \
      --branch all-293 \
      --repo alliance-genome/agr_ai_curation \
      --wait-for-review-seconds 1 \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}"
  )"

  assert_contains "READY_FOR_PR_CLAUDE_STATUS=detected" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_ACTION=bounced_to_in_progress" "${output}"
  assert_contains "READY_FOR_PR_NEXT_STATE=In Progress" "${output}"
  assert_contains "append-section --issue-identifier ALL-293 --section-title PR Handoff" "$(cat "${workpad_log}")"
  assert_contains "--state In Progress --from-state Ready for PR" "$(cat "${state_log}")"
  assert_contains "Claude report: ${report_file}" "$(cat "${section_log}")"
  assert_contains "triage the latest Claude feedback first" "$(cat "${section_log}")"
  assert_contains "move directly to Human Review Prep without editing code" "$(cat "${section_log}")"
}

test_claude_wait_zero_still_scans_existing_feedback() {
  local temp_dir pr_json pr_view_json loop_stub workpad_stub state_stub report_file output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  workpad_stub="${temp_dir}/workpad"
  state_stub="${temp_dir}/state"
  report_file="${temp_dir}/claude-report.md"

  cat > "${pr_json}" <<'EOF'
[{"number":301,"title":"ALL-301: Existing PR","url":"https://example.test/pr/301","headRefName":"all-301"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"number":301,"title":"ALL-301: Existing PR","url":"https://example.test/pr/301","headRefName":"all-301","baseRefName":"main","headRefOid":"abc301","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-04-25T18:53:14Z"}
EOF

  printf 'latest feedback\n' > "${report_file}"
  cat > "${loop_stub}" <<EOF
#!/usr/bin/env bash
if [[ " \$* " != *" --wait-seconds 0 "* ]]; then
  echo "expected zero-second scan" >&2
  exit 98
fi
cat <<'OUT'
CLAUDE_LOOP_STATUS=detected
CLAUDE_LOOP_REPORT_FILE=${report_file}
CLAUDE_LOOP_ROUND=1
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
exit 10
EOF
  chmod +x "${loop_stub}"

  cat > "${workpad_stub}" <<'EOF'
#!/usr/bin/env bash
echo WORKPAD_STATUS=updated
EOF
  chmod +x "${workpad_stub}"

  cat > "${state_stub}" <<'EOF'
#!/usr/bin/env bash
echo LINEAR_STATE_STATUS=ok
EOF
  chmod +x "${state_stub}"

  output="$(
    SYMPHONY_READY_FOR_PR_CLAUDE_LOOP_HELPER="${loop_stub}" \
    SYMPHONY_READY_FOR_PR_WORKPAD_HELPER="${workpad_stub}" \
    SYMPHONY_READY_FOR_PR_STATE_HELPER="${state_stub}" \
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-301 \
      --branch all-301 \
      --repo alliance-genome/agr_ai_curation \
      --wait-for-review-seconds 0 \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}"
  )"

  assert_contains "READY_FOR_PR_CLAUDE_STATUS=detected" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_ACTION=bounced_to_in_progress" "${output}"
}

test_claude_pending_stops_before_check_gate() {
  local temp_dir pr_json pr_view_json loop_stub output_file output rc
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  output_file="${temp_dir}/out.txt"

  cat > "${pr_json}" <<'EOF'
[{"number":302,"title":"ALL-302: Existing PR","url":"https://example.test/pr/302","headRefName":"all-302"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"number":302,"title":"ALL-302: Existing PR","url":"https://example.test/pr/302","headRefName":"all-302","baseRefName":"main","headRefOid":"abc302","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-04-25T18:53:14Z","statusCheckRollup":[{"__typename":"CheckRun","name":"Agent PR Gate","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://example.test/checks/agent"}]}
EOF

  cat > "${loop_stub}" <<'EOF'
#!/usr/bin/env bash
cat <<'OUT'
CLAUDE_LOOP_STATUS=pending
CLAUDE_LOOP_ROUND=2
CLAUDE_LOOP_MAX_ROUNDS=5
CLAUDE_LOOP_LATEST_AT=2026-04-25T18:55:00Z
CLAUDE_LOOP_WAIT_SINCE=2026-04-25T18:55:00Z
OUT
EOF
  chmod +x "${loop_stub}"

  set +e
  SYMPHONY_READY_FOR_PR_CLAUDE_LOOP_HELPER="${loop_stub}" \
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-302 \
      --branch all-302 \
      --repo alliance-genome/agr_ai_curation \
      --wait-for-review-seconds 0 \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}" \
      > "${output_file}"
  rc=$?
  set -e
  output="$(cat "${output_file}")"

  [[ "${rc}" == "11" ]] || {
    echo "Expected exit code 11, got ${rc}" >&2
    printf 'Actual output:\n%s\n' "${output}" >&2
    exit 1
  }

  assert_contains "READY_FOR_PR_CLAUDE_STATUS=pending" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_ROUND=2" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_WAIT_SINCE=2026-04-25T18:55:00Z" "${output}"
  assert_contains "Do not move to Human Review Prep" "${output}"
  assert_not_contains "READY_FOR_PR_CHECK_STATUS=clean" "${output}"
}

test_failed_github_check_auto_bounces_to_in_progress() {
  local temp_dir pr_json pr_view_json loop_stub workpad_stub state_stub workpad_log state_log section_log output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  workpad_stub="${temp_dir}/workpad"
  state_stub="${temp_dir}/state"
  workpad_log="${temp_dir}/workpad.log"
  state_log="${temp_dir}/state.log"
  section_log="${temp_dir}/section.log"

  cat > "${pr_json}" <<'EOF'
[{"number":271,"title":"ALL-301: Existing PR","url":"https://example.test/pr/271","headRefName":"all-301"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"number":271,"title":"ALL-301: Existing PR","url":"https://example.test/pr/271","headRefName":"all-301","baseRefName":"main","headRefOid":"abc301","mergeable":"MERGEABLE","mergeStateStatus":"UNSTABLE","createdAt":"2026-04-25T18:53:14Z","statusCheckRollup":[{"__typename":"CheckRun","name":"GitGuardian Security Checks","status":"COMPLETED","conclusion":"FAILURE","detailsUrl":"https://dashboard.gitguardian.com"},{"__typename":"CheckRun","name":"Agent PR Gate","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://example.test/checks/agent"}]}
EOF

  cat > "${loop_stub}" <<'EOF'
#!/usr/bin/env bash
cat <<'OUT'
CLAUDE_LOOP_STATUS=quiet
CLAUDE_LOOP_ROUND=1
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
EOF
  chmod +x "${loop_stub}"

  cat > "${workpad_stub}" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" > "${workpad_log}"
while [[ \$# -gt 0 ]]; do
  if [[ "\$1" == "--section-file" ]]; then
    cat "\$2" > "${section_log}"
    break
  fi
  shift
done
echo WORKPAD_STATUS=updated
EOF
  chmod +x "${workpad_stub}"

  cat > "${state_stub}" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" > "${state_log}"
echo LINEAR_STATE_STATUS=ok
EOF
  chmod +x "${state_stub}"

  output="$(
    SYMPHONY_READY_FOR_PR_CLAUDE_LOOP_HELPER="${loop_stub}" \
    SYMPHONY_READY_FOR_PR_WORKPAD_HELPER="${workpad_stub}" \
    SYMPHONY_READY_FOR_PR_STATE_HELPER="${state_stub}" \
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-301 \
      --branch all-301 \
      --repo alliance-genome/agr_ai_curation \
      --wait-for-review-seconds 0 \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}"
  )"

  assert_contains "READY_FOR_PR_CHECK_STATUS=failed" "${output}"
  assert_contains "READY_FOR_PR_CHECK_ACTION=bounced_to_in_progress" "${output}"
  assert_contains "--state In Progress --from-state Ready for PR" "$(cat "${state_log}")"
  assert_contains "GitGuardian Security Checks: FAILURE" "$(cat "${section_log}")"
  assert_contains "address the failed PR checks first" "$(cat "${section_log}")"
}

test_claude_maxed_out_without_report_does_not_abort() {
  local temp_dir pr_json pr_view_json loop_stub output_file output rc
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  output_file="${temp_dir}/out.txt"

  cat > "${pr_json}" <<'EOF'
[{"number":270,"title":"ALL-270: Existing PR","url":"https://example.test/pr/270","headRefName":"all-270"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"number":270,"title":"ALL-270: Existing PR","url":"https://example.test/pr/270","headRefName":"all-270","baseRefName":"main","headRefOid":"abc270","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-04-25T16:45:00Z","statusCheckRollup":[{"__typename":"CheckRun","name":"Agent PR Gate","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://example.test/checks/agent"}]}
EOF

  cat > "${loop_stub}" <<'EOF'
#!/usr/bin/env bash
cat <<'OUT'
CLAUDE_LOOP_STATUS=maxed_out
CLAUDE_LOOP_ROUND=5
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
EOF
  chmod +x "${loop_stub}"

  set +e
  SYMPHONY_READY_FOR_PR_CLAUDE_LOOP_HELPER="${loop_stub}" \
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-270 \
      --branch all-270 \
      --repo alliance-genome/agr_ai_curation \
      --wait-for-review-seconds 1 \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}" \
      > "${output_file}"
  rc=$?
  set -e
  output="$(cat "${output_file}")"

  [[ "${rc}" == "0" ]] || {
    echo "Expected exit code 0, got ${rc}" >&2
    printf 'Actual output:\n%s\n' "${output}" >&2
    exit 1
  }

  assert_contains "READY_FOR_PR_CLAUDE_STATUS=maxed_out" "${output}"
  assert_contains "READY_FOR_PR_CHECK_STATUS=clean" "${output}"
}

test_no_pr_skips_lane
test_existing_pr_is_reported
test_conflicted_pr_routes_back_to_in_progress
test_missing_pr_reports_nonzero
test_base_branch_is_rejected
test_dry_run_create_reports_title
test_dry_run_create_infers_title
test_create_pr_uses_plain_cli_output_and_view_json
test_claude_detected_auto_bounces_to_in_progress
test_claude_wait_zero_still_scans_existing_feedback
test_claude_pending_stops_before_check_gate
test_failed_github_check_auto_bounces_to_in_progress
test_claude_maxed_out_without_report_does_not_abort

echo "symphony_ready_for_pr tests passed"
