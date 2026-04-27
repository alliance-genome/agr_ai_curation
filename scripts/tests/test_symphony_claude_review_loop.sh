#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_claude_review_loop.sh"

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

# Helper that runs the loop script with fixture files and --wait-seconds 0
run_loop() {
  local top_json_file="$1"
  local inline_json_file="$2"
  local since="$3"
  local output_file="$4"
  shift 4
  # Remaining args passed through (e.g. --max-rounds, --head-sha)

  set +e
  bash "${SCRIPT_PATH}" \
    --repo alliance-genome/agr_ai_curation \
    --pr 99 \
    --since "${since}" \
    --wait-seconds 0 \
    --top-json-file "${top_json_file}" \
    --inline-json-file "${inline_json_file}" \
    "$@" \
    >"${output_file}"
  local rc=$?
  set -e

  printf '%s' "${rc}"
}

# ── Test: Initial review detected ────────────────────────────────────

test_initial_review_detected() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  # Claude posted a comment 3 minutes after the PR was created
  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:04Z",
      "updatedAt": null,
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "## PR Review\n\nLooks good with minor issues."
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "abc123",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:47Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}")"
  output="$(cat "${output_file}")"

  assert_exit_code "10" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=detected" "${output}"
  assert_contains "CLAUDE_LOOP_ROUND=1" "${output}"
  assert_contains "CLAUDE_LOOP_REPORT_FILE=" "${output}"

  # Verify report file was created and has content
  local report_file
  report_file="$(echo "${output}" | grep CLAUDE_LOOP_REPORT_FILE= | cut -d= -f2)"
  if [[ -n "${report_file}" && -f "${report_file}" ]]; then
    local report_content
    report_content="$(cat "${report_file}")"
    assert_contains "PR Review" "${report_content}"
    assert_contains "1 comment(s) found" "${report_content}"
    rm -f "${report_file}"
  else
    echo "FAIL: Report file not created" >&2
    exit 1
  fi

  echo "  PASS: test_initial_review_detected"
  rm -rf "${temp_dir}"
}

# ── Test: No feedback — quiet ────────────────────────────────────────

test_no_feedback_quiet() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  cat > "${top_json}" <<'EOF'
{
  "comments": [],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "abc123",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:47Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}")"
  output="$(cat "${output_file}")"

  assert_exit_code "0" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=quiet" "${output}"

  echo "  PASS: test_no_feedback_quiet"
  rm -rf "${temp_dir}"
}

# ── Test: In-progress workflow keeps initial review pending ──────────

