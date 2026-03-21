#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_in_review.sh"

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

# ── Test: Basic brief generation from fixture ────────────────────────

test_basic_brief_generation() {
  local temp_dir linear_json output_file rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  output_file="${temp_dir}/output.txt"

  cat > "${linear_json}" <<'EOF'
{
  "data": {
    "issue": {
      "identifier": "ALL-99",
      "title": "Build Curation Prep Agent",
      "description": "## Scope\n\n- [ ] Build the agent\n- [ ] Add tests\n\n## Out of Scope\n\nDo not touch the supervisor.",
      "url": "https://linear.app/test/issue/ALL-99",
      "state": {"name": "In Review"},
      "labels": {"nodes": [{"name": "parallel:wave-4"}]},
      "comments": {
        "nodes": [
          {
            "createdAt": "2026-03-21T14:00:00Z",
            "updatedAt": "2026-03-21T14:00:00Z",
            "body": "### Workpad\n- Started implementation\n- Tests passing",
            "user": {"name": "Christopher Tabone"}
          },
          {
            "createdAt": "2026-03-21T15:00:00Z",
            "updatedAt": "2026-03-21T15:00:00Z",
            "body": "Can you also handle the edge case where adapter_key is missing?",
            "user": {"name": "Christopher Tabone"}
          }
        ]
      }
    }
  }
}
EOF

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-99 \
    --linear-json-file "${linear_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_REVIEW_STATUS=ok" "${output}"
  assert_contains "IN_REVIEW_ISSUE=ALL-99" "${output}"
  assert_contains "IN_REVIEW_COMMENT_COUNT=2" "${output}"
  assert_contains "IN_REVIEW_BRIEF_FILE=" "${output}"

  # Extract and check the brief file
  local brief_file brief_content
  brief_file="$(echo "${output}" | grep 'IN_REVIEW_BRIEF_FILE=' | cut -d= -f2)"
  brief_content="$(cat "${brief_file}")"

  assert_contains "# Review Brief: ALL-99" "${brief_content}"
  assert_contains "Build Curation Prep Agent" "${brief_content}"
  assert_contains "## 1. Issue Description" "${brief_content}"
  assert_contains "Build the agent" "${brief_content}"
  assert_contains "## 2. Issue Comments (2 total)" "${brief_content}"
  assert_contains "Comment 1" "${brief_content}"
  assert_contains "Comment 2" "${brief_content}"
  assert_contains "Workpad" "${brief_content}"
  assert_contains "adapter_key is missing" "${brief_content}"
  assert_contains "## 4. Review Instructions" "${brief_content}"

  rm -f "${brief_file}"
  echo "  PASS: test_basic_brief_generation"
  rm -rf "${temp_dir}"
}

# ── Test: No comments ────────────────────────────────────────────────

test_no_comments() {
  local temp_dir linear_json output_file rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"

  cat > "${linear_json}" <<'EOF'
{
  "data": {
    "issue": {
      "identifier": "ALL-50",
      "title": "Simple fix",
      "description": "Fix the thing.",
      "url": "https://linear.app/test/issue/ALL-50",
      "state": {"name": "In Review"},
      "labels": {"nodes": []},
      "comments": {"nodes": []}
    }
  }
}
EOF

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-50 \
    --linear-json-file "${linear_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_REVIEW_COMMENT_COUNT=0" "${output}"

  local brief_file brief_content
  brief_file="$(echo "${output}" | grep 'IN_REVIEW_BRIEF_FILE=' | cut -d= -f2)"
  brief_content="$(cat "${brief_file}")"

  assert_contains "0 total" "${brief_content}"
  assert_contains "No comments on this issue" "${brief_content}"

  rm -f "${brief_file}"
  echo "  PASS: test_no_comments"
  rm -rf "${temp_dir}"
}

# ── Test: With open PR and Claude review ─────────────────────────────

test_with_pr_and_claude_review() {
  local temp_dir linear_json pr_json pr_comments_json rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  pr_json="${temp_dir}/prs.json"
  pr_comments_json="${temp_dir}/pr-comments.json"

  cat > "${linear_json}" <<'EOF'
{
  "data": {
    "issue": {
      "identifier": "ALL-101",
      "title": "Add supervisor-triggered invocation",
      "description": "Wire the supervisor to call curation prep.",
      "url": "https://linear.app/test/issue/ALL-101",
      "state": {"name": "In Review"},
      "labels": {"nodes": []},
      "comments": {"nodes": []}
    }
  }
}
EOF

  cat > "${pr_json}" <<'EOF'
[{"number":112,"title":"ALL-101: Add supervisor handoff","url":"https://github.com/test/repo/pull/112","headRefName":"all-101-branch"}]
EOF

  cat > "${pr_comments_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:00Z",
      "updatedAt": "2026-03-21T14:04:00Z",
      "body": "## PR Review\n\nOverall: Approve\n\n1. Consider using a constant for the tool name.\n2. The docstring uses 'gene' as an example — use a generic placeholder."
    },
    {
      "author": {"login": "developer"},
      "createdAt": "2026-03-21T14:10:00Z",
      "updatedAt": "2026-03-21T14:10:00Z",
      "body": "Thanks, will fix."
    }
  ],
  "reviews": []
}
EOF

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-101 \
    --branch all-101-branch \
    --linear-json-file "${linear_json}" \
    --pr-json-file "${pr_json}" \
    --pr-comments-file "${pr_comments_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_REVIEW_PR_NUMBER=112" "${output}"
  assert_contains "IN_REVIEW_PR_CLAUDE_REVIEW=present" "${output}"

  local brief_file brief_content
  brief_file="$(echo "${output}" | grep 'IN_REVIEW_BRIEF_FILE=' | cut -d= -f2)"
  brief_content="$(cat "${brief_file}")"

  assert_contains "PR #112" "${brief_content}"
  assert_contains "Latest claude Review Comment" "${brief_content}"
  assert_contains "generic placeholder" "${brief_content}"

  rm -f "${brief_file}"
  echo "  PASS: test_with_pr_and_claude_review"
  rm -rf "${temp_dir}"
}

