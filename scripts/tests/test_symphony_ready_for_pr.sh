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

test_repo_mismatch_is_rejected_when_origin_is_known() {
  local temp_dir output_file rc output
  temp_dir="$(mktemp -d)"
  output_file="${temp_dir}/out.txt"

  git -C "${temp_dir}" init -q
  git -C "${temp_dir}" remote add origin git@github.com:alliance-genome/agr_ai_curation.git

  set +e
  (
    cd "${temp_dir}"
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-338 \
      --branch all-338 \
      --repo alliance-genome-resources/agr_ai_curation \
      --create-if-missing
  ) > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"

  [[ "${rc}" == "2" ]] || {
    echo "Expected exit code 2, got ${rc}" >&2
    exit 1
  }

  assert_contains "READY_FOR_PR_STATUS=repo_mismatch" "${output}"
  assert_contains "READY_FOR_PR_REPO=alliance-genome-resources/agr_ai_curation" "${output}"
  assert_contains "READY_FOR_PR_ORIGIN_REPO=alliance-genome/agr_ai_curation" "${output}"
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
  local temp_dir pr_json pr_view_json loop_stub classifier_stub workpad_stub state_stub report_file workpad_log state_log section_log output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  classifier_stub="${temp_dir}/classifier"
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
{"number":269,"title":"ALL-293: Existing PR","url":"https://example.test/pr/269","headRefName":"all-293","baseRefName":"main","headRefOid":"abc293","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-04-25T16:45:00Z","statusCheckRollup":[{"__typename":"CheckRun","name":"Agent PR Gate","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://example.test/checks/agent"}]}
EOF

  printf 'latest feedback\n' > "${report_file}"
  cat > "${loop_stub}" <<EOF
#!/usr/bin/env bash
cat <<'OUT'
CLAUDE_LOOP_STATUS=actionable_feedback
CLAUDE_LOOP_REPORT_FILE=${report_file}
CLAUDE_LOOP_ROUND=2
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
exit 10
EOF
  chmod +x "${loop_stub}"

  cat > "${classifier_stub}" <<'EOF'
#!/usr/bin/env bash
echo PR_FEEDBACK_CLASSIFIER_STATUS=actionable
echo PR_FEEDBACK_CLASSIFIER_CLASSIFICATION=actionable
echo PR_FEEDBACK_CLASSIFIER_REASON=Review asks for implementation work.
exit 10
EOF
  chmod +x "${classifier_stub}"

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
    SYMPHONY_READY_FOR_PR_FEEDBACK_CLASSIFIER_HELPER="${classifier_stub}" \
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

  assert_contains "READY_FOR_PR_CLAUDE_STATUS=actionable_feedback" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_ACTION=bounced_to_in_progress" "${output}"
  assert_contains "READY_FOR_PR_NEXT_STATE=In Progress" "${output}"
  assert_contains "append-section --issue-identifier ALL-293 --section-title PR Handoff" "$(cat "${workpad_log}")"
  assert_contains "--state In Progress --from-state Ready for PR" "$(cat "${state_log}")"
  assert_contains "Claude report: ${report_file}" "$(cat "${section_log}")"
  assert_contains "triage the latest Claude feedback first" "$(cat "${section_log}")"
  assert_contains "Treat substantive suggestions" "$(cat "${section_log}")"
  assert_contains "ticket file lists are suggested starting locations" "$(cat "${section_log}")"
}

