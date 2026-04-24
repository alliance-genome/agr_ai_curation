#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_linear_workpad.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "FAIL: Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

assert_equals() {
  local expected="$1"
  local actual="$2"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "FAIL: Expected '${expected}', got '${actual}'" >&2
    exit 1
  fi
}

make_context_json() {
  local path="$1"
  local workpad_comments_json="${2:-[]}"
  local latest_non_workpad_json="${3:-null}"
  local comments_json="${4:-[]}"
  cat > "${path}" <<EOF
{
  "issue": {
    "id": "issue-123",
    "identifier": "ALL-123",
    "title": "Example issue",
    "description": "Description.",
    "url": "https://linear.app/test/issue/ALL-123",
    "state": {"id": "state-1", "name": "In Progress", "type": "started"}
  },
  "comments_count": 2,
  "comments": ${comments_json},
  "workpad_comment": $(jq -cn --argjson items "${workpad_comments_json}" '$items | sort_by(.updated_at) | last // null'),
  "workpad_comments": ${workpad_comments_json},
  "duplicate_workpad_count": $(jq -rn --argjson items "${workpad_comments_json}" '($items | length) as $count | if $count > 1 then ($count - 1) else 0 end'),
  "latest_non_workpad_comment": ${latest_non_workpad_json},
  "history": [],
  "team": null
}
EOF
}

test_show_reports_latest_workpad_and_duplicates() {
  local temp_dir context_json output body_file
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"

  make_context_json "${context_json}" \
    '[
      {
        "id": "workpad-1",
        "body": "<!-- symphony-workpad:v1 issue:ALL-123 -->\n\n# Symphony Workpad\n\nOld",
        "created_at": "2026-03-22T10:00:00Z",
        "updated_at": "2026-03-22T10:00:00Z",
        "user_name": "Codex",
        "is_workpad": true
      },
      {
        "id": "workpad-2",
        "body": "<!-- symphony-workpad:v1 issue:ALL-123 -->\n\n# Symphony Workpad\n\nNew",
        "created_at": "2026-03-22T10:10:00Z",
        "updated_at": "2026-03-22T10:15:00Z",
        "user_name": "Codex",
        "is_workpad": true
      }
    ]' \
    'null' \
    '[
      {
        "id": "workpad-1",
        "body": "<!-- symphony-workpad:v1 issue:ALL-123 -->\n\n# Symphony Workpad\n\nOld",
        "created_at": "2026-03-22T10:00:00Z",
        "updated_at": "2026-03-22T10:00:00Z",
        "user_name": "Codex",
        "is_workpad": true
      },
      {
        "id": "workpad-2",
        "body": "<!-- symphony-workpad:v1 issue:ALL-123 -->\n\n# Symphony Workpad\n\nNew",
        "created_at": "2026-03-22T10:10:00Z",
        "updated_at": "2026-03-22T10:15:00Z",
        "user_name": "Codex",
        "is_workpad": true
      }
    ]'

  output="$(bash "${SCRIPT_PATH}" show --context-json-file "${context_json}")"
  assert_contains "WORKPAD_STATUS=found" "${output}"
  assert_contains "WORKPAD_COMMENT_ID=workpad-2" "${output}"
  assert_contains "WORKPAD_DUPLICATE_COUNT=1" "${output}"
  body_file="$(echo "${output}" | awk -F= '/^WORKPAD_BODY_FILE=/{print $2}')"
  assert_contains "New" "$(cat "${body_file}")"

  rm -rf "${temp_dir}"
}

test_latest_human_materializes_comment() {
  local temp_dir context_json output latest_file
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"

  make_context_json "${context_json}" \
    '[]' \
    '{
      "id": "comment-2",
      "body": "Please include the edge case notes.",
      "created_at": "2026-03-22T10:10:00Z",
      "updated_at": "2026-03-22T10:12:00Z",
      "user_name": "Christopher Tabone",
      "is_workpad": false
    }' \
    '[
      {
        "id": "comment-2",
        "body": "Please include the edge case notes.",
        "created_at": "2026-03-22T10:10:00Z",
        "updated_at": "2026-03-22T10:12:00Z",
        "user_name": "Christopher Tabone",
        "is_workpad": false
      }
    ]'

  output="$(bash "${SCRIPT_PATH}" latest-human --context-json-file "${context_json}")"
  assert_contains "WORKPAD_STATUS=found" "${output}"
  assert_contains "LATEST_NON_WORKPAD_COMMENT_ID=comment-2" "${output}"
  latest_file="$(echo "${output}" | awk -F= '/^LATEST_NON_WORKPAD_COMMENT_FILE=/{print $2}')"
  assert_contains "edge case notes" "$(cat "${latest_file}")"

  rm -rf "${temp_dir}"
}

test_upsert_creates_marked_workpad() {
  local temp_dir context_json body_file curl_stub curl_log output payload_json
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  body_file="${temp_dir}/body.md"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"

  make_context_json "${context_json}" '[]' 'null' '[]'
  cat > "${body_file}" <<'EOF'
# Symphony Workpad

Started implementation.
EOF

  cat > "${curl_stub}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
payload=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--data" ]]; then
    payload="${2:-}"
    shift 2
    continue
  fi
  shift
done
printf '%s\n' "${payload}" > "${CURL_STUB_LOG}"
echo '{"data":{"commentCreate":{"success":true,"comment":{"id":"created-workpad","body":"ignored","updatedAt":"2026-03-22T10:30:00Z"}}}}'
EOF
  chmod +x "${curl_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" \
    CURL_STUB_LOG="${curl_log}" \
    bash "${SCRIPT_PATH}" upsert \
      --context-json-file "${context_json}" \
      --body-file "${body_file}" \
      --linear-api-key test-key
  )"

  assert_contains "WORKPAD_STATUS=created" "${output}"
  assert_contains "WORKPAD_ACTION=create" "${output}"
  assert_contains "WORKPAD_COMMENT_ID=created-workpad" "${output}"

  payload_json="$(cat "${curl_log}")"
  assert_equals "issue-123" "$(jq -r '.variables.issueId' <<< "${payload_json}")"
  assert_contains "<!-- symphony-workpad:v1 issue:ALL-123 -->" "$(jq -r '.variables.body' <<< "${payload_json}")"

  rm -rf "${temp_dir}"
}

