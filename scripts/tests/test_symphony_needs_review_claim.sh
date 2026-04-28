#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_needs_review_claim.sh"

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
    echo "FAIL: Expected output not to contain '${unexpected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

make_workspace() {
  local workspace="$1"
  mkdir -p "${workspace}"
  git -C "${workspace}" init -b all-123 >/dev/null
  git -C "${workspace}" config user.name "Test User"
  git -C "${workspace}" config user.email "test@example.com"
  printf '# test\n' > "${workspace}/README.md"
  git -C "${workspace}" add README.md
  git -C "${workspace}" commit -m "initial" >/dev/null
}

make_context_json() {
  local path="$1"
  local workpad_body="$2"
  local current_state="${3:-Needs Review}"

  jq -cn \
    --arg body "${workpad_body}" \
    --arg current_state "${current_state}" '
    {
      issue: {
        id: "issue-123",
        identifier: "ALL-123",
        title: "Example issue",
        description: "Description.",
        url: "https://linear.app/test/issue/ALL-123",
        state: {id: "state-needs-review", name: $current_state, type: "started"}
      },
      comments_count: 1,
      comments: [
        {
          id: "workpad-1",
          body: $body,
          created_at: "2026-04-28T00:00:00Z",
          updated_at: "2026-04-28T00:01:00Z",
          user_name: "Codex",
          is_workpad: true
        }
      ],
      workpad_comment: {
        id: "workpad-1",
        body: $body,
        created_at: "2026-04-28T00:00:00Z",
        updated_at: "2026-04-28T00:01:00Z",
        user_name: "Codex",
        is_workpad: true
      },
      workpad_comments: [],
      duplicate_workpad_count: 0,
      latest_non_workpad_comment: null,
      history: [],
      team: {
        id: "team-1",
        name: "Alliance",
        key: "ALL",
        states: [
          {id: "state-progress", name: "In Progress", type: "started", position: 2},
          {id: "state-needs-review", name: "Needs Review", type: "started", position: 3},
          {id: "state-in-review", name: "In Review", type: "started", position: 4},
          {id: "state-blocked", name: "Blocked", type: "backlog", position: 5}
        ]
      }
    }' > "${path}"
}

write_curl_stub() {
  local stub="$1"
  cat > "${stub}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
payload=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data|-d)
      payload="${2:-}"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
printf '%s\n' "${payload}" >> "${CURL_STUB_LOG}"
if jq -e '.query | contains("commentUpdate")' <<< "${payload}" >/dev/null; then
  echo '{"data":{"commentUpdate":{"success":true,"comment":{"id":"workpad-1","body":"","updatedAt":"2026-04-28T00:02:00Z"}}}}'
elif jq -e '.query | contains("issueUpdate")' <<< "${payload}" >/dev/null; then
  echo '{"data":{"issueUpdate":{"success":true}}}'
else
  echo '{"data":{}}'
fi
EOF
  chmod +x "${stub}"
}

test_help_describes_claim_lane() {
  local output
  output="$(bash "${SCRIPT_PATH}" --help)"
  assert_contains "claim-only lane" "${output}"
  assert_contains "Review Claim" "${output}"
}

test_claims_clean_handoff_to_in_review() {
  local temp_dir workspace context_json curl_stub curl_log output
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  context_json="${temp_dir}/context.json"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"

  make_workspace "${workspace}"
  make_context_json "${context_json}" '<!-- symphony-workpad:v1 issue:ALL-123 -->

# Symphony Workpad

## Review Handoff

- Outcome: Implemented prompt update.
- Reviewer focus: Verify prompt mirrors stay identical.'
  write_curl_stub "${curl_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" \
    CURL_STUB_LOG="${curl_log}" \
    bash "${SCRIPT_PATH}" \
      --issue-identifier ALL-123 \
      --workspace-dir "${workspace}" \
      --context-json-file "${context_json}" \
      --linear-api-key test-key
  )"

  assert_contains "NEEDS_REVIEW_CLAIM_STATUS=claimed" "${output}"
  assert_contains "NEEDS_REVIEW_CLAIM_TO_STATE=In Review" "${output}"
  assert_contains "NEEDS_REVIEW_CLAIM_HANDOFF_FOUND=1" "${output}"
  assert_not_contains $'\nWORKPAD_STATUS=' "${output}"
  assert_not_contains $'\nLINEAR_STATE_STATUS=' "${output}"
  assert_contains "\"stateId\":\"state-in-review\"" "$(cat "${curl_log}")"
  assert_contains "Review Claim" "$(cat "${curl_log}")"

  rm -rf "${temp_dir}"
}

test_missing_handoff_returns_to_in_progress() {
  local temp_dir workspace context_json curl_stub curl_log output
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  context_json="${temp_dir}/context.json"
  curl_stub="${temp_dir}/curl"
  curl_log="${temp_dir}/curl.log"

  make_workspace "${workspace}"
  make_context_json "${context_json}" '<!-- symphony-workpad:v1 issue:ALL-123 -->

# Symphony Workpad

## Todo Handoff

- Outcome: Intake done.'
  write_curl_stub "${curl_stub}"

  output="$(
    PATH="${temp_dir}:${PATH}" \
    CURL_STUB_LOG="${curl_log}" \
    bash "${SCRIPT_PATH}" \
      --issue-identifier ALL-123 \
      --workspace-dir "${workspace}" \
      --context-json-file "${context_json}" \
      --linear-api-key test-key
  )"

  assert_contains "NEEDS_REVIEW_CLAIM_STATUS=returned_to_in_progress" "${output}"
  assert_contains "NEEDS_REVIEW_CLAIM_TO_STATE=In Progress" "${output}"
  assert_contains "NEEDS_REVIEW_CLAIM_REASON=missing_review_handoff" "${output}"
  assert_contains "\"stateId\":\"state-progress\"" "$(cat "${curl_log}")"

  rm -rf "${temp_dir}"
}

test_help_describes_claim_lane
test_claims_clean_handoff_to_in_review
test_missing_handoff_returns_to_in_progress

echo "symphony_needs_review_claim tests passed"
