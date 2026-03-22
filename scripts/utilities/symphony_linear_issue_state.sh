#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
# shellcheck source=../lib/symphony_linear_common.sh
source "${REPO_ROOT}/scripts/lib/symphony_linear_common.sh"

CONTEXT_HELPER="${REPO_ROOT}/scripts/utilities/symphony_linear_issue_context.sh"

usage() {
  cat <<'EOF'
Usage:
  symphony_linear_issue_state.sh --issue-identifier ISSUE --state "In Progress" [options]
  symphony_linear_issue_state.sh --issue-id ISSUE_ID --state "Blocked" [options]

Purpose:
  Move a Linear issue to a named workflow state using the issue's team workflow.

Why prefer this helper over raw GraphQL:
  - It resolves state ids from the issue's own team instead of guessing ids.
  - It gives Symphony a stable guardrail around allowed current-state checks.
  - It uses the same issue-context helper as the rest of the helper layer.

Options:
  --issue-identifier VALUE    Linear issue identifier such as ALL-123.
  --issue-id VALUE            Linear issue id; accepted as an alternative lookup key.
  --state VALUE               Required target state name.
  --from-state VALUE          Expected current state name before transition.
  --allow-any-from-state      Skip `--from-state` enforcement.
  --linear-api-key VALUE      Linear API key. Default: ~/.linear/api_key.txt.
  --context-json-file PATH    Testing/debug override: use a normalized context JSON file.
  --linear-json-file PATH     Testing/debug override forwarded to the context helper.
  --json-output-file PATH     Write a JSON summary of the operation to this path.
  --format VALUE              One of: env, json, pretty. Default: env.
  --help                      Show this help.

Known repo workflow states:
  - Todo
  - In Progress
  - Needs Review
  - In Review
  - Ready for PR
  - Human Review Prep
  - Human Review
  - Finalizing
  - Blocked
  - Done
  - Closed
  - Cancelled
  - Canceled
  - Duplicate

Notes:
  - Actual state availability still depends on the issue's team workflow in Linear.
  - Use `--from-state` when the transition should only happen from one known state.
  - Use `linear_graphql` only for unusual diagnostics outside routine transitions.

Output contract:
  LINEAR_STATE_STATUS=ok|error
  LINEAR_STATE_ISSUE_ID=...
  LINEAR_STATE_ISSUE_IDENTIFIER=...
  LINEAR_STATE_FROM=...
  LINEAR_STATE_TO=...
  LINEAR_STATE_TARGET_ID=...
  LINEAR_STATE_ERROR=...

Examples:
  bash scripts/utilities/symphony_linear_issue_state.sh \
    --issue-identifier ALL-123 \
    --state "In Progress" \
    --from-state "Todo"

  bash scripts/utilities/symphony_linear_issue_state.sh \
    --issue-identifier ALL-123 \
    --state "Blocked" \
    --allow-any-from-state

  bash scripts/utilities/symphony_linear_issue_state.sh \
    --issue-id 7f4d... \
    --state "Done" \
    --format pretty

Exit codes:
  0  Success.
  2  Invalid arguments.
  3  Linear request or response failure.
EOF
}

issue_identifier=""
issue_id=""
target_state=""
from_state=""
allow_any_from_state=0
linear_api_key=""
context_json_file=""
linear_json_file=""
json_output_file=""
format="env"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue-identifier)
      issue_identifier="${2:-}"
      shift 2
      ;;
    --issue-id)
      issue_id="${2:-}"
      shift 2
      ;;
    --state)
      target_state="${2:-}"
      shift 2
      ;;
    --from-state)
      from_state="${2:-}"
      shift 2
      ;;
    --allow-any-from-state)
      allow_any_from_state=1
      shift
      ;;
    --linear-api-key)
      linear_api_key="${2:-}"
      shift 2
      ;;
    --context-json-file)
      context_json_file="${2:-}"
      shift 2
      ;;
    --linear-json-file)
      linear_json_file="${2:-}"
      shift 2
      ;;
    --json-output-file)
      json_output_file="${2:-}"
      shift 2
      ;;
    --format)
      format="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${context_json_file}" && -z "${issue_identifier}" && -z "${issue_id}" ]]; then
  echo "Either --context-json-file or one of --issue-identifier/--issue-id is required." >&2
  exit 2
