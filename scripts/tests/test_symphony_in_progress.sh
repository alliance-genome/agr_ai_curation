#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_in_progress.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "FAIL: Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

assert_not_contains() {
  local unexpected="$1"
  local actual="$2"
  if [[ "${actual}" == *"${unexpected}"* ]]; then
    echo "FAIL: Expected output NOT to contain '${unexpected}'" >&2
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

# Helper to create minimal Linear fixtures
make_linear_json() {
  local identifier="$1"
  local title="$2"
  local comments_json="${3:-[]}"

  cat <<EOF
{
  "data": {
    "issue": {
      "identifier": "${identifier}",
      "title": "${title}",
      "description": "## Scope\\n\\n- [ ] Do the thing\\n\\n## Out of Scope\\n\\nDo not touch other things.",
      "url": "https://linear.app/test/issue/${identifier}",
      "state": {"name": "In Progress"},
      "labels": {"nodes": []},
      "comments": {"nodes": ${comments_json}}
    }
  }
}
EOF
}

make_history_json() {
  local transitions_json="$1"
  cat <<EOF
{
  "data": {
    "issue": {
      "history": {
        "nodes": ${transitions_json}
      }
    }
  }
}
EOF
}

# ── Test: First implementation from Todo ──────────────────────────────

test_first_implementation_from_todo() {
  local temp_dir linear_json history_json rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  history_json="${temp_dir}/history.json"

  make_linear_json "ALL-50" "Build the widget" > "${linear_json}"
  make_history_json '[
    {"createdAt": "2026-03-21T14:00:00Z", "fromState": {"name": "Todo"}, "toState": {"name": "In Progress"}}
  ]' > "${history_json}"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-50 \
    --linear-json-file "${linear_json}" \
    --history-json-file "${history_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_PROGRESS_STATUS=ok" "${output}"
  assert_contains "IN_PROGRESS_ENTRY_FROM=Todo" "${output}"
  assert_contains "IN_PROGRESS_PASS_NUMBER=1" "${output}"

  local brief_file brief_content
  brief_file="$(echo "${output}" | grep 'IN_PROGRESS_BRIEF_FILE=' | cut -d= -f2)"
  brief_content="$(cat "${brief_file}")"

  assert_contains "First implementation pass" "${brief_content}"
  assert_contains "pass **#1**" "${brief_content}"
  assert_contains "## 2. Issue Description" "${brief_content}"
  assert_contains "Implement" "${brief_content}"

  rm -f "${brief_file}"
  echo "  PASS: test_first_implementation_from_todo"
  rm -rf "${temp_dir}"
}

# ── Test: Bounce from In Review ──────────────────────────────────────

test_bounce_from_in_review() {
  local temp_dir linear_json history_json rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  history_json="${temp_dir}/history.json"

  make_linear_json "ALL-60" "Fix the thing" > "${linear_json}"
  make_history_json '[
    {"createdAt": "2026-03-21T14:00:00Z", "fromState": {"name": "Todo"}, "toState": {"name": "In Progress"}},
    {"createdAt": "2026-03-21T15:00:00Z", "fromState": {"name": "In Progress"}, "toState": {"name": "Needs Review"}},
    {"createdAt": "2026-03-21T15:05:00Z", "fromState": {"name": "Needs Review"}, "toState": {"name": "In Review"}},
    {"createdAt": "2026-03-21T15:30:00Z", "fromState": {"name": "In Review"}, "toState": {"name": "In Progress"}}
  ]' > "${history_json}"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-60 \
    --linear-json-file "${linear_json}" \
    --history-json-file "${history_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_PROGRESS_ENTRY_FROM=In Review" "${output}"
  assert_contains "IN_PROGRESS_PASS_NUMBER=2" "${output}"

  local brief_file brief_content
  brief_file="$(echo "${output}" | grep 'IN_PROGRESS_BRIEF_FILE=' | cut -d= -f2)"
  brief_content="$(cat "${brief_file}")"

  assert_contains "Bounced from reviewer" "${brief_content}"
  assert_contains "blocking" "${brief_content}"
  assert_contains "pass **#2**" "${brief_content}"
  assert_contains "Fix the blocking reviewer findings" "${brief_content}"

  rm -f "${brief_file}"
  echo "  PASS: test_bounce_from_in_review"
  rm -rf "${temp_dir}"
}