test_claude_wait_zero_still_scans_existing_feedback() {
  local temp_dir pr_json pr_view_json loop_stub classifier_stub workpad_stub state_stub report_file output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  classifier_stub="${temp_dir}/classifier"
  workpad_stub="${temp_dir}/workpad"
  state_stub="${temp_dir}/state"
  report_file="${temp_dir}/claude-report.md"

  cat > "${pr_json}" <<'EOF'
[{"number":301,"title":"ALL-301: Existing PR","url":"https://example.test/pr/301","headRefName":"all-301"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"number":301,"title":"ALL-301: Existing PR","url":"https://example.test/pr/301","headRefName":"all-301","baseRefName":"main","headRefOid":"abc301","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-04-25T18:53:14Z","statusCheckRollup":[{"__typename":"CheckRun","name":"Agent PR Gate","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://example.test/checks/agent"}]}
EOF

  printf 'latest feedback\n' > "${report_file}"
  cat > "${loop_stub}" <<EOF
#!/usr/bin/env bash
if [[ " \$* " != *" --wait-seconds 0 "* ]]; then
  echo "expected zero-second scan" >&2
  exit 98
fi
cat <<'OUT'
CLAUDE_LOOP_STATUS=actionable_feedback
CLAUDE_LOOP_REPORT_FILE=${report_file}
CLAUDE_LOOP_ROUND=1
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
exit 10
EOF
  chmod +x "${loop_stub}"

  cat > "${classifier_stub}" <<'EOF'
#!/usr/bin/env bash
echo PR_FEEDBACK_CLASSIFIER_STATUS=actionable
echo PR_FEEDBACK_CLASSIFIER_CLASSIFICATION=actionable
echo PR_FEEDBACK_CLASSIFIER_REASON=Review asks for implementation work.
exit 10
EOF
  chmod +x "${classifier_stub}"

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
    SYMPHONY_READY_FOR_PR_FEEDBACK_CLASSIFIER_HELPER="${classifier_stub}" \
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

  assert_contains "READY_FOR_PR_CLAUDE_STATUS=actionable_feedback" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_ACTION=bounced_to_in_progress" "${output}"
}

test_clean_claude_review_does_not_auto_bounce() {
  local temp_dir pr_json pr_view_json loop_stub classifier_stub classifier_log disposition_file workpad_stub state_stub report_file output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  classifier_stub="${temp_dir}/classifier"
  classifier_log="${temp_dir}/classifier.log"
  disposition_file="${temp_dir}/disposition.md"
  workpad_stub="${temp_dir}/workpad"
  state_stub="${temp_dir}/state"
  report_file="${temp_dir}/claude-report.md"

  cat > "${pr_json}" <<'EOF'
[{"number":341,"title":"ALL-341: Existing PR","url":"https://example.test/pr/341","headRefName":"all-341"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"number":341,"title":"ALL-341: Existing PR","url":"https://example.test/pr/341","headRefName":"all-341","baseRefName":"main","headRefOid":"abc341","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-05-04T00:13:22Z","statusCheckRollup":[{"__typename":"CheckRun","name":"Agent PR Gate","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://example.test/checks/agent"}]}
EOF

  cat > "${report_file}" <<'EOF'
# Claude Code Review Report — PR #341

## 1. review

### BLOCKING Issues

None.

### Assessment

Previous approval stands. **Approve.**
EOF

  printf '%s\n' '- Prior retry finding was already resolved in commit abc123.' > "${disposition_file}"

  cat > "${loop_stub}" <<EOF
#!/usr/bin/env bash
cat <<'OUT'
CLAUDE_LOOP_STATUS=actionable_feedback
CLAUDE_LOOP_REPORT_FILE=${report_file}
CLAUDE_LOOP_ROUND=3
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
exit 10
EOF
  chmod +x "${loop_stub}"

  cat > "${classifier_stub}" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "${CLASSIFIER_LOG}"
echo PR_FEEDBACK_CLASSIFIER_STATUS=clean
echo PR_FEEDBACK_CLASSIFIER_CLASSIFICATION=clean
echo PR_FEEDBACK_CLASSIFIER_REASON=Review clearly approves with no remaining work.
exit 0
EOF
  chmod +x "${classifier_stub}"

  cat > "${workpad_stub}" <<'EOF'
#!/usr/bin/env bash
echo "workpad should not be called for clean Claude reviews" >&2
exit 97
EOF
  chmod +x "${workpad_stub}"

  cat > "${state_stub}" <<'EOF'
#!/usr/bin/env bash
echo "state helper should not be called for clean Claude reviews" >&2
exit 98
EOF
  chmod +x "${state_stub}"

  output="$(
    SYMPHONY_READY_FOR_PR_CLAUDE_LOOP_HELPER="${loop_stub}" \
    SYMPHONY_READY_FOR_PR_FEEDBACK_CLASSIFIER_HELPER="${classifier_stub}" \
    SYMPHONY_READY_FOR_PR_WORKPAD_HELPER="${workpad_stub}" \
    SYMPHONY_READY_FOR_PR_STATE_HELPER="${state_stub}" \
    CLASSIFIER_LOG="${classifier_log}" \
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-341 \
      --branch all-341 \
      --repo alliance-genome/agr_ai_curation \
      --wait-for-review-seconds 1 \
      --disposition-file "${disposition_file}" \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}"
  )"

  assert_contains "READY_FOR_PR_CLAUDE_STATUS=actionable_feedback" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_ACTION=clean_review_no_bounce" "${output}"
  assert_contains "READY_FOR_PR_CHECK_STATUS=clean" "${output}"
  assert_contains "move to Human Review Prep" "${output}"
  assert_not_contains "READY_FOR_PR_CLAUDE_ACTION=bounced_to_in_progress" "${output}"
  assert_contains "--github-check-status" "$(cat "${classifier_log}")"
  assert_contains "clean" "$(cat "${classifier_log}")"
  assert_contains "--disposition-file" "$(cat "${classifier_log}")"
  assert_contains "${disposition_file}" "$(cat "${classifier_log}")"
}

