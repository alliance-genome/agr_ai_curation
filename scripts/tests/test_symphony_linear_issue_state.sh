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
      {"id": "state-backlog", "name": "Backlog", "type": "backlog", "position": 0},
      {"id": "state-todo", "name": "Todo", "type": "unstarted", "position": 1},
      {"id": "state-progress", "name": "In Progress", "type": "started", "position": 2},
      {"id": "state-needs-review", "name": "Needs Review", "type": "started", "position": 3},
      {"id": "state-in-review", "name": "In Review", "type": "started", "position": 4},
      {"id": "state-ready-for-pr", "name": "Ready for PR", "type": "started", "position": 5},
      {"id": "state-human-review-prep", "name": "Human Review Prep", "type": "started", "position": 6},
      {"id": "state-human-review", "name": "Human Review", "type": "started", "position": 7},
      {"id": "state-finalizing", "name": "Finalizing", "type": "started", "position": 8},
      {"id": "state-blocked", "name": "Blocked", "type": "backlog", "position": 9},
      {"id": "state-done", "name": "Done", "type": "completed", "position": 10},
      {"id": "state-closed", "name": "Closed", "type": "canceled", "position": 11},
      {"id": "state-cancelled", "name": "Cancelled", "type": "canceled", "position": 12},
      {"id": "state-canceled", "name": "Canceled", "type": "canceled", "position": 13},
      {"id": "state-duplicate", "name": "Duplicate", "type": "canceled", "position": 14}
    ]
  }
}
EOF
}

write_curl_stub() {
  local path="$1"
  cat > "${path}" <<'EOF'
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
if [[ "${payload}" == *"SymphonyRefreshIssueState"* ]]; then
  jq -cn --arg state "${CURL_STUB_CURRENT_STATE:?}" '{data: {issue: {state: {name: $state}}}}'
  exit 0
fi
printf '%s\n' "${payload}" > "${CURL_STUB_LOG}"
echo '{"data":{"issueUpdate":{"success":true}}}'
EOF
  chmod +x "${path}"
}

test_help_lists_repo_states() {
  local output
  output="$(bash "${SCRIPT_PATH}" --help)"
  assert_contains "Human Review Prep" "${output}"
  assert_contains "Cancelled" "${output}"
  assert_contains "Canceled" "${output}"
  assert_contains "Duplicate" "${output}"
  assert_contains "--allow-workflow-override" "${output}"
  assert_contains "--override-reason" "${output}"
  assert_contains "Workflow transition rejected" "${output}"
}

test_state_transition_updates_issue() {
  local temp_dir context_json curl_stub curl_log output
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"

  make_context_json "${context_json}" "Todo"

  write_curl_stub "${curl_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" \
    CURL_STUB_LOG="${curl_log}" \
    CURL_STUB_CURRENT_STATE="Todo" \
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
  assert_contains "LINEAR_STATE_TRANSITION_GUARD=allowed" "${output}"
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
    --state "Imaginary State" \
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

test_all_documented_workflow_edges_are_allowed() {
  local edge from_state target_state temp_dir context_json curl_stub curl_log output
  local -a edges=(
    "Todo|In Progress"
    "Todo|Blocked"
    "In Progress|Needs Review"
    "In Progress|Blocked"
    "Needs Review|In Review"
    "Needs Review|In Progress"
    "Needs Review|Blocked"
    "In Review|Ready for PR"
    "In Review|Human Review Prep"
    "In Review|In Progress"
    "In Review|Blocked"
    "Ready for PR|Human Review Prep"
    "Ready for PR|In Progress"
    "Ready for PR|Blocked"
    "Human Review Prep|Human Review"
    "Human Review Prep|In Progress"
    "Human Review Prep|Blocked"
    "Human Review|Finalizing"
    "Human Review|In Progress"
    "Finalizing|Done"
    "Finalizing|In Progress"
    "Finalizing|Blocked"
    "Blocked|In Progress"
  )

  for edge in "${edges[@]}"; do
    IFS='|' read -r from_state target_state <<< "${edge}"
    temp_dir="$(mktemp -d)"
    context_json="${temp_dir}/context.json"
    curl_stub="${temp_dir}/curl"
    curl_log="${temp_dir}/curl.log"
    make_context_json "${context_json}" "${from_state}"
    write_curl_stub "${curl_stub}"

    output="$(
      PATH="${temp_dir}:${PATH}" \
      CURL_STUB_LOG="${curl_log}" \
      CURL_STUB_CURRENT_STATE="${from_state}" \
      bash "${SCRIPT_PATH}" \
        --context-json-file "${context_json}" \
        --state "${target_state}" \
        --from-state "${from_state}" \
        --linear-api-key test-key
    )"

    assert_contains "LINEAR_STATE_STATUS=ok" "${output}"
    assert_contains "LINEAR_STATE_FROM=${from_state}" "${output}"
    assert_contains "LINEAR_STATE_TO=${target_state}" "${output}"
    assert_contains "LINEAR_STATE_TRANSITION_GUARD=allowed" "${output}"
    [[ -s "${curl_log}" ]] || {
      echo "FAIL: Expected ${from_state} -> ${target_state} to call Linear." >&2
      exit 1
    }

    rm -rf "${temp_dir}"
  done
}

