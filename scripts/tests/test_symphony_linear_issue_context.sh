#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_linear_issue_context.sh"

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

test_help_lists_contract() {
  local output
  output="$(bash "${SCRIPT_PATH}" --help)"
  assert_contains "Why prefer this helper over raw GraphQL" "${output}"
  assert_contains "LINEAR_CONTEXT_STATUS=ok|error" "${output}"
  assert_contains "env, json, pretty" "${output}"
}

test_context_normalizes_workpad_and_history() {
  local temp_dir raw_json output json_file
  temp_dir="$(mktemp -d)"
  raw_json="${temp_dir}/linear.json"

  cat > "${raw_json}" <<'EOF'
{
  "data": {
    "issue": {
      "id": "issue-123",
      "identifier": "ALL-123",
      "title": "Implement canonical Linear helpers",
      "description": "Scope goes here.",
      "url": "https://linear.app/test/issue/ALL-123",
      "createdAt": "2026-03-22T10:00:00Z",
      "updatedAt": "2026-03-22T11:00:00Z",
      "priority": 2,
      "state": {"id": "state-in-progress", "name": "In Progress", "type": "started"},
      "labels": {
        "nodes": [
          {"id": "label-1", "name": "wave-1", "color": "#111111"}
        ]
      },
      "comments": {
        "nodes": [
          {
            "id": "comment-1",
            "body": "<!-- symphony-workpad:v1 issue:ALL-123 -->\n\n# Symphony Workpad\n\nOld note.",
            "createdAt": "2026-03-22T10:05:00Z",
            "updatedAt": "2026-03-22T10:05:00Z",
            "user": {"id": "user-1", "name": "Codex", "displayName": "Codex"}
          },
          {
            "id": "comment-2",
            "body": "Please also cover the duplicate case.",
            "createdAt": "2026-03-22T10:10:00Z",
            "updatedAt": "2026-03-22T10:12:00Z",
            "user": {"id": "user-2", "name": "Christopher Tabone", "displayName": "Christopher Tabone"}
          },
          {
            "id": "comment-3",
            "body": "<!-- symphony-workpad:v1 issue:ALL-123 -->\n\n# Symphony Workpad\n\nNewest note.",
            "createdAt": "2026-03-22T10:20:00Z",
            "updatedAt": "2026-03-22T10:25:00Z",
            "user": {"id": "user-3", "name": "Codex", "displayName": "Codex"}
          }
        ]
      },
      "attachments": {
        "nodes": [
          {
            "id": "attachment-old",
            "title": "External analysis",
            "subtitle": "External JSON",
            "url": "https://example.org/analysis.json",
            "sourceType": "unknown",
            "createdAt": "2026-03-22T10:30:00Z",
            "updatedAt": "2026-03-22T10:30:00Z"
          },
          {
            "id": "attachment-new",
            "title": "Trace fixture",
            "subtitle": "Linear JSON",
            "url": "https://uploads.linear.app/org/upload/fixture.json",
            "sourceType": "unknown",
            "createdAt": "2026-03-22T10:40:00Z",
            "updatedAt": "2026-03-22T10:41:00Z"
          }
        ]
      },
      "history": {
        "nodes": [
          {
            "id": "hist-1",
            "createdAt": "2026-03-22T10:01:00Z",
            "fromState": {"id": "todo", "name": "Todo", "type": "unstarted"},
            "toState": {"id": "in-progress", "name": "In Progress", "type": "started"}
          }
        ]
      },
      "team": {
        "id": "team-1",
        "name": "Alliance",
        "key": "ALL",
        "states": {
          "nodes": [
            {"id": "todo", "name": "Todo", "type": "unstarted", "position": 1},
            {"id": "in-progress", "name": "In Progress", "type": "started", "position": 2}
          ]
        }
      }
    }
  }
}
EOF

  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --linear-json-file "${raw_json}")"

  assert_contains "LINEAR_CONTEXT_STATUS=ok" "${output}"
  assert_contains "LINEAR_CONTEXT_WORKPAD_COMMENT_ID=comment-3" "${output}"
  assert_contains "LINEAR_CONTEXT_LATEST_NON_WORKPAD_COMMENT_ID=comment-2" "${output}"
  assert_contains "LINEAR_CONTEXT_WORKPAD_DUPLICATE_COUNT=1" "${output}"
  assert_contains "LINEAR_CONTEXT_ATTACHMENTS_COUNT=2" "${output}"

  json_file="$(echo "${output}" | awk -F= '/^LINEAR_CONTEXT_JSON_FILE=/{print $2}')"
  assert_equals "comment-3" "$(jq -r '.workpad_comment.id' "${json_file}")"
  assert_equals "comment-2" "$(jq -r '.latest_non_workpad_comment.id' "${json_file}")"
  assert_equals "1" "$(jq -r '.duplicate_workpad_count' "${json_file}")"
  assert_equals "Todo" "$(jq -r '.history[0].from_state.name' "${json_file}")"
  assert_equals "In Progress" "$(jq -r '.issue.state.name' "${json_file}")"
  assert_equals "attachment-new" "$(jq -r '.attachments[0].id' "${json_file}")"
  assert_equals "true" "$(jq -r '.attachments[0].download_requires_linear_api_key' "${json_file}")"
  assert_equals "attachment-old" "$(jq -r '.attachments[1].id' "${json_file}")"
  assert_equals "false" "$(jq -r '.attachments[1].download_requires_linear_api_key' "${json_file}")"

  output="$(bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --linear-json-file "${raw_json}" \
    --format pretty)"

  assert_contains "Attachment details:" "${output}"
  assert_contains "Download: use Linear API-key auth" "${output}"
  assert_contains "Download: do not send the Linear API key" "${output}"

  if bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --linear-json-file "${raw_json}" \
    --attachments-first nope >/dev/null 2>&1; then
    echo "FAIL: --attachments-first should reject non-numeric values" >&2
    exit 1
  fi

  rm -rf "${temp_dir}"
}

test_help_lists_contract
test_context_normalizes_workpad_and_history

echo "symphony_linear_issue_context tests passed"