test_default_classifier_uses_source_root_fallback() {
  local temp_dir workspace source_root runner_path pr_json pr_view_json loop_stub classifier_stub workpad_stub state_stub report_file output
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  source_root="${temp_dir}/source"
  runner_path="${workspace}/scripts/utilities/symphony_ready_for_pr.sh"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  classifier_stub="${source_root}/scripts/utilities/symphony_classify_pr_feedback.sh"
  workpad_stub="${temp_dir}/workpad"
  state_stub="${temp_dir}/state"
  report_file="${temp_dir}/claude-report.md"
  mkdir -p "${workspace}/scripts/utilities" "${source_root}/scripts/utilities"
  cp "${SCRIPT_PATH}" "${runner_path}"
  chmod +x "${runner_path}"

  cat > "${pr_json}" <<'EOF'
[{"number":344,"title":"ALL-344: Existing PR","url":"https://example.test/pr/344","headRefName":"all-344"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"number":344,"title":"ALL-344: Existing PR","url":"https://example.test/pr/344","headRefName":"all-344","baseRefName":"main","headRefOid":"abc344","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-05-04T00:13:22Z","statusCheckRollup":[{"__typename":"CheckRun","name":"Agent PR Gate","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://example.test/checks/agent"}]}
EOF

  printf 'clean approval\n' > "${report_file}"

  cat > "${loop_stub}" <<EOF
#!/usr/bin/env bash
cat <<'OUT'
CLAUDE_LOOP_STATUS=actionable_feedback
CLAUDE_LOOP_REPORT_FILE=${report_file}
CLAUDE_LOOP_ROUND=1
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
exit 10
EOF
  chmod +x "${loop_stub}"

  cat > "${classifier_stub}" <<'EOF'
#!/usr/bin/env bash
echo PR_FEEDBACK_CLASSIFIER_STATUS=clean
echo PR_FEEDBACK_CLASSIFIER_CLASSIFICATION=clean
echo PR_FEEDBACK_CLASSIFIER_REASON=source root fallback
exit 0
EOF
  chmod +x "${classifier_stub}"

  cat > "${workpad_stub}" <<'EOF'
#!/usr/bin/env bash
echo "workpad should not be called for clean Claude reviews" >&2
exit 97
EOF
  chmod +x "${workpad_stub}"

  cat > "${state_stub}" <<'EOF'
#!/usr/bin/env bash
echo "state helper should not be called for clean Claude reviews" >&2
exit 98
EOF
  chmod +x "${state_stub}"

  output="$(
    (
      cd "${temp_dir}"
      SYMPHONY_LOCAL_SOURCE_ROOT="${source_root}" \
      SYMPHONY_READY_FOR_PR_CLAUDE_LOOP_HELPER="${loop_stub}" \
      SYMPHONY_READY_FOR_PR_WORKPAD_HELPER="${workpad_stub}" \
      SYMPHONY_READY_FOR_PR_STATE_HELPER="${state_stub}" \
      bash "${runner_path}" \
        --delivery-mode pr \
        --issue-identifier ALL-344 \
        --branch all-344 \
        --repo alliance-genome/agr_ai_curation \
        --wait-for-review-seconds 1 \
        --pr-json-file "${pr_json}" \
        --pr-view-json-file "${pr_view_json}"
    )
  )"

  assert_contains "PR_FEEDBACK_CLASSIFIER_REASON=source root fallback" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_ACTION=clean_review_no_bounce" "${output}"
  assert_not_contains "READY_FOR_PR_CLAUDE_CLASSIFIER_WARNING" "${output}"
}

