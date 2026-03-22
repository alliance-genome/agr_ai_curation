#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_linear_issue_state.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "FAIL: Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

make_context_json() {
  local path="$1"
  local current_state="$2"
  cat > "${path}" <<EOF
{
  "issue": {
    "id": "issue-123",
    "identifier": "ALL-123",
    "title": "Example issue",
    "description": "Description.",
    "url": "https://linear.app/test/issue/ALL-123",
    "state": {"id": "state-current", "name": "${current_state}", "type": "started"}
  },
  "comments_count": 0,
  "comments": [],
  "workpad_comment": null,
  "workpad_comments": [],
  "duplicate_workpad_count": 0,
  "latest_non_workpad_comment": null,
  "history": [],
  "team": {
    "id": "team-1",
    "name": "Alliance",
    "key": "ALL",
    "states": [
      {"id": "state-todo", "name": "Todo", "type": "unstarted", "position": 1},
      {"id": "state-progress", "name": "In Progress", "type": "started", "position": 2},
      {"id": "state-blocked", "name": "Blocked", "type": "backlog", "position": 3}
    ]
  }
}
EOF
}

test_help_lists_repo_states() {
  local output
  output="$(bash "${SCRIPT_PATH}" --help)"
  assert_contains "Human Review Prep" "${output}"
  assert_contains "Cancelled" "${output}"
  assert_contains "Canceled" "${output}"
  assert_contains "Duplicate" "${output}"
}

test_state_transition_updates_issue() {
  local temp_dir context_json curl_stub curl_log output
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"

  make_context_json "${context_json}" "Todo"

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
echo '{"data":{"issueUpdate":{"success":true}}}'
EOF
  chmod +x "${curl_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" \
    CURL_STUB_LOG="${curl_log}" \
    bash "${SCRIPT_PATH}" \
      --context-json-file "${context_json}" \
      --state "In Progress" \
      --from-state "Todo" \
      --linear-api-key test-key
  )"

  assert_contains "LINEAR_STATE_STATUS=ok" "${output}"
  assert_contains "LINEAR_STATE_FROM=Todo" "${output}"
  assert_contains "LINEAR_STATE_TO=In Progress" "${output}"
  assert_contains "LINEAR_STATE_TARGET_ID=state-progress" "${output}"
  assert_contains "\"stateId\":\"state-progress\"" "$(cat "${curl_log}")"

  rm -rf "${temp_dir}"
}

test_missing_state_errors() {
  local temp_dir context_json output rc
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"

  make_context_json "${context_json}" "Todo"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --context-json-file "${context_json}" \
    --state "Ready for PR" \
    --linear-api-key test-key 2>&1)"
  rc=$?
  set -e

  [[ "${rc}" == "3" ]] || {
    echo "FAIL: Expected exit code 3, got ${rc}" >&2
    exit 1
  }
  assert_contains "LINEAR_STATE_STATUS=error" "${output}"
  assert_contains "LINEAR_STATE_ERROR=Target state not found" "${output}"

  rm -rf "${temp_dir}"
}

test_from_state_mismatch_errors() {
  local temp_dir context_json output rc
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"

  make_context_json "${context_json}" "Blocked"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --context-json-file "${context_json}" \
    --state "In Progress" \
    --from-state "Todo" \
    --linear-api-key test-key 2>&1)"
  rc=$?
  set -e

  [[ "${rc}" == "3" ]] || {
    echo "FAIL: Expected exit code 3, got ${rc}" >&2
    exit 1
  }
  assert_contains "LINEAR_STATE_STATUS=error" "${output}"
  assert_contains "LINEAR_STATE_ERROR=Current state does not match --from-state." "${output}"

  rm -rf "${temp_dir}"
}

test_help_lists_repo_states
test_state_transition_updates_issue
test_missing_state_errors
test_from_state_mismatch_errors

echo "symphony_linear_issue_state tests passed"