fi

if [[ -z "${target_state}" ]]; then
  echo "--state is required." >&2
  exit 2
fi

case "${format}" in
  env|json|pretty)
    ;;
  *)
    echo "--format must be one of: env, json, pretty" >&2
    exit 2
    ;;
esac

emit_payload() {
  local payload="$1"
  if [[ -n "${json_output_file}" ]]; then
    printf '%s\n' "${payload}" > "${json_output_file}"
  fi

  case "${format}" in
    json)
      printf '%s\n' "${payload}"
      ;;
    pretty)
      jq -r '
        [
          "Symphony Linear issue state result",
          "",
          "Status: \(.linear_state_status // "unknown")",
          "Issue: \(.linear_state_issue_identifier // "")",
          "From: \(.linear_state_from // "")",
          "To: \(.linear_state_to // "")",
          "Target state id: \(.linear_state_target_id // "")",
          "Error: \(.linear_state_error // "none")"
        ] | join("\n")
      ' <<< "${payload}"
      ;;
    env)
      jq -r '
        to_entries
        | map("\(.key|ascii_upcase)=\(.value // "")")
        | .[]
      ' <<< "${payload}"
      ;;
  esac
}

resolve_context_json_file() {
  if [[ -n "${context_json_file}" ]]; then
    printf '%s' "${context_json_file}"
    return 0
  fi

  local temp_json context_output
  local -a cmd
  temp_json="$(mktemp /tmp/symphony-state-context-XXXXXX.json)"
  cmd=(
    bash "${CONTEXT_HELPER}"
    --include-team-states
    --json-output-file "${temp_json}"
  )
  if [[ -n "${issue_identifier}" ]]; then
    cmd+=(--issue-identifier "${issue_identifier}")
  fi
  if [[ -n "${issue_id}" ]]; then
    cmd+=(--issue-id "${issue_id}")
  fi
  if [[ -n "${linear_json_file}" ]]; then
    cmd+=(--linear-json-file "${linear_json_file}")
  fi
  if [[ -n "${linear_api_key}" ]]; then
    cmd+=(--linear-api-key "${linear_api_key}")
  fi

  set +e
  context_output="$("${cmd[@]}" 2>&1)"
  local rc=$?
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    rm -f "${temp_json}"
    echo "${context_output}" >&2
    return 1
  fi

  printf '%s' "${temp_json}"
}

if ! linear_api_key="$(symphony_linear_read_api_key "${linear_api_key}")"; then
  payload="$(jq -cn \
    --arg status "error" \
    --arg error "No Linear API key found. Set --linear-api-key or create ~/.linear/api_key.txt" '
    {
      linear_state_status: $status,
      linear_state_error: $error
    }')"
  emit_payload "${payload}"
  exit 3
fi

context_json_path="$(resolve_context_json_file)" || exit 3
context_json="$(cat "${context_json_path}")"

current_state="$(jq -r '.issue.state.name // ""' <<< "${context_json}")"
resolved_issue_id="$(jq -r '.issue.id // ""' <<< "${context_json}")"
resolved_issue_identifier="$(jq -r '.issue.identifier // ""' <<< "${context_json}")"
target_state_id="$(jq -r --arg target_state "${target_state}" '
  .team.states[]
  | select(.name == $target_state)
  | .id
' <<< "${context_json}" | head -n 1)"