test_approval_with_actionable_suggestions_still_auto_bounces() {
  local temp_dir pr_json pr_view_json loop_stub classifier_stub workpad_stub state_stub report_file output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  classifier_stub="${temp_dir}/classifier"
  workpad_stub="${temp_dir}/workpad"
  state_stub="${temp_dir}/state"
  report_file="${temp_dir}/claude-report.md"

  cat > "${pr_json}" <<'EOF'
[{"number":342,"title":"ALL-342: Existing PR","url":"https://example.test/pr/342","headRefName":"all-342"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"number":342,"title":"ALL-342: Existing PR","url":"https://example.test/pr/342","headRefName":"all-342","baseRefName":"main","headRefOid":"abc342","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-05-04T00:13:22Z","statusCheckRollup":[{"__typename":"CheckRun","name":"Agent PR Gate","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://example.test/checks/agent"}]}
EOF

  cat > "${report_file}" <<'EOF'
LGTM overall.

Non-blocking issues:
- Please add a regression test for the retry path before final handoff.
EOF

  cat > "${loop_stub}" <<EOF
#!/usr/bin/env bash
cat <<'OUT'
CLAUDE_LOOP_STATUS=actionable_feedback
CLAUDE_LOOP_REPORT_FILE=${report_file}
CLAUDE_LOOP_ROUND=1
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
exit 10
EOF
  chmod +x "${loop_stub}"

  cat > "${classifier_stub}" <<'EOF'
#!/usr/bin/env bash
echo PR_FEEDBACK_CLASSIFIER_STATUS=actionable
echo PR_FEEDBACK_CLASSIFIER_CLASSIFICATION=actionable
echo PR_FEEDBACK_CLASSIFIER_REASON=Review includes non-blocking implementation work.
echo PR_FEEDBACK_CLASSIFIER_ACTION_ITEM_1=Add a regression test.
exit 10
EOF
  chmod +x "${classifier_stub}"

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
    SYMPHONY_READY_FOR_PR_FEEDBACK_CLASSIFIER_HELPER="${classifier_stub}" \
    SYMPHONY_READY_FOR_PR_WORKPAD_HELPER="${workpad_stub}" \
    SYMPHONY_READY_FOR_PR_STATE_HELPER="${state_stub}" \
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-342 \
      --branch all-342 \
      --repo alliance-genome/agr_ai_curation \
      --wait-for-review-seconds 1 \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}"
  )"

  assert_contains "READY_FOR_PR_CLAUDE_STATUS=actionable_feedback" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_ACTION=bounced_to_in_progress" "${output}"
  assert_not_contains "READY_FOR_PR_CLAUDE_ACTION=clean_review_no_bounce" "${output}"
}

