#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_request_claude_rereview.sh"

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
  local pr_json="$1"
  local inline_json="$2"
  local head_sha="$3"
  local head_committed_at="$4"
  local output_file="$5"
  shift 5

  bash "${SCRIPT_PATH}" \
    --repo alliance-genome/agr_ai_curation \
    --pr 42 \
    --head-sha "${head_sha}" \
    --head-committed-at "${head_committed_at}" \
    --pr-json-file "${pr_json}" \
    --inline-json-file "${inline_json}" \
    "$@" \
    --dry-run \
    >"${output_file}"
}

test_skips_when_no_prior_claude_feedback() {
  local temp_dir pr_json inline_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/pr.json"
  inline_json="${temp_dir}/inline.json"

  cat > "${pr_json}" <<'EOF'
{"url":"https://example.test/pr/42","headRefOid":"abc1234","comments":[{"author":{"login":"someone-else"},"body":"looks good"}],"reviews":[],"commits":[{"oid":"abc1234","committedDate":"2026-03-07T16:15:00Z"}]}
EOF
  echo '[]' > "${inline_json}"

  output="$(run_helper "${pr_json}" "${inline_json}" "abc1234" "2026-03-07T16:15:00Z" "${temp_dir}/out.txt"; cat "${temp_dir}/out.txt")"
  assert_contains "CLAUDE_REREVIEW_STATUS=skipped_no_prior_feedback" "${output}"
}

test_dry_run_requests_rereview_when_head_is_newer_than_claude_feedback() {
  local temp_dir pr_json inline_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/pr.json"
  inline_json="${temp_dir}/inline.json"

  cat > "${pr_json}" <<'EOF'
{"url":"https://example.test/pr/42","headRefOid":"abc1234","comments":[{"author":{"login":"claude"},"createdAt":"2026-03-07T16:03:54Z","updatedAt":"2026-03-07T16:03:54Z","body":"please fix x"}],"reviews":[],"commits":[{"oid":"abc1234","committedDate":"2026-03-07T16:15:00Z"}]}
EOF
  echo '[]' > "${inline_json}"

  output="$(run_helper "${pr_json}" "${inline_json}" "abc1234" "2026-03-07T16:15:00Z" "${temp_dir}/out.txt"; cat "${temp_dir}/out.txt")"
  assert_contains "CLAUDE_REREVIEW_STATUS=dry_run" "${output}"
  assert_contains "CLAUDE_REREVIEW_HEAD_SHA=abc1234" "${output}"
}

test_skips_duplicate_request_for_same_head_sha() {
  local temp_dir pr_json inline_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/pr.json"
  inline_json="${temp_dir}/inline.json"

  cat > "${pr_json}" <<'EOF'
{"url":"https://example.test/pr/42","headRefOid":"abc1234","comments":[{"author":{"login":"claude"},"createdAt":"2026-03-07T16:03:54Z","updatedAt":"2026-03-07T16:03:54Z","body":"please fix x"},{"author":{"login":"codex"},"createdAt":"2026-03-07T16:10:00Z","updatedAt":"2026-03-07T16:10:00Z","body":"<!-- symphony-claude-rereview:abc1234 -->\n@claude Please review these recent changes."}],"reviews":[],"commits":[{"oid":"abc1234","committedDate":"2026-03-07T16:15:00Z"}]}
EOF
  echo '[]' > "${inline_json}"

  output="$(run_helper "${pr_json}" "${inline_json}" "abc1234" "2026-03-07T16:15:00Z" "${temp_dir}/out.txt"; cat "${temp_dir}/out.txt")"
  assert_contains "CLAUDE_REREVIEW_STATUS=skipped_already_requested" "${output}"
}

test_accepts_prior_review_from_claude() {
  local temp_dir pr_json inline_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/pr.json"
  inline_json="${temp_dir}/inline.json"

  cat > "${pr_json}" <<'EOF'
{"url":"https://example.test/pr/42","headRefOid":"fedcba9","comments":[],"reviews":[{"author":{"login":"claude"},"state":"COMMENTED","submittedAt":"2026-03-07T16:03:54Z"}],"commits":[{"oid":"fedcba9","committedDate":"2026-03-07T16:15:00Z"}]}
EOF
  echo '[]' > "${inline_json}"

  output="$(run_helper "${pr_json}" "${inline_json}" "fedcba9" "2026-03-07T16:15:00Z" "${temp_dir}/out.txt"; cat "${temp_dir}/out.txt")"
  assert_contains "CLAUDE_REREVIEW_STATUS=dry_run" "${output}"
  assert_contains "CLAUDE_REREVIEW_HEAD_SHA=fedcba9" "${output}"
}

test_skips_when_head_is_not_newer_than_latest_claude_feedback() {
  local temp_dir pr_json inline_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/pr.json"
  inline_json="${temp_dir}/inline.json"

  cat > "${pr_json}" <<'EOF'
{"url":"https://example.test/pr/42","headRefOid":"abc1234","comments":[{"author":{"login":"claude"},"createdAt":"2026-03-07T16:20:00Z","updatedAt":"2026-03-07T16:20:00Z","body":"latest feedback"}],"reviews":[],"commits":[{"oid":"abc1234","committedDate":"2026-03-07T16:15:00Z"}]}
EOF
  echo '[]' > "${inline_json}"

  output="$(run_helper "${pr_json}" "${inline_json}" "abc1234" "2026-03-07T16:15:00Z" "${temp_dir}/out.txt"; cat "${temp_dir}/out.txt")"
  assert_contains "CLAUDE_REREVIEW_STATUS=skipped_head_not_newer_than_feedback" "${output}"
}

test_considers_inline_claude_feedback_when_deciding_if_head_is_newer() {
  local temp_dir pr_json inline_json output
  temp_dir="$(mktemp -d)"
  pr_json="${temp_dir}/pr.json"
  inline_json="${temp_dir}/inline.json"

  cat > "${pr_json}" <<'EOF'
{"url":"https://example.test/pr/42","headRefOid":"abc1234","comments":[{"author":{"login":"claude"},"createdAt":"2026-03-07T16:03:54Z","updatedAt":"2026-03-07T16:03:54Z","body":"please fix x"}],"reviews":[],"commits":[{"oid":"abc1234","committedDate":"2026-03-07T16:15:00Z"}]}
EOF
  cat > "${inline_json}" <<'EOF'
[{"user":{"login":"claude"},"created_at":"2026-03-07T16:21:00Z","updated_at":"2026-03-07T16:21:00Z","html_url":"https://example.test/inline"}]
EOF

  output="$(run_helper "${pr_json}" "${inline_json}" "abc1234" "2026-03-07T16:15:00Z" "${temp_dir}/out.txt"; cat "${temp_dir}/out.txt")"
  assert_contains "CLAUDE_REREVIEW_STATUS=skipped_head_not_newer_than_feedback" "${output}"
}

test_skips_when_no_prior_claude_feedback
test_dry_run_requests_rereview_when_head_is_newer_than_claude_feedback
test_skips_duplicate_request_for_same_head_sha
test_accepts_prior_review_from_claude
test_skips_when_head_is_not_newer_than_latest_claude_feedback
test_considers_inline_claude_feedback_when_deciding_if_head_is_newer

echo "symphony_request_claude_rereview tests passed"
