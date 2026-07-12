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
                              This does not bypass the workflow transition graph.
  --allow-workflow-override   Permit an edge outside the workflow transition graph.
                              Requires --override-reason and emits a warning.
  --override-reason VALUE     Human-readable reason for an intentional workflow override.
  --linear-api-key VALUE      Linear API key. Default: LINEAR_API_KEY or ~/.linear/api_key.txt.
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
  - Same-state requests are allowed as no-op preflight checks.
  - Routine lane changes must follow the Symphony workflow graph documented below.
  - `--allow-any-from-state` does not permit an otherwise invalid workflow edge.
  - Emergency or administrative edges require both `--allow-workflow-override`
    and `--override-reason`; the helper records the override in its output.
  - Use `linear_graphql` only for unusual diagnostics outside routine transitions.

Routine workflow edges:
  Todo -> In Progress | Blocked
  In Progress -> Needs Review | Blocked
  Needs Review -> In Review | In Progress | Blocked
  In Review -> Ready for PR | Human Review Prep | In Progress | Blocked
  Ready for PR -> Human Review Prep | In Progress | Blocked
  Human Review Prep -> Human Review | In Progress | Blocked
  Human Review -> Finalizing | In Progress
  Finalizing -> Done | In Progress | Blocked
  Blocked -> In Progress

Output contract:
  LINEAR_STATE_STATUS=ok|error
  LINEAR_STATE_ISSUE_ID=...
  LINEAR_STATE_ISSUE_IDENTIFIER=...
  LINEAR_STATE_FROM=...
  LINEAR_STATE_TO=...
  LINEAR_STATE_TARGET_ID=...
  LINEAR_STATE_TRANSITION_GUARD=allowed|same_state|override|rejected|stale_state
  LINEAR_STATE_ALLOWED_TARGETS=...
  LINEAR_STATE_OVERRIDE_REASON=...
  LINEAR_STATE_ERROR=...

Examples:
  bash scripts/utilities/symphony_linear_issue_state.sh \
    --issue-identifier ALL-123 \
    --state "In Progress" \
    --from-state "Todo"

  bash scripts/utilities/symphony_linear_issue_state.sh \
    --issue-identifier ALL-123 \
    --state "Blocked" \
    --from-state "In Progress"

  bash scripts/utilities/symphony_linear_issue_state.sh \
    --issue-identifier ALL-123 \
    --state "Canceled" \
    --allow-any-from-state \
    --allow-workflow-override \
    --override-reason "Coordinator canceled obsolete work after reconciliation"

  bash scripts/utilities/symphony_linear_issue_state.sh \
    --issue-id 7f4d... \
    --state "Done" \
    --from-state "Finalizing" \
    --format pretty

Exit codes:
  0  Success.
  2  Invalid arguments.
  3  Linear request or response failure.
  4  Workflow transition rejected before any Linear mutation.
EOF
}

issue_identifier=""
issue_id=""
target_state=""
from_state=""
allow_any_from_state=0
allow_workflow_override=0
override_reason=""
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
    --allow-workflow-override)
      allow_workflow_override=1
      shift
      ;;
    --override-reason)
      override_reason="${2:-}"
      shift 2
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

override_reason="$(printf '%s' "${override_reason}" | tr '\r\n\t' '   ' | awk '{$1=$1; print}')"
if [[ "${allow_workflow_override}" -eq 1 && -z "${override_reason}" ]]; then
  echo "--allow-workflow-override requires a non-empty --override-reason." >&2
  exit 2
fi
if [[ "${allow_workflow_override}" -ne 1 && -n "${override_reason}" ]]; then
  echo "--override-reason requires --allow-workflow-override." >&2
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
          "Transition guard: \(.linear_state_transition_guard // "unknown")",
          "Allowed targets: \(.linear_state_allowed_targets // "")",
          "Override reason: \(.linear_state_override_reason // "")",
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

workflow_allowed_targets() {
  case "$1" in
    "Todo")
      printf '%s' "In Progress, Blocked"
      ;;
    "In Progress")
      printf '%s' "Needs Review, Blocked"
      ;;
    "Needs Review")
      printf '%s' "In Review, In Progress, Blocked"
      ;;
    "In Review")
      printf '%s' "Ready for PR, Human Review Prep, In Progress, Blocked"
      ;;
    "Ready for PR")
      printf '%s' "Human Review Prep, In Progress, Blocked"
      ;;
    "Human Review Prep")
      printf '%s' "Human Review, In Progress, Blocked"
      ;;
    "Human Review")
      printf '%s' "Finalizing, In Progress"
      ;;
    "Finalizing")
      printf '%s' "Done, In Progress, Blocked"
      ;;
    "Blocked")
      printf '%s' "In Progress"
      ;;
    *)
      printf '%s' "none"
      return 1
      ;;
  esac
}