test_classifier_error_is_conservative_and_auto_bounces() {
  local temp_dir pr_json pr_view_json loop_stub classifier_stub workpad_stub state_stub report_file output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  classifier_stub="${temp_dir}/classifier"
  workpad_stub="${temp_dir}/workpad"
  state_stub="${temp_dir}/state"
  report_file="${temp_dir}/claude-report.md"

  cat > "${pr_json}" <<'EOF'
[{"number":343,"title":"ALL-343: Existing PR","url":"https://example.test/pr/343","headRefName":"all-343"}]
EOF

  cat > "${pr_view_json}" <<'EOF'
{"number":343,"title":"ALL-343: Existing PR","url":"https://example.test/pr/343","headRefName":"all-343","baseRefName":"main","headRefOid":"abc343","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-05-04T00:13:22Z","statusCheckRollup":[{"__typename":"CheckRun","name":"Agent PR Gate","status":"COMPLETED","conclusion":"SUCCESS","detailsUrl":"https://example.test/checks/agent"}]}
EOF

  printf 'Claude report that the classifier cannot parse.\n' > "${report_file}"

  cat > "${loop_stub}" <<EOF
#!/usr/bin/env bash
cat <<'OUT'
CLAUDE_LOOP_STATUS=actionable_feedback
CLAUDE_LOOP_REPORT_FILE=${report_file}
CLAUDE_LOOP_ROUND=1
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
exit 10
EOF
  chmod +x "${loop_stub}"

  cat > "${classifier_stub}" <<'EOF'
#!/usr/bin/env bash
echo PR_FEEDBACK_CLASSIFIER_STATUS=error
echo PR_FEEDBACK_CLASSIFIER_ERROR=simulated failure
exit 2
EOF
  chmod +x "${classifier_stub}"

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
    SYMPHONY_READY_FOR_PR_FEEDBACK_CLASSIFIER_HELPER="${classifier_stub}" \
    SYMPHONY_READY_FOR_PR_WORKPAD_HELPER="${workpad_stub}" \
    SYMPHONY_READY_FOR_PR_STATE_HELPER="${state_stub}" \
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-343 \
      --branch all-343 \
      --repo alliance-genome/agr_ai_curation \
      --wait-for-review-seconds 1 \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}"
  )"

  assert_contains "PR_FEEDBACK_CLASSIFIER_STATUS=error" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_CLASSIFIER_WARNING=Could not classify Claude report safely" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_ACTION=bounced_to_in_progress" "${output}"
  assert_not_contains "READY_FOR_PR_CLAUDE_ACTION=clean_review_no_bounce" "${output}"
}