test_running_workflow_keeps_initial_review_pending() {
  local temp_dir top_json inline_json workflow_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  workflow_json="${temp_dir}/workflow-runs.json"
  output_file="${temp_dir}/output.txt"

  cat > "${top_json}" <<'EOF'
{
  "comments": [],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/500",
  "title": "ALL-500: Pending Claude run",
  "headRefOid": "abc500",
  "commits": [
    {"oid": "abc500", "committedDate": "2026-04-27T16:00:00Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  cat > "${workflow_json}" <<'EOF'
[
  {
    "databaseId": 25006064832,
    "workflowName": "Claude Code Review",
    "displayTitle": "ALL-500: Pending Claude run",
    "status": "in_progress",
    "conclusion": "",
    "event": "issue_comment",
    "createdAt": "2026-04-27T16:07:43Z",
    "updatedAt": "2026-04-27T16:07:49Z",
    "headBranch": "main",
    "headSha": "21a9e7d74d0a48d150d4549dbca546ce1d7edbf2",
    "url": "https://github.com/test/repo/actions/runs/25006064832"
  }
]
EOF

  set +e
  bash "${SCRIPT_PATH}" \
    --repo alliance-genome/agr_ai_curation \
    --pr 500 \
    --since "2026-04-27T16:00:00Z" \
    --wait-seconds 1 \
    --poll-seconds 1 \
    --top-json-file "${top_json}" \
    --inline-json-file "${inline_json}" \
    --workflow-runs-json-file "${workflow_json}" \
    > "${output_file}"
  rc=$?
  set -e
  output="$(cat "${output_file}")"

  assert_exit_code "0" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=pending" "${output}"
  assert_contains "CLAUDE_LOOP_WORKFLOW_STATUS=pending" "${output}"
  assert_contains "CLAUDE_LOOP_WORKFLOW_RUN_ID=25006064832" "${output}"

  echo "  PASS: test_running_workflow_keeps_initial_review_pending"
  rm -rf "${temp_dir}"
}

# ── Test: Old feedback before since — quiet ──────────────────────────

test_old_feedback_before_since_is_quiet() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  # Claude's comment is BEFORE the since timestamp
  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T13:00:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "Old review"
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "abc123",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:47Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}")"
  output="$(cat "${output_file}")"

  assert_exit_code "0" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=quiet" "${output}"

  echo "  PASS: test_old_feedback_before_since_is_quiet"
  rm -rf "${temp_dir}"
}

# ── Test: Head newer than feedback, no markers — advances round ──────
#
# When the head is newer than the initial Claude review and no re-review
# markers exist, the script should advance to request_and_wait (posting a
# marker so the round counter progresses).  The old behavior was to always
# report_current, which trapped agents in an infinite bounce loop when the
# review was non-blocking (see ALL-102 incident).

test_head_newer_no_markers_advances_round() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  # Claude reviewed at 14:04, then agent pushed a new commit at 14:10.
  # No re-review markers exist.  The script should post a re-review
  # marker (request_and_wait) so the round counter advances, rather than
  # endlessly re-reporting the same initial review.
  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:04Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "## PR Review\n\nPlease fix X."
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "def456",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:47Z"},
    {"oid": "def456", "committedDate": "2026-03-21T14:10:19Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  # --dry-run prevents the gh pr comment call; wait-seconds=0 means the
  # poll returns immediately, but the outstanding re-review is still pending.
  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}" --dry-run)"
  output="$(cat "${output_file}")"

  assert_exit_code "0" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=pending" "${output}"
  assert_contains "CLAUDE_LOOP_ACTION=request_and_wait" "${output}"

  echo "  PASS: test_head_newer_no_markers_advances_round"
  rm -rf "${temp_dir}"
}

test_head_newer_with_markers_triggers_rereview() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  # Round 1 done, re-review marker exists for commit2, agent pushed commit3 (newer)
  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "## Round 1 review"
    },
    {
      "author": {"login": "symphony-bot"},
      "createdAt": "2026-03-21T14:20:00Z",
      "body": "<!-- symphony-claude-rereview:commit2 -->\n@claude Please review."
    },
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:25:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-3",
      "body": "## Round 2 review"
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "commit3",
  "commits": [
    {"oid": "commit1", "committedDate": "2026-03-21T14:01:00Z"},
    {"oid": "commit2", "committedDate": "2026-03-21T14:15:00Z"},
    {"oid": "commit3", "committedDate": "2026-03-21T14:30:00Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  # dry-run + wait-seconds=0: should try request_and_wait and report pending.
  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}" --dry-run)"
  output="$(cat "${output_file}")"

  assert_exit_code "0" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=pending" "${output}"
  assert_contains "CLAUDE_LOOP_ACTION=request_and_wait" "${output}"

  echo "  PASS: test_head_newer_with_markers_triggers_rereview"
  rm -rf "${temp_dir}"
}

# ── Test: Feedback current (head not newer) — report_current ─────────

test_feedback_current_reports_immediately() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  # Claude reviewed at 14:04, head commit is from 14:01 (before review)
  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:04Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "## PR Review\n\nPlease fix Y."
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "abc123",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:47Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}")"
  output="$(cat "${output_file}")"

  assert_exit_code "10" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=detected" "${output}"
  assert_contains "CLAUDE_LOOP_REPORT_FILE=" "${output}"

  # Clean up report file
  local report_file
  report_file="$(echo "${output}" | grep CLAUDE_LOOP_REPORT_FILE= | cut -d= -f2)"
  [[ -n "${report_file}" ]] && rm -f "${report_file}"

  echo "  PASS: test_feedback_current_reports_immediately"
  rm -rf "${temp_dir}"
}

# ── Test: Maxed out after max rounds ─────────────────────────────────