# ── Test: Bounce from Ready for PR with failing checks ───────────────

test_bounce_from_ready_for_pr_with_failures() {
  local temp_dir linear_json history_json pr_json pr_view_json rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  history_json="${temp_dir}/history.json"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"

  make_linear_json "ALL-70" "Add the endpoint" > "${linear_json}"
  make_history_json '[
    {"createdAt": "2026-03-21T14:00:00Z", "fromState": {"name": "Todo"}, "toState": {"name": "In Progress"}},
    {"createdAt": "2026-03-21T16:00:00Z", "fromState": {"name": "Ready for PR"}, "toState": {"name": "In Progress"}}
  ]' > "${history_json}"

  echo '[{"number":88,"title":"ALL-70: Add endpoint","url":"https://github.com/test/repo/pull/88","headRefName":"all-70-branch"}]' > "${pr_json}"

  cat > "${pr_view_json}" <<'EOF'
{
  "headRefOid": "abc123",
  "statusCheckRollup": [
    {"name": "Backend Unit Tests", "status": "COMPLETED", "conclusion": "FAILURE"},
    {"name": "Frontend Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"name": "Agent PR Gate", "status": "COMPLETED", "conclusion": "FAILURE"}
  ],
  "comments": [],
  "reviews": []
}
EOF

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-70 \
    --branch all-70-branch \
    --linear-json-file "${linear_json}" \
    --history-json-file "${history_json}" \
    --pr-json-file "${pr_json}" \
    --pr-view-json-file "${pr_view_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_PROGRESS_ENTRY_FROM=Ready for PR" "${output}"
  assert_contains "IN_PROGRESS_FAILING_CHECKS=" "${output}"
  assert_contains "Backend Unit Tests" "${output}"

  local brief_file brief_content
  brief_file="$(echo "${output}" | grep 'IN_PROGRESS_BRIEF_FILE=' | cut -d= -f2)"
  brief_content="$(cat "${brief_file}")"

  assert_contains "Bounced from Ready for PR" "${brief_content}"
  assert_contains "CI checks failed" "${brief_content}"
  assert_contains "Backend Unit Tests" "${brief_content}"
  assert_contains "Failing Checks" "${brief_content}"

  rm -f "${brief_file}"
  echo "  PASS: test_bounce_from_ready_for_pr_with_failures"
  rm -rf "${temp_dir}"
}

# ── Test: Bounce from Ready for PR with Claude review ────────────────

test_bounce_from_ready_for_pr_with_claude_review() {
  local temp_dir linear_json history_json pr_json pr_view_json rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  history_json="${temp_dir}/history.json"
  pr_json="${temp_dir}/prs.json"
  pr_view_json="${temp_dir}/pr-view.json"

  make_linear_json "ALL-71" "Refactor thing" > "${linear_json}"
  make_history_json '[
    {"createdAt": "2026-03-21T14:00:00Z", "fromState": {"name": "Todo"}, "toState": {"name": "In Progress"}},
    {"createdAt": "2026-03-21T16:00:00Z", "fromState": {"name": "Ready for PR"}, "toState": {"name": "In Progress"}}
  ]' > "${history_json}"

  echo '[{"number":89,"title":"ALL-71: Refactor","url":"https://github.com/test/repo/pull/89","headRefName":"all-71-branch"}]' > "${pr_json}"

  cat > "${pr_view_json}" <<'EOF'
{
  "headRefOid": "def456",
  "statusCheckRollup": [
    {"name": "Backend Unit Tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"name": "Agent PR Gate", "status": "COMPLETED", "conclusion": "SUCCESS"}
  ],
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T15:30:00Z",
      "updatedAt": "2026-03-21T15:30:00Z",
      "body": "## PR Review\n\nPlease fix the error handling in bootstrap_service.py."
    }
  ],
  "reviews": []
}
EOF

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-71 \
    --branch all-71-branch \
    --linear-json-file "${linear_json}" \
    --history-json-file "${history_json}" \
    --pr-json-file "${pr_json}" \
    --pr-view-json-file "${pr_view_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_PROGRESS_ENTRY_FROM=Ready for PR" "${output}"
  assert_contains "IN_PROGRESS_PR_CLAUDE_REVIEW=present" "${output}"
  assert_not_contains "IN_PROGRESS_FAILING_CHECKS" "${output}"

  local brief_file brief_content
  brief_file="$(echo "${output}" | grep 'IN_PROGRESS_BRIEF_FILE=' | cut -d= -f2)"
  brief_content="$(cat "${brief_file}")"

  assert_contains "Claude left review feedback" "${brief_content}"
  assert_contains "fix the error handling" "${brief_content}"

  rm -f "${brief_file}"
  echo "  PASS: test_bounce_from_ready_for_pr_with_claude_review"
  rm -rf "${temp_dir}"
}