test_claude_pending_after_clean_checks_stops_before_human_review() {
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
CLAUDE_LOOP_WORKFLOW_STATUS=pending
CLAUDE_LOOP_WORKFLOW_RUN_ID=25006064832
CLAUDE_LOOP_WORKFLOW_RUN_URL=https://example.test/actions/runs/25006064832
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
  assert_contains "READY_FOR_PR_CLAUDE_WORKFLOW_STATUS=pending" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_WORKFLOW_RUN_URL=https://example.test/actions/runs/25006064832" "${output}"
  assert_contains "READY_FOR_PR_CHECK_STATUS=clean" "${output}"
  assert_contains "Do not move to Human Review Prep" "${output}"
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
echo "Claude loop should not run while GitHub checks are failing" >&2
exit 97
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

test_actionable_claude_feedback_stops_pending_checks_and_cancels_exact_head() {
  local temp_dir pr_json pr_view_json loop_stub classifier_stub workpad_stub state_stub
  local report_file bin_dir cancel_log loop_log output_file output rc
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  classifier_stub="${temp_dir}/classifier"
  workpad_stub="${temp_dir}/workpad"
  state_stub="${temp_dir}/state"
  report_file="${temp_dir}/claude-report.md"
  bin_dir="${temp_dir}/bin"
  cancel_log="${temp_dir}/cancel.log"
  loop_log="${temp_dir}/loop.log"
  output_file="${temp_dir}/out.txt"
  mkdir -p "${bin_dir}"

  cat > "${pr_json}" <<'EOF'
[{"number":700,"title":"ALL-700: Pending checks","url":"https://example.test/pr/700","headRefName":"all-700"}]
EOF
  cat > "${pr_view_json}" <<'EOF'
{"number":700,"title":"ALL-700: Pending checks","url":"https://example.test/pr/700","headRefName":"all-700","baseRefName":"main","headRefOid":"exact-head","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-07-12T10:00:00Z","statusCheckRollup":[{"__typename":"CheckRun","name":"Unit Tests","status":"IN_PROGRESS","conclusion":"","detailsUrl":"https://example.test/checks/unit"}]}
EOF
  printf 'Please fix the race.\n' > "${report_file}"
  cat > "${loop_stub}" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "${loop_log}"
cat <<'OUT'
CLAUDE_LOOP_STATUS=actionable_feedback
CLAUDE_LOOP_REPORT_FILE=${report_file}
CLAUDE_LOOP_ROUND=1
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
exit 10
EOF
  cat > "${classifier_stub}" <<'EOF'
#!/usr/bin/env bash
echo PR_FEEDBACK_CLASSIFIER_STATUS=actionable
exit 10
EOF
  cat > "${workpad_stub}" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  cat > "${state_stub}" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  cat > "${bin_dir}/gh" <<EOF
#!/usr/bin/env bash
if [[ "\$1 \$2" == "run list" ]]; then
  cat <<'JSON'
[
  {"databaseId":101,"workflowName":"Unit Tests","status":"in_progress","conclusion":"","headSha":"exact-head","headBranch":"all-700","event":"pull_request","url":"u1"},
  {"databaseId":102,"workflowName":"Agent PR Gate","status":"queued","conclusion":"","headSha":"exact-head","headBranch":"all-700","event":"pull_request","url":"u2"},
  {"databaseId":103,"workflowName":"CodeQL","status":"waiting","conclusion":"","headSha":"exact-head","headBranch":"all-700","event":"pull_request","url":"u3"},
  {"databaseId":104,"workflowName":"Unit Tests","status":"completed","conclusion":"success","headSha":"exact-head","headBranch":"all-700","event":"pull_request","url":"u4"},
  {"databaseId":105,"workflowName":"Unit Tests","status":"in_progress","conclusion":"","headSha":"other-head","headBranch":"all-700","event":"pull_request","url":"u5"},
  {"databaseId":106,"workflowName":"Deploy Production","status":"in_progress","conclusion":"","headSha":"exact-head","headBranch":"all-700","event":"pull_request","url":"u6"},
  {"databaseId":107,"workflowName":"Unit Tests","status":"in_progress","conclusion":"","headSha":"exact-head","headBranch":"main","event":"push","url":"u7"}
]
JSON
  exit 0
fi
if [[ "\$1 \$2" == "run cancel" ]]; then
  echo "\$3" >> "${cancel_log}"
  exit 0
fi
exit 99
EOF
  chmod +x "${loop_stub}" "${classifier_stub}" "${workpad_stub}" "${state_stub}" "${bin_dir}/gh"

  set +e
  PATH="${bin_dir}:${PATH}" \
    SYMPHONY_READY_FOR_PR_CLAUDE_LOOP_HELPER="${loop_stub}" \
    SYMPHONY_READY_FOR_PR_FEEDBACK_CLASSIFIER_HELPER="${classifier_stub}" \
    SYMPHONY_READY_FOR_PR_WORKPAD_HELPER="${workpad_stub}" \
    SYMPHONY_READY_FOR_PR_STATE_HELPER="${state_stub}" \
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-700 \
      --branch all-700 \
      --repo alliance-genome/agr_ai_curation \
      --wait-for-checks-seconds 30 \
      --check-poll-seconds 1 \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}" \
      > "${output_file}"
  rc=$?
  set -e
  output="$(cat "${output_file}")"

  [[ "${rc}" == "0" ]]
  assert_contains "READY_FOR_PR_CHECK_STATUS=claude_feedback" "${output}"
  assert_contains "READY_FOR_PR_CLAUDE_ACTION=bounced_to_in_progress" "${output}"
  assert_contains "READY_FOR_PR_CI_CANCELLED_COUNT=3" "${output}"
  assert_contains "--inspect-only" "$(cat "${loop_log}")"
  [[ "$(sort -n "${cancel_log}" | tr '\n' ' ')" == "101 102 103 " ]]
}