test_maxed_out_after_limit() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  # Initial Claude review + 2 re-review request/response cycles = 3 rounds
  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "## Round 1 review"
    },
    {
      "author": {"login": "symphony-bot"},
      "createdAt": "2026-03-21T14:20:00Z",
      "body": "<!-- symphony-claude-rereview:commit2 -->\n@claude Please review."
    },
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:25:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-3",
      "body": "## Round 2 review"
    },
    {
      "author": {"login": "symphony-bot"},
      "createdAt": "2026-03-21T14:40:00Z",
      "body": "<!-- symphony-claude-rereview:commit3 -->\n@claude Please review."
    },
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:45:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-5",
      "body": "## Round 3 review"
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "commit4",
  "commits": [
    {"oid": "commit4", "committedDate": "2026-03-21T14:50:00Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}" --max-rounds 3)"
  output="$(cat "${output_file}")"

  assert_exit_code "0" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=maxed_out" "${output}"
  assert_contains "CLAUDE_LOOP_ROUND=3" "${output}"
  assert_contains "CLAUDE_LOOP_MAX_ROUNDS=3" "${output}"

  echo "  PASS: test_maxed_out_after_limit"
  rm -rf "${temp_dir}"
}

# ── Test: Under limit continues ──────────────────────────────────────

test_under_limit_head_newer_advances() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  # Only 1 round done (initial), max is 3, no re-review markers.
  # Head is newer than feedback → request_and_wait (advances the round).
  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "## Round 1 review"
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "newcommit",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:00Z"},
    {"oid": "newcommit", "committedDate": "2026-03-21T14:15:00Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}" --max-rounds 3 --dry-run)"
  output="$(cat "${output_file}")"

  assert_exit_code "0" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=pending" "${output}"
  assert_contains "CLAUDE_LOOP_ACTION=request_and_wait" "${output}"
  assert_not_contains "maxed_out" "${output}"

  echo "  PASS: test_under_limit_head_newer_advances"
  rm -rf "${temp_dir}"
}

# ── Test: Inline comment detected ────────────────────────────────────

test_inline_comment_detected() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  cat > "${top_json}" <<'EOF'
{
  "comments": [],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "abc123",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:47Z"}
  ]
}
EOF
  cat > "${inline_json}" <<'EOF'
[
  {
    "user": {"login": "claude"},
    "created_at": "2026-03-21T14:09:00Z",
    "updated_at": "2026-03-21T14:09:00Z",
    "html_url": "https://github.com/test/repo/pull/99#discussion_r1",
    "path": "src/main.py",
    "line": 42,
    "body": "Consider using a context manager here."
  }
]
EOF

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}")"
  output="$(cat "${output_file}")"

  assert_exit_code "10" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=detected" "${output}"

  # Clean up report file
  local report_file
  report_file="$(echo "${output}" | grep CLAUDE_LOOP_REPORT_FILE= | cut -d= -f2)"
  [[ -n "${report_file}" ]] && rm -f "${report_file}"

  echo "  PASS: test_inline_comment_detected"
  rm -rf "${temp_dir}"
}

# ── Test: PR review (not comment) detected ───────────────────────────

test_pr_review_detected() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  cat > "${top_json}" <<'EOF'
{
  "comments": [],
  "reviews": [
    {
      "author": {"login": "claude"},
      "submittedAt": "2026-03-21T14:06:00Z",
      "state": "CHANGES_REQUESTED",
      "url": "https://github.com/test/repo/pull/99#pullrequestreview-1",
      "body": "Please address the issues."
    }
  ],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "abc123",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:47Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}")"
  output="$(cat "${output_file}")"

  assert_exit_code "10" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=detected" "${output}"

  # Clean up report file
  local report_file
  report_file="$(echo "${output}" | grep CLAUDE_LOOP_REPORT_FILE= | cut -d= -f2)"
  [[ -n "${report_file}" ]] && rm -f "${report_file}"

  echo "  PASS: test_pr_review_detected"
  rm -rf "${temp_dir}"
}

# ── Test: Custom author ──────────────────────────────────────────────

