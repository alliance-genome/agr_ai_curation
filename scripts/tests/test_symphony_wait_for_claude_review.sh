#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_wait_for_claude_review.sh"

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
  local since="$3"
  local output_file="$4"

  set +e
  bash "${SCRIPT_PATH}" \
    --repo alliance-genome/agr_ai_curation \
    --pr 39 \
    --since "${since}" \
    --wait-seconds 0 \
    --top-json-file "${top_json}" \
    --inline-json-file "${inline_json}" \
    >"${output_file}"
  local rc=$?
  set -e

  printf '%s' "${rc}"
}

test_detects_top_level_claude_comment_after_since() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  cat > "${top_json}" <<'EOF'
{"comments":[{"author":{"login":"claude"},"createdAt":"2026-03-07T16:03:54Z","updatedAt":"2026-03-07T16:03:54Z","url":"https://example.test/comment"}],"reviews":[]}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_helper "${top_json}" "${inline_json}" "2026-03-07T16:00:00Z" "${output_file}")"
  output="$(cat "${output_file}")"

  [[ "${rc}" == "10" ]] || {
    echo "Expected exit code 10, got ${rc}" >&2
    exit 1
  }
  assert_contains "CLAUDE_REVIEW_STATUS=detected" "${output}"
  assert_contains "CLAUDE_REVIEW_SOURCE=top_level_comment" "${output}"
}

test_ignores_old_claude_comment_before_since() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  cat > "${top_json}" <<'EOF'
{"comments":[{"author":{"login":"claude"},"createdAt":"2026-03-07T15:03:54Z","updatedAt":"2026-03-07T15:03:54Z","url":"https://example.test/comment"}],"reviews":[]}
EOF
  echo '[]' > "${inline_json}"

  rc="$(run_helper "${top_json}" "${inline_json}" "2026-03-07T16:00:00Z" "${output_file}")"
  output="$(cat "${output_file}")"

  [[ "${rc}" == "0" ]] || {
    echo "Expected exit code 0, got ${rc}" >&2
    exit 1
  }
  assert_contains "CLAUDE_REVIEW_STATUS=quiet" "${output}"
}

test_detects_inline_claude_comment() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  echo '{"comments":[],"reviews":[]}' > "${top_json}"
  cat > "${inline_json}" <<'EOF'
[{"user":{"login":"claude"},"created_at":"2026-03-07T16:09:00Z","updated_at":"2026-03-07T16:09:00Z","html_url":"https://example.test/inline"}]
EOF

  rc="$(run_helper "${top_json}" "${inline_json}" "2026-03-07T16:00:00Z" "${output_file}")"
  output="$(cat "${output_file}")"

  [[ "${rc}" == "10" ]] || {
    echo "Expected exit code 10, got ${rc}" >&2
    exit 1
  }
  assert_contains "CLAUDE_REVIEW_SOURCE=inline_comment" "${output}"
}

test_detects_review_by_author_override() {
  local temp_dir top_json inline_json output_file rc output
  temp_dir="$(mktemp -d)"
  top_json="${temp_dir}/top.json"
  inline_json="${temp_dir}/inline.json"
  output_file="${temp_dir}/output.txt"

  cat > "${top_json}" <<'EOF'
{"comments":[],"reviews":[{"author":{"login":"review-bot"},"submittedAt":"2026-03-07T16:20:00Z","url":"https://example.test/review"}]}
EOF
  echo '[]' > "${inline_json}"

  set +e
  bash "${SCRIPT_PATH}" \
    --repo alliance-genome/agr_ai_curation \
    --pr 39 \
    --since "2026-03-07T16:00:00Z" \
    --author review-bot \
    --wait-seconds 0 \
    --top-json-file "${top_json}" \
    --inline-json-file "${inline_json}" \
    >"${output_file}"
  rc=$?
  set -e
  output="$(cat "${output_file}")"

  [[ "${rc}" == "10" ]] || {
    echo "Expected exit code 10, got ${rc}" >&2
    exit 1
  }
  assert_contains "CLAUDE_REVIEW_SOURCE=review" "${output}"
}

test_detects_top_level_claude_comment_after_since
test_ignores_old_claude_comment_before_since
test_detects_inline_claude_comment
test_detects_review_by_author_override

echo "symphony_wait_for_claude_review tests passed"