# ── Test: With open PR but no Claude review ──────────────────────────

test_pr_no_claude_review() {
  local temp_dir linear_json pr_json pr_comments_json rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  pr_json="${temp_dir}/prs.json"
  pr_comments_json="${temp_dir}/pr-comments.json"

  cat > "${linear_json}" <<'EOF'
{
  "data": {
    "issue": {
      "identifier": "ALL-50",
      "title": "Quick fix",
      "description": "Fix it.",
      "url": "https://linear.app/test/issue/ALL-50",
      "state": {"name": "In Review"},
      "labels": {"nodes": []},
      "comments": {"nodes": []}
    }
  }
}
EOF

  cat > "${pr_json}" <<'EOF'
[{"number":55,"title":"ALL-50: Quick fix","url":"https://github.com/test/repo/pull/55","headRefName":"all-50-branch"}]
EOF

  cat > "${pr_comments_json}" <<'EOF'
{"comments": [], "reviews": []}
EOF

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-50 \
    --branch all-50-branch \
    --linear-json-file "${linear_json}" \
    --pr-json-file "${pr_json}" \
    --pr-comments-file "${pr_comments_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_REVIEW_PR_NUMBER=55" "${output}"
  assert_contains "IN_REVIEW_PR_CLAUDE_REVIEW=absent" "${output}"

  local brief_file brief_content
  brief_file="$(echo "${output}" | grep 'IN_REVIEW_BRIEF_FILE=' | cut -d= -f2)"
  brief_content="$(cat "${brief_file}")"

  assert_contains "No claude review comments found" "${brief_content}"

  rm -f "${brief_file}"
  echo "  PASS: test_pr_no_claude_review"
  rm -rf "${temp_dir}"
}

# ── Test: Missing issue identifier ───────────────────────────────────

test_missing_identifier() {
  local rc
  set +e
  bash "${SCRIPT_PATH}" 2>/dev/null
  rc=$?
  set -e

  assert_exit_code "2" "${rc}"
  echo "  PASS: test_missing_identifier"
}

# ── Test: Output file option ─────────────────────────────────────────

test_output_file_option() {
  local temp_dir linear_json output_path rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"
  output_path="${temp_dir}/my-brief.md"

  cat > "${linear_json}" <<'EOF'
{
  "data": {
    "issue": {
      "identifier": "ALL-10",
      "title": "Test output file",
      "description": "Testing.",
      "url": "https://linear.app/test/issue/ALL-10",
      "state": {"name": "In Review"},
      "labels": {"nodes": []},
      "comments": {"nodes": []}
    }
  }
}
EOF

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-10 \
    --linear-json-file "${linear_json}" \
    --output-file "${output_path}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "0" "${rc}"
  assert_contains "IN_REVIEW_BRIEF_FILE=${output_path}" "${output}"

  # Verify the file exists and has content
  if [[ ! -f "${output_path}" ]]; then
    echo "FAIL: Output file not created at ${output_path}" >&2
    exit 1
  fi

  local content
  content="$(cat "${output_path}")"
  assert_contains "# Review Brief: ALL-10" "${content}"

  echo "  PASS: test_output_file_option"
  rm -rf "${temp_dir}"
}

# ── Test: Invalid Linear response returns error ──────────────────────

test_invalid_linear_response() {
  local temp_dir linear_json rc output
  temp_dir="$(mktemp -d)"
  linear_json="${temp_dir}/linear.json"

  cat > "${linear_json}" <<'EOF'
{"data":{"issue":null}}
EOF

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-999 \
    --linear-json-file "${linear_json}" 2>&1)"
  rc=$?
  set -e

  assert_exit_code "2" "${rc}"
  assert_contains "IN_REVIEW_STATUS=error" "${output}"
  assert_contains "Could not fetch Linear issue" "${output}"

  echo "  PASS: test_invalid_linear_response"
  rm -rf "${temp_dir}"
}

# ── Run all tests ────────────────────────────────────────────────────

echo "Running symphony_in_review tests..."
test_basic_brief_generation
test_no_comments
test_with_pr_and_claude_review
test_pr_no_claude_review
test_missing_identifier
test_output_file_option
test_invalid_linear_response

echo "symphony_in_review tests passed (7/7)"