test_append_section_updates_existing_workpad() {
  local temp_dir context_json section_file curl_stub curl_log output updated_body
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  section_file="${temp_dir}/section.md"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"

  make_context_json "${context_json}" \
    '[
      {
        "id": "workpad-9",
        "body": "<!-- symphony-workpad:v1 issue:ALL-123 -->\n\n# Symphony Workpad\n\n## Claude Feedback Disposition\n\nOld entry.\n",
        "created_at": "2026-03-22T10:00:00Z",
        "updated_at": "2026-03-22T10:05:00Z",
        "user_name": "Codex",
        "is_workpad": true
      }
    ]' \
    'null' \
    '[
      {
        "id": "workpad-9",
        "body": "<!-- symphony-workpad:v1 issue:ALL-123 -->\n\n# Symphony Workpad\n\n## Claude Feedback Disposition\n\nOld entry.\n",
        "created_at": "2026-03-22T10:00:00Z",
        "updated_at": "2026-03-22T10:05:00Z",
        "user_name": "Codex",
        "is_workpad": true
      }
    ]'

  cat > "${section_file}" <<'EOF'
- fixed: clarified help output
- not taken: example
EOF

  cat > "${curl_stub}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
payload=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--data" ]]; then
    payload="${2:-}"
    shift 2
    continue
  fi
  shift
done
printf '%s\n' "${payload}" > "${CURL_STUB_LOG}"
echo '{"data":{"commentUpdate":{"success":true,"comment":{"id":"workpad-9","body":"ignored","updatedAt":"2026-03-22T10:30:00Z"}}}}'
EOF
  chmod +x "${curl_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" \
    CURL_STUB_LOG="${curl_log}" \
    bash "${SCRIPT_PATH}" append-section \
      --context-json-file "${context_json}" \
      --section-title "Claude Feedback Disposition" \
      --section-file "${section_file}" \
      --linear-api-key test-key
  )"

  assert_contains "WORKPAD_STATUS=updated" "${output}"
  assert_contains "WORKPAD_ACTION=update" "${output}"
  updated_body="$(jq -r '.variables.body' < "${curl_log}")"
  assert_contains "## Claude Feedback Disposition" "${updated_body}"
  assert_contains "fixed: clarified help output" "${updated_body}"
  if [[ "${updated_body}" == *"Old entry."* ]]; then
    echo "FAIL: Expected append-section to replace the old section body" >&2
    exit 1
  fi

  rm -rf "${temp_dir}"
}

test_append_section_reads_stdin_without_shell_interpolation() {
  local temp_dir context_json curl_stub curl_log output updated_body
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"

  make_context_json "${context_json}" \
    '[
      {
        "id": "workpad-10",
        "body": "<!-- symphony-workpad:v1 issue:ALL-123 -->\n\n# Symphony Workpad\n",
        "created_at": "2026-03-22T10:00:00Z",
        "updated_at": "2026-03-22T10:05:00Z",
        "user_name": "Codex",
        "is_workpad": true
      }
    ]' \
    'null' \
    '[
      {
        "id": "workpad-10",
        "body": "<!-- symphony-workpad:v1 issue:ALL-123 -->\n\n# Symphony Workpad\n",
        "created_at": "2026-03-22T10:00:00Z",
        "updated_at": "2026-03-22T10:05:00Z",
        "user_name": "Codex",
        "is_workpad": true
      }
    ]'

  cat > "${curl_stub}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
payload=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--data" ]]; then
    payload="${2:-}"
    shift 2
    continue
  fi
  shift
done
printf '%s\n' "${payload}" > "${CURL_STUB_LOG}"
echo '{"data":{"commentUpdate":{"success":true,"comment":{"id":"workpad-10","body":"ignored","updatedAt":"2026-03-22T10:30:00Z"}}}}'
EOF
  chmod +x "${curl_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" \
    CURL_STUB_LOG="${curl_log}" \
    bash "${SCRIPT_PATH}" append-section \
      --context-json-file "${context_json}" \
      --section-title "Review Handoff" \
      --section-stdin \
      --linear-api-key test-key <<'EOF'
- Outcome: kept inline `code`, `$(echo expanded)`, and `${HOME}` literal.
- Validation: no shell interpolation occurred.
EOF
  )"

  assert_contains "WORKPAD_STATUS=updated" "${output}"
  assert_contains "WORKPAD_ACTION=update" "${output}"
  updated_body="$(jq -r '.variables.body' < "${curl_log}")"
  assert_contains "## Review Handoff" "${updated_body}"
  assert_contains 'inline `code`, `$(echo expanded)`, and `${HOME}` literal' "${updated_body}"
  if [[ "${updated_body}" == *"expanded literal"* ]]; then
    echo "FAIL: Expected append-section --section-stdin to preserve literal command text" >&2
    exit 1
  fi

  rm -rf "${temp_dir}"
}

test_show_reports_latest_workpad_and_duplicates
test_latest_human_materializes_comment
test_upsert_creates_marked_workpad
test_append_section_updates_existing_workpad
test_append_section_reads_stdin_without_shell_interpolation

echo "symphony_linear_workpad tests passed"