if [[ -z "${target_state_id}" ]]; then
  payload="$(jq -cn \
    --arg status "error" \
    --arg issue_id "${resolved_issue_id}" \
    --arg issue_identifier "${resolved_issue_identifier}" \
    --arg from "${current_state}" \
    --arg to "${target_state}" \
    --arg error "Target state not found in the issue team workflow." '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_error: $error
    }')"
  emit_payload "${payload}"
  exit 3
fi

if [[ "${allow_any_from_state}" -ne 1 && -n "${from_state}" && "${current_state}" != "${from_state}" ]]; then
  payload="$(jq -cn \
    --arg status "error" \
    --arg issue_id "${resolved_issue_id}" \
    --arg issue_identifier "${resolved_issue_identifier}" \
    --arg from "${current_state}" \
    --arg to "${target_state}" \
    --arg target_id "${target_state_id}" \
    --arg error "Current state does not match --from-state." '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id,
      linear_state_error: $error
    }')"
  emit_payload "${payload}"
  exit 3
fi

if [[ "${current_state}" == "${target_state}" ]]; then
  payload="$(jq -cn \
    --arg status "ok" \
    --arg issue_id "${resolved_issue_id}" \
    --arg issue_identifier "${resolved_issue_identifier}" \
    --arg from "${current_state}" \
    --arg to "${target_state}" \
    --arg target_id "${target_state_id}" '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id
    }')"
  emit_payload "${payload}"
  exit 0
fi

update_query='
mutation SymphonyUpdateIssueState($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: {stateId: $stateId}) {
    success
  }
}'

if ! mutation_json="$(symphony_linear_graphql \
  "${linear_api_key}" \
  "${update_query}" \
  "$(jq -cn --arg issueId "${resolved_issue_id}" --arg stateId "${target_state_id}" '{issueId: $issueId, stateId: $stateId}')")"; then
  payload="$(jq -cn \
    --arg status "error" \
    --arg issue_id "${resolved_issue_id}" \
    --arg issue_identifier "${resolved_issue_identifier}" \
    --arg from "${current_state}" \
    --arg to "${target_state}" \
    --arg target_id "${target_state_id}" \
    --arg error "Linear state update request failed." '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id,
      linear_state_error: $error
    }')"
  emit_payload "${payload}"
  exit 3
fi

response_error="$(symphony_linear_response_error "${mutation_json}")"
if [[ -n "${response_error}" ]]; then
  payload="$(jq -cn \
    --arg status "error" \
    --arg issue_id "${resolved_issue_id}" \
    --arg issue_identifier "${resolved_issue_identifier}" \
    --arg from "${current_state}" \
    --arg to "${target_state}" \
    --arg target_id "${target_state_id}" \
    --arg error "${response_error}" '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id,
      linear_state_error: $error
    }')"
  emit_payload "${payload}"
  exit 3
fi

if [[ "$(jq -r '.data.issueUpdate.success // false' <<< "${mutation_json}")" != "true" ]]; then
  payload="$(jq -cn \
    --arg status "error" \
    --arg issue_id "${resolved_issue_id}" \
    --arg issue_identifier "${resolved_issue_identifier}" \
    --arg from "${current_state}" \
    --arg to "${target_state}" \
    --arg target_id "${target_state_id}" \
    --arg error "Linear issueUpdate did not succeed." '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id,
      linear_state_error: $error
    }')"
  emit_payload "${payload}"
  exit 3
fi

payload="$(jq -cn \
  --arg status "ok" \
  --arg issue_id "${resolved_issue_id}" \
  --arg issue_identifier "${resolved_issue_identifier}" \
  --arg from "${current_state}" \
  --arg to "${target_state}" \
  --arg target_id "${target_state_id}" '
  {
    linear_state_status: $status,
    linear_state_issue_id: $issue_id,
    linear_state_issue_identifier: $issue_identifier,
    linear_state_from: $from,
    linear_state_to: $to,
    linear_state_target_id: $target_id
  }')"
emit_payload "${payload}"