workflow_transition_allowed() {
  case "$1 -> $2" in
    "Todo -> In Progress"|\
    "Todo -> Blocked"|\
    "In Progress -> Needs Review"|\
    "In Progress -> Blocked"|\
    "Needs Review -> In Review"|\
    "Needs Review -> In Progress"|\
    "Needs Review -> Blocked"|\
    "In Review -> Ready for PR"|\
    "In Review -> Human Review Prep"|\
    "In Review -> In Progress"|\
    "In Review -> Blocked"|\
    "Ready for PR -> Human Review Prep"|\
    "Ready for PR -> In Progress"|\
    "Ready for PR -> Blocked"|\
    "Human Review Prep -> Human Review"|\
    "Human Review Prep -> In Progress"|\
    "Human Review Prep -> Blocked"|\
    "Human Review -> Finalizing"|\
    "Human Review -> In Progress"|\
    "Finalizing -> Done"|\
    "Finalizing -> In Progress"|\
    "Finalizing -> Blocked"|\
    "Blocked -> In Progress")
      return 0
      ;;
    *)
      return 1
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
    --arg error "No Linear API key found. Set --linear-api-key, export LINEAR_API_KEY, or run bash scripts/utilities/symphony_materialize_linear_auth.sh." '
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

if [[ "${current_state}" == "${target_state}" ]]; then
  payload="$(jq -cn \
    --arg status "ok" \
    --arg issue_id "${resolved_issue_id}" \
    --arg issue_identifier "${resolved_issue_identifier}" \
    --arg from "${current_state}" \
    --arg to "${target_state}" \
    --arg target_id "${target_state_id}" \
    --arg guard "same_state" '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id,
      linear_state_transition_guard: $guard
    }')"
  emit_payload "${payload}"
  exit 0
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

transition_guard="allowed"
allowed_targets="$(workflow_allowed_targets "${current_state}" || true)"
if ! workflow_transition_allowed "${current_state}" "${target_state}"; then
  if [[ "${allow_workflow_override}" -eq 1 ]]; then
    transition_guard="override"
  else
    payload="$(jq -cn \
      --arg status "error" \
      --arg issue_id "${resolved_issue_id}" \
      --arg issue_identifier "${resolved_issue_identifier}" \
      --arg from "${current_state}" \
      --arg to "${target_state}" \
      --arg target_id "${target_state_id}" \
      --arg guard "rejected" \
      --arg allowed_targets "${allowed_targets}" \
      --arg error "Transition is not allowed by the Symphony workflow graph. Use --allow-workflow-override with --override-reason only for an intentional emergency or administrative transition." '
      {
        linear_state_status: $status,
        linear_state_issue_id: $issue_id,
        linear_state_issue_identifier: $issue_identifier,
        linear_state_from: $from,
        linear_state_to: $to,
        linear_state_target_id: $target_id,
        linear_state_transition_guard: $guard,
        linear_state_allowed_targets: $allowed_targets,
        linear_state_error: $error
      }')"
    emit_payload "${payload}"
    exit 4
  fi
fi

refresh_query='
query SymphonyRefreshIssueState($issueId: String!) {
  issue(id: $issueId) {
    state {
      name
    }
  }
}'

if ! refresh_json="$(symphony_linear_graphql \
  "${linear_api_key}" \
  "${refresh_query}" \
  "$(jq -cn --arg issueId "${resolved_issue_id}" '{issueId: $issueId}')")"; then
  payload="$(jq -cn \
    --arg status "error" \
    --arg issue_id "${resolved_issue_id}" \
    --arg issue_identifier "${resolved_issue_identifier}" \
    --arg from "${current_state}" \
    --arg to "${target_state}" \
    --arg target_id "${target_state_id}" \
    --arg guard "${transition_guard}" \
    --arg error "Final Linear state refresh failed; no mutation was attempted." '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id,
      linear_state_transition_guard: $guard,
      linear_state_error: $error
    }')"
  emit_payload "${payload}"
  exit 3