test_custom_author() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "review-bot"},
      "createdAt": "2026-03-21T14:04:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "Custom bot review"
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "abc123",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:47Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}" --author review-bot)"
  output="$(cat "${output_file}")"

  assert_exit_code "10" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=detected" "${output}"

  # Clean up report file
  local report_file
  report_file="$(echo "${output}" | grep CLAUDE_LOOP_REPORT_FILE= | cut -d= -f2)"
  [[ -n "${report_file}" ]] && rm -f "${report_file}"

  echo "  PASS: test_custom_author"
  rm -rf "${temp_dir}"
}

# ── Test: Already-requested SHA skips re-post ────────────────────────

test_already_requested_sha_does_not_repost() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  # Claude reviewed, agent pushed, re-review already requested for this SHA
  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "## Review"
    },
    {
      "author": {"login": "symphony-bot"},
      "createdAt": "2026-03-21T14:20:00Z",
      "body": "<!-- symphony-claude-rereview:def456 -->\n@claude Please review."
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "def456",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:00Z"},
    {"oid": "def456", "committedDate": "2026-03-21T14:15:00Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  # With wait-seconds=0, it should not re-post, but the already-requested
  # re-review is still pending.
  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}" --head-sha def456)"
  output="$(cat "${output_file}")"

  assert_exit_code "0" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=pending" "${output}"
  assert_contains "CLAUDE_LOOP_ACTION=wait" "${output}"

  echo "  PASS: test_already_requested_sha_does_not_repost"
  rm -rf "${temp_dir}"
}

# ── Test: Report excludes re-review markers ──────────────────────────

test_report_excludes_rereview_markers() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  # PR has a Claude review AND a re-review marker comment
  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "## PR Review\n\nReal feedback here."
    },
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:05:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-2",
      "body": "<!-- symphony-claude-rereview:abc123 -->\n@claude Please review."
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "abc123",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:47Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}")"
  output="$(cat "${output_file}")"

  assert_exit_code "10" "${rc}"

  # Verify report contains real feedback but not the marker
  local report_file report_content
  report_file="$(echo "${output}" | grep CLAUDE_LOOP_REPORT_FILE= | cut -d= -f2)"
  report_content="$(cat "${report_file}")"
  assert_contains "Real feedback here" "${report_content}"
  assert_contains "1 comment(s) found" "${report_content}"
  assert_not_contains "symphony-claude-rereview" "${report_content}"
  rm -f "${report_file}"

  echo "  PASS: test_report_excludes_rereview_markers"
  rm -rf "${temp_dir}"
}

test_report_shows_latest_feedback_only() {
  local temp_dir top_json inline_json output_file rc output report_file report_content
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "Old feedback that was already fixed."
    },
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:08:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-2",
      "body": "Latest feedback to fix now."
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "abc123",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:47Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}")"
  output="$(cat "${output_file}")"

  assert_exit_code "10" "${rc}"
  report_file="$(echo "${output}" | grep CLAUDE_LOOP_REPORT_FILE= | cut -d= -f2)"
  report_content="$(cat "${report_file}")"

  assert_contains "2 comment(s) found since 2026-03-21T14:00:00Z; showing latest only." "${report_content}"
  assert_contains "Latest feedback to fix now." "${report_content}"
  assert_not_contains "Old feedback that was already fixed." "${report_content}"
  rm -f "${report_file}"

  echo "  PASS: test_report_shows_latest_feedback_only"
  rm -rf "${temp_dir}"
}

# ── Test: The exact PR #109 scenario that exposed the bug ────────────

test_pr109_scenario_head_after_review() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  # Reproduce the PR #109 timeline:
  # - PR created at 14:02:45Z
  # - First commit at 14:01:47Z
  # - Claude reviews at 14:04:04Z
  # - Lint fix commit at 14:10:19Z (HEAD is now AFTER Claude's review)
  #
  # With the fix, head newer than feedback + no markers → request_and_wait
  # (posts re-review marker so round counter advances).  With wait-seconds=0
  # the poll returns immediately, but the re-review is pending.

  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:04Z",
      "updatedAt": null,
      "url": "https://github.com/test/repo/pull/109#issuecomment-123",
      "body": "## PR Review: KANBAN-1115 ALL-99\n\nLooks good with minor issues."
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/109",
  "headRefOid": "024e7143",
  "commits": [
    {"oid": "c838f51c", "committedDate": "2026-03-21T14:01:47Z"},
    {"oid": "024e7143", "committedDate": "2026-03-21T14:10:19Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:02:45Z" "${output_file}" --dry-run)"
  output="$(cat "${output_file}")"

  assert_exit_code "0" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=pending" "${output}"
  assert_contains "CLAUDE_LOOP_ACTION=request_and_wait" "${output}"

  echo "  PASS: test_pr109_scenario_head_after_review"
  rm -rf "${temp_dir}"
}