# ── Test: Bounce from Human Review ───────────────────────────────────

test_bounce_from_human_review() {
  local temp_dir linear_json history_json rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  history_json="${temp_dir}/history.json"

  make_linear_json "ALL-80" "Polish the UI" '[
    {"createdAt":"2026-03-21T16:00:00Z","body":"The button placement is wrong, move it to the header.","user":{"name":"Christopher Tabone"}}
  ]' > "${linear_json}"

  make_history_json '[
    {"createdAt": "2026-03-21T14:00:00Z", "fromState": {"name": "Todo"}, "toState": {"name": "In Progress"}},
    {"createdAt": "2026-03-21T17:00:00Z", "fromState": {"name": "Human Review"}, "toState": {"name": "In Progress"}}
  ]' > "${history_json}"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-80 \
    --linear-json-file "${linear_json}" \
    --history-json-file "${history_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_PROGRESS_ENTRY_FROM=Human Review" "${output}"
  assert_contains "IN_PROGRESS_PASS_NUMBER=2" "${output}"

  local brief_file brief_content
  brief_file="$(echo "${output}" | grep 'IN_PROGRESS_BRIEF_FILE=' | cut -d= -f2)"
  brief_content="$(cat "${brief_file}")"

  assert_contains "Sent back from Human Review" "${brief_content}"
  assert_contains "Chris reviewed this" "${brief_content}"
  assert_contains "button placement" "${brief_content}"
  assert_contains "Address Chris" "${brief_content}"

  rm -f "${brief_file}"
  echo "  PASS: test_bounce_from_human_review"
  rm -rf "${temp_dir}"
}

# ── Test: Missing identifier ─────────────────────────────────────────

test_missing_identifier() {
  local rc
  set +e
  bash "${SCRIPT_PATH}" 2>/dev/null
  rc=$?
  set -e

  assert_exit_code "2" "${rc}"
  echo "  PASS: test_missing_identifier"
}

# ── Test: Invalid Linear response ────────────────────────────────────

test_invalid_linear_response() {
  local temp_dir linear_json history_json rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  history_json="${temp_dir}/history.json"

  echo '{"data":{"issue":null}}' > "${linear_json}"
  make_history_json '[]' > "${history_json}"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-999 \
    --linear-json-file "${linear_json}" \
    --history-json-file "${history_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "2" "${rc}"
  assert_contains "IN_PROGRESS_STATUS=error" "${output}"
  assert_contains "Could not fetch Linear issue" "${output}"

  echo "  PASS: test_invalid_linear_response"
  rm -rf "${temp_dir}"
}

# ── Test: Multiple bounces counts correctly ──────────────────────────