fi

refresh_error="$(symphony_linear_response_error "${refresh_json}")"
refreshed_state="$(jq -r '.data.issue.state.name // ""' <<< "${refresh_json}")"
if [[ -n "${refresh_error}" || -z "${refreshed_state}" ]]; then
  payload="$(jq -cn \
    --arg status "error" \
    --arg issue_id "${resolved_issue_id}" \
    --arg issue_identifier "${resolved_issue_identifier}" \
    --arg from "${current_state}" \
    --arg to "${target_state}" \
    --arg target_id "${target_state_id}" \
    --arg guard "${transition_guard}" \
    --arg error "${refresh_error:-Final Linear state refresh did not return the current state; no mutation was attempted.}" '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id,
      linear_state_transition_guard: $guard,
      linear_state_error: $error
    }')"
  emit_payload "${payload}"
  exit 3
fi

if [[ "${refreshed_state}" != "${current_state}" ]]; then
  if [[ "${refreshed_state}" == "${target_state}" ]]; then
    payload="$(jq -cn \
      --arg status "ok" \
      --arg issue_id "${resolved_issue_id}" \
      --arg issue_identifier "${resolved_issue_identifier}" \
      --arg from "${refreshed_state}" \
      --arg to "${target_state}" \
      --arg target_id "${target_state_id}" \
      --arg guard "same_state" '
      {
        linear_state_status: $status,
        linear_state_issue_id: $issue_id,
        linear_state_issue_identifier: $issue_identifier,
        linear_state_from: $from,
        linear_state_to: $to,
        linear_state_target_id: $target_id,
        linear_state_transition_guard: $guard
      }')"
    emit_payload "${payload}"
    exit 0
  fi

  allowed_targets="$(workflow_allowed_targets "${refreshed_state}" || true)"
  payload="$(jq -cn \
    --arg status "error" \
    --arg issue_id "${resolved_issue_id}" \
    --arg issue_identifier "${resolved_issue_identifier}" \
    --arg from "${refreshed_state}" \
    --arg to "${target_state}" \
    --arg target_id "${target_state_id}" \
    --arg guard "stale_state" \
    --arg allowed_targets "${allowed_targets}" \
    --arg error "Current state changed after workflow validation; no mutation was attempted. Rerun from fresh issue context." '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id,
      linear_state_transition_guard: $guard,
      linear_state_allowed_targets: $allowed_targets,
      linear_state_error: $error
    }')"
  emit_payload "${payload}"
  exit 4
fi

if [[ "${transition_guard}" == "override" ]]; then
  printf 'WARNING: Symphony workflow transition override for %s: %s -> %s. Reason: %s\n' \
    "${resolved_issue_identifier:-${resolved_issue_id}}" \
    "${current_state}" \
    "${target_state}" \
    "${override_reason}" >&2
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
    --arg guard "${transition_guard}" \
    --arg override_reason "${override_reason}" \
    --arg error "Linear state update request failed." '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id,
      linear_state_transition_guard: $guard,
      linear_state_override_reason: (if $guard == "override" then $override_reason else null end),
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
    --arg guard "${transition_guard}" \
    --arg override_reason "${override_reason}" \
    --arg error "${response_error}" '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id,
      linear_state_transition_guard: $guard,
      linear_state_override_reason: (if $guard == "override" then $override_reason else null end),
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
    --arg guard "${transition_guard}" \
    --arg override_reason "${override_reason}" \
    --arg error "Linear issueUpdate did not succeed." '
    {
      linear_state_status: $status,
      linear_state_issue_id: $issue_id,
      linear_state_issue_identifier: $issue_identifier,
      linear_state_from: $from,
      linear_state_to: $to,
      linear_state_target_id: $target_id,
      linear_state_transition_guard: $guard,
      linear_state_override_reason: (if $guard == "override" then $override_reason else null end),
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
  --arg target_id "${target_state_id}" \
  --arg guard "${transition_guard}" \
  --arg override_reason "${override_reason}" '
  {
    linear_state_status: $status,
    linear_state_issue_id: $issue_id,
    linear_state_issue_identifier: $issue_identifier,
    linear_state_from: $from,
    linear_state_to: $to,
    linear_state_target_id: $target_id,
    linear_state_transition_guard: $guard,
    linear_state_override_reason: (if $guard == "override" then $override_reason else null end)
  }')"
emit_payload "${payload}"