assert_transition_rejected() {
  local from_state="$1"
  local target_state="$2"
  local temp_dir context_json curl_stub curl_log output rc
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"
  make_context_json "${context_json}" "${from_state}"
  write_curl_stub "${curl_stub}"

  set +e
  output="$(
    PATH="${temp_dir}:${PATH}" \
    CURL_STUB_LOG="${curl_log}" \
    CURL_STUB_CURRENT_STATE="In Progress" \
    bash "${SCRIPT_PATH}" \
      --context-json-file "${context_json}" \
      --state "${target_state}" \
      --from-state "${from_state}" \
      --linear-api-key test-key 2>&1
  )"
  rc=$?
  set -e

  [[ "${rc}" == "4" ]] || {
    echo "FAIL: Expected ${from_state} -> ${target_state} to exit 4, got ${rc}" >&2
    printf 'Actual output:\n%s\n' "${output}" >&2
    exit 1
  }
  assert_contains "LINEAR_STATE_STATUS=error" "${output}"
  assert_contains "LINEAR_STATE_TRANSITION_GUARD=rejected" "${output}"
  assert_contains "LINEAR_STATE_ALLOWED_TARGETS=" "${output}"
  assert_contains "Transition is not allowed by the Symphony workflow graph" "${output}"
  [[ ! -e "${curl_log}" ]] || {
    echo "FAIL: Rejected transition ${from_state} -> ${target_state} called Linear." >&2
    exit 1
  }

  rm -rf "${temp_dir}"
}

test_invalid_shortcuts_are_rejected_before_linear() {
  assert_transition_rejected "In Progress" "Human Review Prep"
  assert_transition_rejected "In Progress" "In Review"
  assert_transition_rejected "In Progress" "Done"
  assert_transition_rejected "Backlog" "In Progress"
  assert_transition_rejected "Done" "In Progress"
}

is_documented_workflow_edge() {
  local candidate="$1 -> $2"
  local edge
  local -a edges=(
    "Todo -> In Progress"
    "Todo -> Blocked"
    "In Progress -> Needs Review"
    "In Progress -> Blocked"
    "Needs Review -> In Review"
    "Needs Review -> In Progress"
    "Needs Review -> Blocked"
    "In Review -> Ready for PR"
    "In Review -> Human Review Prep"
    "In Review -> In Progress"
    "In Review -> Blocked"
    "Ready for PR -> Human Review Prep"
    "Ready for PR -> In Progress"
    "Ready for PR -> Blocked"
    "Human Review Prep -> Human Review"
    "Human Review Prep -> In Progress"
    "Human Review Prep -> Blocked"
    "Human Review -> Finalizing"
    "Human Review -> In Progress"
    "Finalizing -> Done"
    "Finalizing -> In Progress"
    "Finalizing -> Blocked"
    "Blocked -> In Progress"
  )

  for edge in "${edges[@]}"; do
    if [[ "${candidate}" == "${edge}" ]]; then
      return 0
    fi
  done
  return 1
}