# ── Test: max-rounds=1 means no re-reviews allowed ───────────────────

test_max_rounds_one_maxes_after_initial() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  # One Claude review exists (round 1 complete), max-rounds=1
  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "## Review\n\nOne issue found."
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "commit2",
  "commits": [
    {"oid": "commit1", "committedDate": "2026-03-21T14:01:00Z"},
    {"oid": "commit2", "committedDate": "2026-03-21T14:15:00Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}" --max-rounds 1)"
  output="$(cat "${output_file}")"

  assert_exit_code "0" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=maxed_out" "${output}"
  assert_contains "CLAUDE_LOOP_ROUND=1" "${output}"
  assert_contains "CLAUDE_LOOP_MAX_ROUNDS=1" "${output}"

  echo "  PASS: test_max_rounds_one_maxes_after_initial"
  rm -rf "${temp_dir}"
}

# ── Test: --disposition-file is accepted and does not break dry-run ───

test_disposition_file_accepted() {
  local temp_dir top_json inline_json output_file disposition_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"
  disposition_file="${temp_dir}/disposition.md"

  # Scenario that triggers request_and_wait: head newer than review, no markers
  cat > "${top_json}" <<'EOF'
{
  "comments": [
    {
      "author": {"login": "claude"},
      "createdAt": "2026-03-21T14:04:00Z",
      "url": "https://github.com/test/repo/pull/99#issuecomment-1",
      "body": "## Review\n\nSuggestion: add error handling.\nSuggestion: refactor helper."
    }
  ],
  "reviews": [],
  "url": "https://github.com/test/repo/pull/99",
  "headRefOid": "def456",
  "commits": [
    {"oid": "abc123", "committedDate": "2026-03-21T14:01:00Z"},
    {"oid": "def456", "committedDate": "2026-03-21T14:15:00Z"}
  ]
}
EOF
  echo '[]' > "${inline_json}"

  # Write a disposition file with not-taken items
  cat > "${disposition_file}" <<'EOF'
- Add error handling → **fixed**
- Refactor helper → **not taken**: out of scope, covered by ALL-99
EOF

  # Run with --dry-run and --disposition-file; should succeed without error
  rc="$(run_loop "${top_json}" "${inline_json}" "2026-03-21T14:00:00Z" "${output_file}" \
    --dry-run --disposition-file "${disposition_file}")"
  output="$(cat "${output_file}")"

  assert_exit_code "0" "${rc}"
  assert_contains "CLAUDE_LOOP_STATUS=pending" "${output}"
  assert_contains "CLAUDE_LOOP_ACTION=request_and_wait" "${output}"

  echo "  PASS: test_disposition_file_accepted"
  rm -rf "${temp_dir}"
}

# ── Run all tests ────────────────────────────────────────────────────

echo "Running symphony_claude_review_loop tests..."
test_initial_review_detected
test_no_feedback_quiet
test_running_workflow_keeps_initial_review_pending
test_old_feedback_before_since_is_quiet
test_head_newer_no_markers_advances_round
test_head_newer_with_markers_triggers_rereview
test_feedback_current_reports_immediately
test_maxed_out_after_limit
test_max_rounds_one_maxes_after_initial
test_under_limit_head_newer_advances
test_inline_comment_detected
test_pr_review_detected
test_custom_author
test_already_requested_sha_does_not_repost
test_report_excludes_rereview_markers
test_report_shows_latest_feedback_only
test_pr109_scenario_head_after_review
test_disposition_file_accepted

echo "symphony_claude_review_loop tests passed (18/18)"