test_clean_pending_ci_feedback_is_cached_without_bounce() {
  local temp_dir pr_json pr_view_json loop_stub classifier_stub guard_stub
  local report_file classifier_log output_file output rc
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"
  loop_stub="${temp_dir}/claude-loop"
  classifier_stub="${temp_dir}/classifier"
  guard_stub="${temp_dir}/must-not-bounce"
  report_file="${temp_dir}/claude-report.md"
  classifier_log="${temp_dir}/classifier.log"
  output_file="${temp_dir}/out.txt"

  cat > "${pr_json}" <<'EOF'
[{"number":701,"title":"ALL-701: Pending CI","url":"https://example.test/pr/701","headRefName":"all-701"}]
EOF
  cat > "${pr_view_json}" <<'EOF'
{"number":701,"title":"ALL-701: Pending CI","url":"https://example.test/pr/701","headRefName":"all-701","baseRefName":"main","headRefOid":"head-701","mergeable":"MERGEABLE","mergeStateStatus":"CLEAN","createdAt":"2026-07-12T10:00:00Z","statusCheckRollup":[{"__typename":"CheckRun","name":"Unit Tests","status":"IN_PROGRESS","conclusion":"","detailsUrl":"https://example.test/checks/unit"}]}
EOF
  printf 'Approve/LGTM; merge once CI passes.\n' > "${report_file}"
  cat > "${loop_stub}" <<EOF
#!/usr/bin/env bash
cat <<'OUT'
CLAUDE_LOOP_STATUS=actionable_feedback
CLAUDE_LOOP_REPORT_FILE=${report_file}
CLAUDE_LOOP_ROUND=1
CLAUDE_LOOP_MAX_ROUNDS=5
OUT
exit 10
EOF
  cat > "${classifier_stub}" <<EOF
#!/usr/bin/env bash
echo called >> "${classifier_log}"
echo PR_FEEDBACK_CLASSIFIER_STATUS=clean
echo PR_FEEDBACK_CLASSIFIER_CLASSIFICATION=clean
echo PR_FEEDBACK_CLASSIFIER_REASON=Pure pending-CI gate language; no implementation work.
exit 0
EOF
  cat > "${guard_stub}" <<'EOF'
#!/usr/bin/env bash
echo "unexpected bounce: $*" >&2
exit 99
EOF
  chmod +x "${loop_stub}" "${classifier_stub}" "${guard_stub}"

  set +e
  SYMPHONY_READY_FOR_PR_CLAUDE_LOOP_HELPER="${loop_stub}" \
    SYMPHONY_READY_FOR_PR_FEEDBACK_CLASSIFIER_HELPER="${classifier_stub}" \
    SYMPHONY_READY_FOR_PR_WORKPAD_HELPER="${guard_stub}" \
    SYMPHONY_READY_FOR_PR_STATE_HELPER="${guard_stub}" \
    bash "${SCRIPT_PATH}" \
      --delivery-mode pr \
      --issue-identifier ALL-701 \
      --branch all-701 \
      --repo alliance-genome/agr_ai_curation \
      --wait-for-checks-seconds 2 \
      --check-poll-seconds 1 \
      --pr-json-file "${pr_json}" \
      --pr-view-json-file "${pr_view_json}" \
      > "${output_file}"
  rc=$?
  set -e
  output="$(cat "${output_file}")"

  [[ "${rc}" == "11" ]]
  assert_contains "READY_FOR_PR_EARLY_CLAUDE_ACTION=clean_review_checks_continue" "${output}"
  assert_contains "READY_FOR_PR_EARLY_CLAUDE_CLASSIFIER_CACHE=miss" "${output}"
  assert_contains "READY_FOR_PR_EARLY_CLAUDE_CLASSIFIER_CACHE=hit" "${output}"
  assert_contains "READY_FOR_PR_CHECK_STATUS=pending" "${output}"
  [[ "$(wc -l < "${classifier_log}")" == "1" ]]
}

test_no_pr_skips_lane
test_existing_pr_is_reported
test_conflicted_pr_routes_back_to_in_progress
test_missing_pr_reports_nonzero
test_base_branch_is_rejected
test_dry_run_create_reports_title
test_repo_mismatch_is_rejected_when_origin_is_known
test_dry_run_create_infers_title
test_create_pr_uses_plain_cli_output_and_view_json
test_claude_detected_auto_bounces_to_in_progress
test_claude_wait_zero_still_scans_existing_feedback
test_clean_claude_review_does_not_auto_bounce
test_default_classifier_uses_source_root_fallback
test_approval_with_actionable_suggestions_still_auto_bounces
test_classifier_error_is_conservative_and_auto_bounces
test_claude_pending_after_clean_checks_stops_before_human_review
test_failed_github_check_auto_bounces_to_in_progress
test_claude_maxed_out_without_report_does_not_abort
test_actionable_claude_feedback_stops_pending_checks_and_cancels_exact_head
test_clean_pending_ci_feedback_is_cached_without_bounce

echo "symphony_ready_for_pr tests passed"