test_all_undocumented_workflow_edges_are_rejected() {
  local from_state target_state
  local -a states=(
    "Backlog"
    "Todo"
    "In Progress"
    "Needs Review"
    "In Review"
    "Ready for PR"
    "Human Review Prep"
    "Human Review"
    "Finalizing"
    "Blocked"
    "Done"
    "Closed"
    "Cancelled"
    "Canceled"
    "Duplicate"
  )

  for from_state in "${states[@]}"; do
    for target_state in "${states[@]}"; do
      if [[ "${from_state}" == "${target_state}" ]] || is_documented_workflow_edge "${from_state}" "${target_state}"; then
        continue
      fi
      assert_transition_rejected "${from_state}" "${target_state}"
    done
  done
}

test_allow_any_from_state_does_not_bypass_workflow_graph() {
  local temp_dir context_json output rc
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  make_context_json "${context_json}" "In Progress"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --context-json-file "${context_json}" \
    --state "Human Review Prep" \
    --allow-any-from-state \
    --linear-api-key test-key 2>&1)"
  rc=$?
  set -e

  [[ "${rc}" == "4" ]] || {
    echo "FAIL: Expected --allow-any-from-state invalid edge to exit 4, got ${rc}" >&2
    exit 1
  }
  assert_contains "LINEAR_STATE_TRANSITION_GUARD=rejected" "${output}"

  rm -rf "${temp_dir}"
}

test_same_state_preflight_is_allowed_without_mutation() {
  local temp_dir context_json curl_stub curl_log output
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"
  make_context_json "${context_json}" "Needs Review"
  write_curl_stub "${curl_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" \
    CURL_STUB_LOG="${curl_log}" \
    bash "${SCRIPT_PATH}" \
      --context-json-file "${context_json}" \
      --state "Needs Review" \
      --from-state "Needs Review" \
      --linear-api-key test-key
  )"

  assert_contains "LINEAR_STATE_STATUS=ok" "${output}"
  assert_contains "LINEAR_STATE_TRANSITION_GUARD=same_state" "${output}"
  [[ ! -e "${curl_log}" ]] || {
    echo "FAIL: Same-state preflight unexpectedly called Linear." >&2
    exit 1
  }

  rm -rf "${temp_dir}"
}

test_same_state_preflight_ignores_stale_from_state() {
  local temp_dir context_json output
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  make_context_json "${context_json}" "Done"

  output="$(bash "${SCRIPT_PATH}" --context-json-file "${context_json}" --state "Done" --from-state "Finalizing" --linear-api-key test-key)"

  assert_contains "LINEAR_STATE_STATUS=ok" "${output}"
  assert_contains "LINEAR_STATE_TRANSITION_GUARD=same_state" "${output}"

  rm -rf "${temp_dir}"
}

test_final_refresh_rejects_stale_context_without_mutation() {
  local temp_dir context_json curl_stub curl_log output rc
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"
  make_context_json "${context_json}" "In Review"
  write_curl_stub "${curl_stub}"

  set +e
  output="$(PATH="${temp_dir}:${PATH}" CURL_STUB_LOG="${curl_log}" CURL_STUB_CURRENT_STATE="In Progress" bash "${SCRIPT_PATH}" --context-json-file "${context_json}" --state "Ready for PR" --from-state "In Review" --linear-api-key test-key 2>&1)"
  rc=$?
  set -e

  [[ "${rc}" == "4" ]] || {
    echo "FAIL: Expected stale final state refresh to exit 4, got ${rc}" >&2
    printf 'Actual output:\n%s\n' "${output}" >&2
    exit 1
  }
  assert_contains "LINEAR_STATE_TRANSITION_GUARD=stale_state" "${output}"
  assert_contains "LINEAR_STATE_FROM=In Progress" "${output}"
  assert_contains "no mutation was attempted" "${output}"
  [[ ! -e "${curl_log}" ]] || {
    echo "FAIL: Stale state refresh unexpectedly mutated Linear." >&2
    exit 1
  }

  rm -rf "${temp_dir}"
}

