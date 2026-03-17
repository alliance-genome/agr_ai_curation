#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_claude_review_rounds.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

run_helper() {
  local top_json="$1"
  local inline_json="$2"
  local output_file="$3"
  shift 3

  bash "${SCRIPT_PATH}" \
    --repo alliance-genome/agr_ai_curation \
    --pr 42 \
    --top-json-file "${top_json}" \
    --inline-json-file "${inline_json}" \
    "$@" \
    >"${output_file}"
}

test_reports_no_feedback() {
  local temp_dir top_json inline_json output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"

  cat > "${top_json}" <<'EOF'
{"url":"https://example.test/pr/42","comments":[{"author":{"login":"someone-else"},"createdAt":"2026-03-07T16:03:54Z","updatedAt":"2026-03-07T16:03:54Z","body":"looks good"}],"reviews":[]}
EOF
  echo '[]' > "${inline_json}"

  output="$(run_helper "${top_json}" "${inline_json}" "${temp_dir}/out.txt"; cat "${temp_dir}/out.txt")"
  assert_contains "CLAUDE_REVIEW_STATUS=no_feedback" "${output}"
  assert_contains "CLAUDE_REVIEW_ROUNDS=0" "${output}"
}

test_counts_initial_round_only() {
  local temp_dir top_json inline_json output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"

  cat > "${top_json}" <<'EOF'
{"url":"https://example.test/pr/42","comments":[{"author":{"login":"claude"},"createdAt":"2026-03-07T16:03:54Z","updatedAt":"2026-03-07T16:03:54Z","body":"please fix x"}],"reviews":[]}
EOF
  echo '[]' > "${inline_json}"

  output="$(run_helper "${top_json}" "${inline_json}" "${temp_dir}/out.txt"; cat "${temp_dir}/out.txt")"
  assert_contains "CLAUDE_REVIEW_STATUS=below_limit" "${output}"
  assert_contains "CLAUDE_REVIEW_ROUNDS=1" "${output}"
  assert_contains "CLAUDE_REVIEW_INITIAL_ROUND=1" "${output}"
}

test_counts_feedback_after_rereview_request() {
  local temp_dir top_json inline_json output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"

  cat > "${top_json}" <<'EOF'
{"url":"https://example.test/pr/42","comments":[
  {"author":{"login":"claude"},"createdAt":"2026-03-07T16:03:54Z","updatedAt":"2026-03-07T16:03:54Z","body":"please fix x"},
  {"author":{"login":"codex"},"createdAt":"2026-03-07T16:10:00Z","updatedAt":"2026-03-07T16:10:00Z","body":"<!-- symphony-claude-rereview:abc1234 -->\n@claude Please review these recent changes."}
],"reviews":[{"author":{"login":"claude"},"submittedAt":"2026-03-07T16:20:00Z","url":"https://example.test/review"}]}
EOF
  echo '[]' > "${inline_json}"

  output="$(run_helper "${top_json}" "${inline_json}" "${temp_dir}/out.txt"; cat "${temp_dir}/out.txt")"
  assert_contains "CLAUDE_REVIEW_STATUS=maxed" "${output}"
  assert_contains "CLAUDE_REVIEW_ROUNDS=2" "${output}"
  assert_contains "CLAUDE_REVIEW_RESPONDED_REQUESTS=1" "${output}"
  assert_contains "CLAUDE_REVIEW_ROUNDS_MAXED=1" "${output}"
}

test_reports_pending_rereview_request_without_new_feedback() {
  local temp_dir top_json inline_json output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"

  cat > "${top_json}" <<'EOF'
{"url":"https://example.test/pr/42","comments":[
  {"author":{"login":"claude"},"createdAt":"2026-03-07T16:03:54Z","updatedAt":"2026-03-07T16:03:54Z","body":"please fix x"},
  {"author":{"login":"codex"},"createdAt":"2026-03-07T16:10:00Z","updatedAt":"2026-03-07T16:10:00Z","body":"<!-- symphony-claude-rereview:abc1234 -->\n@claude Please review these recent changes."}
],"reviews":[]}
EOF
  echo '[]' > "${inline_json}"

  output="$(run_helper "${top_json}" "${inline_json}" "${temp_dir}/out.txt"; cat "${temp_dir}/out.txt")"
  assert_contains "CLAUDE_REVIEW_STATUS=below_limit" "${output}"
  assert_contains "CLAUDE_REVIEW_ROUNDS=1" "${output}"
  assert_contains "CLAUDE_REVIEW_PENDING_REQUESTS=1" "${output}"
}

test_counts_inline_feedback_after_rereview_request() {
  local temp_dir top_json inline_json output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"

  cat > "${top_json}" <<'EOF'
{"url":"https://example.test/pr/42","comments":[
  {"author":{"login":"claude"},"createdAt":"2026-03-07T16:03:54Z","updatedAt":"2026-03-07T16:03:54Z","body":"please fix x"},
  {"author":{"login":"codex"},"createdAt":"2026-03-07T16:10:00Z","updatedAt":"2026-03-07T16:10:00Z","body":"<!-- symphony-claude-rereview:abc1234 -->\n@claude Please review these recent changes."}
],"reviews":[]}
EOF
  cat > "${inline_json}" <<'EOF'
[{"user":{"login":"claude"},"created_at":"2026-03-07T16:21:00Z","updated_at":"2026-03-07T16:21:00Z","html_url":"https://example.test/inline"}]
EOF

  output="$(run_helper "${top_json}" "${inline_json}" "${temp_dir}/out.txt"; cat "${temp_dir}/out.txt")"
  assert_contains "CLAUDE_REVIEW_STATUS=maxed" "${output}"
  assert_contains "CLAUDE_REVIEW_ROUNDS=2" "${output}"
}

test_reports_no_feedback
test_counts_initial_round_only
test_counts_feedback_after_rereview_request
test_reports_pending_rereview_request_without_new_feedback
test_counts_inline_feedback_after_rereview_request

echo "symphony_claude_review_rounds tests passed"