test_multiple_bounces_counts_correctly() {
  local temp_dir linear_json history_json rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  history_json="${temp_dir}/history.json"

  make_linear_json "ALL-90" "Complex fix" > "${linear_json}"
  make_history_json '[
    {"createdAt": "2026-03-21T10:00:00Z", "fromState": {"name": "Todo"}, "toState": {"name": "In Progress"}},
    {"createdAt": "2026-03-21T12:00:00Z", "fromState": {"name": "In Review"}, "toState": {"name": "In Progress"}},
    {"createdAt": "2026-03-21T14:00:00Z", "fromState": {"name": "Ready for PR"}, "toState": {"name": "In Progress"}},
    {"createdAt": "2026-03-21T16:00:00Z", "fromState": {"name": "Ready for PR"}, "toState": {"name": "In Progress"}}
  ]' > "${history_json}"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-90 \
    --linear-json-file "${linear_json}" \
    --history-json-file "${history_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_PROGRESS_ENTRY_FROM=Ready for PR" "${output}"
  assert_contains "IN_PROGRESS_PASS_NUMBER=4" "${output}"

  local brief_file brief_content
  brief_file="$(echo "${output}" | grep 'IN_PROGRESS_BRIEF_FILE=' | cut -d= -f2)"
  brief_content="$(cat "${brief_file}")"

  assert_contains "pass **#4**" "${brief_content}"

  rm -f "${brief_file}"
  echo "  PASS: test_multiple_bounces_counts_correctly"
  rm -rf "${temp_dir}"
}

# ── Test: Output file option ──────────────────────────────────────────

test_output_file_option() {
  local temp_dir linear_json history_json output_path rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  history_json="${temp_dir}/history.json"
  output_path="${temp_dir}/my-brief.md"

  make_linear_json "ALL-10" "Test output file" > "${linear_json}"
  make_history_json '[
    {"createdAt": "2026-03-21T14:00:00Z", "fromState": {"name": "Todo"}, "toState": {"name": "In Progress"}}
  ]' > "${history_json}"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-10 \
    --linear-json-file "${linear_json}" \
    --history-json-file "${history_json}" \
    --output-file "${output_path}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_PROGRESS_BRIEF_FILE=${output_path}" "${output}"

  if [[ ! -f "${output_path}" ]]; then
    echo "FAIL: Output file not created at ${output_path}" >&2
    exit 1
  fi

  local content
  content="$(cat "${output_path}")"
  assert_contains "# In Progress Brief: ALL-10" "${content}"

  echo "  PASS: test_output_file_option"
  rm -rf "${temp_dir}"
}

# ── Test: Empty history defaults to pass #1 ──────────────────────────

test_empty_history_defaults_to_pass_one() {
  local temp_dir linear_json history_json rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  history_json="${temp_dir}/history.json"

  make_linear_json "ALL-11" "Created directly in progress" > "${linear_json}"
  make_history_json '[]' > "${history_json}"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-11 \
    --linear-json-file "${linear_json}" \
    --history-json-file "${history_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_PROGRESS_PASS_NUMBER=1" "${output}"
  assert_contains "IN_PROGRESS_ENTRY_FROM=unknown" "${output}"

  local brief_file brief_content
  brief_file="$(echo "${output}" | grep 'IN_PROGRESS_BRIEF_FILE=' | cut -d= -f2)"
  brief_content="$(cat "${brief_file}")"

  assert_contains "pass **#1**" "${brief_content}"
  assert_not_contains "pass **#0**" "${brief_content}"

  rm -f "${brief_file}"
  echo "  PASS: test_empty_history_defaults_to_pass_one"
  rm -rf "${temp_dir}"
}

# ── Run all tests ────────────────────────────────────────────────────

echo "Running symphony_in_progress tests..."
test_first_implementation_from_todo
test_bounce_from_in_review
test_bounce_from_ready_for_pr_with_failures
test_bounce_from_ready_for_pr_with_claude_review
test_bounce_from_human_review
test_missing_identifier
test_invalid_linear_response
test_multiple_bounces_counts_correctly
test_output_file_option
test_empty_history_defaults_to_pass_one

echo "symphony_in_progress tests passed (10/10)"