test_final_refresh_treats_concurrent_target_as_success() {
  local temp_dir context_json curl_stub curl_log output
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"
  make_context_json "${context_json}" "In Review"
  write_curl_stub "${curl_stub}"

  output="$(PATH="${temp_dir}:${PATH}" CURL_STUB_LOG="${curl_log}" CURL_STUB_CURRENT_STATE="Ready for PR" bash "${SCRIPT_PATH}" --context-json-file "${context_json}" --state "Ready for PR" --from-state "In Review" --linear-api-key test-key)"

  assert_contains "LINEAR_STATE_STATUS=ok" "${output}"
  assert_contains "LINEAR_STATE_FROM=Ready for PR" "${output}"
  assert_contains "LINEAR_STATE_TRANSITION_GUARD=same_state" "${output}"
  [[ ! -e "${curl_log}" ]] || {
    echo "FAIL: Concurrent target state unexpectedly triggered a mutation." >&2
    exit 1
  }

  rm -rf "${temp_dir}"
}

test_workflow_override_requires_reason() {
  local temp_dir context_json output rc
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  make_context_json "${context_json}" "In Progress"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --context-json-file "${context_json}" \
    --state "Human Review Prep" \
    --allow-workflow-override \
    --linear-api-key test-key 2>&1)"
  rc=$?
  set -e

  [[ "${rc}" == "2" ]] || {
    echo "FAIL: Expected missing override reason to exit 2, got ${rc}" >&2
    exit 1
  }
  assert_contains "requires a non-empty --override-reason" "${output}"

  set +e
  output="$(bash "${SCRIPT_PATH}" \
    --context-json-file "${context_json}" \
    --state "Human Review Prep" \
    --override-reason "Manual recovery" \
    --linear-api-key test-key 2>&1)"
  rc=$?
  set -e

  [[ "${rc}" == "2" ]] || {
    echo "FAIL: Expected reason without override flag to exit 2, got ${rc}" >&2
    exit 1
  }
  assert_contains "requires --allow-workflow-override" "${output}"

  rm -rf "${temp_dir}"
}

test_explicit_workflow_override_is_logged_and_mutates() {
  local temp_dir context_json curl_stub curl_log output
  temp_dir="$(mktemp -d)"
  context_json="${temp_dir}/context.json"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"
  make_context_json "${context_json}" "In Progress"
  write_curl_stub "${curl_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" \
    CURL_STUB_LOG="${curl_log}" \
    CURL_STUB_CURRENT_STATE="In Progress" \
    bash "${SCRIPT_PATH}" \
      --context-json-file "${context_json}" \
      --state "Human Review Prep" \
      --from-state "In Progress" \
      --allow-workflow-override \
      --override-reason $'  Coordinator approved\nmanual recovery  ' \
      --linear-api-key test-key 2>&1
  )"

  assert_contains "WARNING: Symphony workflow transition override" "${output}"
  assert_contains "In Progress -> Human Review Prep" "${output}"
  assert_contains "LINEAR_STATE_STATUS=ok" "${output}"
  assert_contains "LINEAR_STATE_TRANSITION_GUARD=override" "${output}"
  assert_contains "LINEAR_STATE_OVERRIDE_REASON=Coordinator approved manual recovery" "${output}"
  assert_contains "\"stateId\":\"state-human-review-prep\"" "$(cat "${curl_log}")"

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
test_all_documented_workflow_edges_are_allowed
test_invalid_shortcuts_are_rejected_before_linear
test_all_undocumented_workflow_edges_are_rejected
test_allow_any_from_state_does_not_bypass_workflow_graph
test_same_state_preflight_is_allowed_without_mutation
test_same_state_preflight_ignores_stale_from_state
test_final_refresh_rejects_stale_context_without_mutation
test_final_refresh_treats_concurrent_target_as_success
test_workflow_override_requires_reason
test_explicit_workflow_override_is_logged_and_mutates

echo "symphony_linear_issue_state tests passed"
