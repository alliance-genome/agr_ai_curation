#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

CONTEXT_HELPER="${SYMPHONY_FINALIZING_CONTEXT_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_context.sh}"
WORKPAD_HELPER="${SYMPHONY_FINALIZING_WORKPAD_HELPER:-${SCRIPT_DIR}/symphony_linear_workpad.sh}"
STATE_HELPER="${SYMPHONY_FINALIZING_STATE_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_state.sh}"
FINALIZE_HELPER="${SYMPHONY_FINALIZING_HELPER:-${SCRIPT_DIR}/symphony_finalize_issue.sh}"

usage() {
  cat <<'EOF'
Usage:
  symphony_finalizing_lane.sh --issue-identifier ISSUE [options]

Purpose:
  Deterministically handle the Symphony Finalizing lane without Codex.

Behavior:
  - Confirm the issue is still in Finalizing.
  - Run the canonical finalization helper.
  - Record a Finalization Summary in the workpad.
  - Move successful finalization to Done.
  - Move merge conflicts back to In Progress.
  - Move non-recoverable finalization failures to Blocked.

Options:
  --issue-identifier VALUE    Linear issue identifier such as ALL-123.
  --issue-id VALUE            Linear issue id; accepted by Linear helper calls.
  --workspace-dir PATH        Workspace checkout. Default: current directory.
  --delivery-mode VALUE       pr or no_pr. Default: inferred from workflow:no-pr label.
  --linear-api-key VALUE      Linear API key forwarded to Linear helper calls.
  --context-json-file PATH    Testing/debug override: use a normalized context JSON file.
  --linear-json-file PATH     Testing/debug override forwarded to context/workpad/state helpers.
  --context-helper PATH       Override context helper path.
  --workpad-helper PATH       Override workpad helper path.
  --state-helper PATH         Override state helper path.
  --finalize-helper PATH      Override finalization helper path.
  --json-output-file PATH     Write JSON summary to this path.
  --format VALUE              One of: env, json, pretty. Default: env.
  --help                      Show this help.

Output contract:
  FINALIZING_LANE_STATUS=done|returned_to_in_progress|blocked|noop|error
  FINALIZING_LANE_TO_STATE=Done|In Progress|Blocked|<current state>
  FINALIZING_LANE_REASON=...
EOF
}

issue_identifier=""
issue_id=""
workspace_dir="$PWD"
delivery_mode=""
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
    --workspace-dir)
      workspace_dir="${2:-}"
      shift 2
      ;;
    --delivery-mode)
      delivery_mode="${2:-}"
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
    --context-helper)
      CONTEXT_HELPER="${2:-}"
      shift 2
      ;;
    --workpad-helper)
      WORKPAD_HELPER="${2:-}"
      shift 2
      ;;
    --state-helper)
      STATE_HELPER="${2:-}"
      shift 2
      ;;
    --finalize-helper)
      FINALIZE_HELPER="${2:-}"
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

case "${format}" in
  env|json|pretty) ;;
  *)
    echo "--format must be one of: env, json, pretty" >&2
    exit 2
    ;;
esac

if [[ -z "${issue_identifier}" && -z "${issue_id}" ]]; then
  echo "One of --issue-identifier or --issue-id is required." >&2
  exit 2
fi

for helper in "${WORKPAD_HELPER}" "${STATE_HELPER}" "${FINALIZE_HELPER}"; do
  if [[ ! -x "${helper}" ]]; then
    echo "Required helper is missing or not executable: ${helper}" >&2
    exit 2
  fi
done
if [[ -z "${context_json_file}" && ! -x "${CONTEXT_HELPER}" ]]; then
  echo "Context helper is missing or not executable: ${CONTEXT_HELPER}" >&2
  exit 2
fi

workspace_dir="$(cd "${workspace_dir}" && pwd -P)"

helper_args_common=()

rebuild_helper_args_common() {
  helper_args_common=()
  if [[ -n "${issue_identifier}" ]]; then
    helper_args_common+=(--issue-identifier "${issue_identifier}")
  fi
  if [[ -n "${issue_id}" ]]; then
    helper_args_common+=(--issue-id "${issue_id}")
  fi
  if [[ -n "${linear_api_key}" ]]; then
    helper_args_common+=(--linear-api-key "${linear_api_key}")
  fi
  if [[ -n "${linear_json_file}" ]]; then
    helper_args_common+=(--linear-json-file "${linear_json_file}")
  fi
}

rebuild_helper_args_common

extract_kv() {
  local key="$1"
  local text="$2"

  printf '%s\n' "${text}" | awk -F= -v key="${key}" '
    $1 == key {
      sub(/^[^=]*=/, "")
      value = $0
    }
    END {
      if (value != "") {
        print value
      }
    }
  '
}

sanitize_value() {
  local value="${1-}"

  value="${value//$'\r'/ }"
  value="${value//$'\n'/ }"
  value="${value//\`/\'}"
  value="$(printf '%s' "${value}" | sed -E 's#([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@[:space:]]+@#\1REDACTED@#g')"
  value="$(printf '%s' "${value}" | sed -E 's#authorization:[[:space:]]*bearer[[:space:]]+[^[:space:]]+#authorization: REDACTED#Ig')"
  value="$(printf '%s' "${value}" | sed -E 's#(token|password|secret|authorization|api[_-]?key)=([^[:space:]]+)#\1=REDACTED#Ig')"
  value="$(printf '%s' "${value}" | sed -E 's#(token|password|secret|authorization|api[_-]?key):[[:space:]]*([^[:space:]]+)#\1: REDACTED#Ig')"
  value="$(printf '%s' "${value}" | sed -E 's#Bearer[[:space:]]+[^[:space:]]+#Bearer REDACTED#Ig')"

  printf '%s' "${value}"
}

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
          "Symphony Finalizing lane result",
          "",
          "Status: \(.finalizing_lane_status // "unknown")",
          "Issue: \(.finalizing_lane_issue_identifier // "")",
          "Delivery mode: \(.finalizing_lane_delivery_mode // "")",
          "Branch: \(.finalizing_lane_branch // "")",
          "To: \(.finalizing_lane_to_state // "")",
          "Reason: \(.finalizing_lane_reason // "none")"
        ] | join("\n")
      ' <<< "${payload}"
      ;;
    env)
      jq -r 'to_entries | map("\(.key|ascii_upcase)=\(.value // "")") | .[]' <<< "${payload}"
      ;;
  esac
}

build_payload() {
  local status="$1"
  local to_state="$2"
  local reason="$3"
  local finalize_status="${4:-}"
  local workpad_status="${5:-}"
  local state_status="${6:-}"

  jq -cn \
    --arg status "${status}" \
    --arg issue_identifier "${issue_identifier}" \
    --arg delivery_mode "${delivery_mode}" \
    --arg branch "${branch:-}" \
    --arg head_sha "${head_sha:-}" \
    --arg to_state "${to_state}" \
    --arg reason "${reason}" \
    --arg finalize_status "${finalize_status}" \
    --arg workpad_status "${workpad_status}" \
    --arg state_status "${state_status}" '
    {
      finalizing_lane_status: $status,
      finalizing_lane_issue_identifier: $issue_identifier,
      finalizing_lane_delivery_mode: $delivery_mode,
      finalizing_lane_branch: $branch,
      finalizing_lane_head_sha: $head_sha,
      finalizing_lane_to_state: $to_state,
      finalizing_lane_reason: $reason,
      finalizing_lane_finalize_status: $finalize_status,
      finalizing_lane_workpad_status: $workpad_status,
      finalizing_lane_state_status: $state_status
    }'
}

resolve_context_json_file() {
  if [[ -n "${context_json_file}" ]]; then
    printf '%s' "${context_json_file}"
    return 0
  fi

  local temp_json context_output rc
  temp_json="$(mktemp "${TMPDIR:-/tmp}/symphony-finalizing-context-XXXXXX.json")"
  set +e
  context_output="$(bash "${CONTEXT_HELPER}" \
    --json-output-file "${temp_json}" \
    --include-team-states \
    "${helper_args_common[@]}" 2>&1)"
  rc=$?
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    rm -f "${temp_json}"
    echo "${context_output}" >&2
    return 1
  fi

  printf '%s' "${temp_json}"
}

current_issue_state() {
  jq -r '.issue.state.name // ""' "${context_json_path}"
}

context_issue_identifier() {
  jq -r '.issue.identifier // ""' "${context_json_path}"
}

infer_delivery_mode() {
  if jq -e '
    [
      .issue.labels[]?
      | if type == "object" then (.name // "") else . end
      | ascii_downcase
    ]
    | index("workflow:no-pr")
  ' "${context_json_path}" >/dev/null; then
    printf 'no_pr'
  else
    printf 'pr'
  fi
}

branch_name() {
  git -C "${workspace_dir}" rev-parse --abbrev-ref HEAD 2>/dev/null || true
}

branch_head_sha() {
  git -C "${workspace_dir}" rev-parse HEAD 2>/dev/null || true
}

append_finalization_summary() {
  local section_file="$1"
  local output rc

  set +e
  output="$(bash "${WORKPAD_HELPER}" append-section \
    "${helper_args_common[@]}" \
    --section-title "Finalization Summary" \
    --section-file "${section_file}" 2>&1)"
  rc=$?
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    echo "${output}" >&2
    return "${rc}"
  fi

  extract_kv "WORKPAD_STATUS" "${output}"
}

move_issue_state() {
  local target_state="$1"
  local output rc

  set +e
  output="$(bash "${STATE_HELPER}" \
    "${helper_args_common[@]}" \
    --state "${target_state}" \
    --from-state "Finalizing" 2>&1)"
  rc=$?
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    echo "${output}" >&2
    return "${rc}"
  fi

  extract_kv "LINEAR_STATE_STATUS" "${output}"
}

write_summary_section() {
  local section_file="$1"
  local outcome="$2"
  local next_state="$3"
  local reason="$4"
  local finalize_output="$5"
  local key value
  local keys=(
    FINALIZE_STATUS
    FINALIZE_NEXT_STATE
    FINALIZE_DELIVERY_MODE
    FINALIZE_BRANCH
    FINALIZE_PR_NUMBER
    FINALIZE_PR_URL
    FINALIZE_PRE_CLEANUP_STATUS
    FINALIZE_POST_CLEANUP_STATUS
    FINALIZE_WORKSPACE_REMOVAL
    FINALIZE_MERGE_STATUS
    FINALIZE_MESSAGE
    FINALIZE_MERGE_OUTPUT
    CONFLICT_FILES
    CONFLICT_SIBLING_TICKETS
    CONFLICT_BASE_REF
  )

  {
    echo "- Outcome: ${outcome}"
    echo "- Branch: \`${branch:-unknown}\`"
    echo "- Head SHA: \`${head_sha:-unknown}\`"
    echo "- Delivery mode: \`${delivery_mode:-unknown}\`"
    echo "- Reason: ${reason}"
    echo "- Next state: ${next_state}"
    echo
    echo "### Finalizer Output"
    for key in "${keys[@]}"; do
      value="$(extract_kv "${key}" "${finalize_output}")"
      if [[ -n "${value}" ]]; then
        echo "- ${key}: \`$(sanitize_value "${value}")\`"
      fi
    done
  } > "${section_file}"
}

run_finalize_helper() {
  local -a cmd

  cmd=(bash "${FINALIZE_HELPER}"
    --delivery-mode "${delivery_mode}"
    --workspace-dir "${workspace_dir}"
    --compose-project "$(basename "${workspace_dir}" | tr '[:upper:]' '[:lower:]')")

  if [[ -n "${issue_identifier}" ]]; then
    cmd+=(--issue-identifier "${issue_identifier}")
  fi

  (
    cd "${workspace_dir}"
    "${cmd[@]}"
  )
}

context_json_path="$(resolve_context_json_file)" || exit 3
if [[ -z "${issue_identifier}" ]]; then
  issue_identifier="$(context_issue_identifier)"
  rebuild_helper_args_common
fi

if [[ -z "${issue_identifier}" ]]; then
  echo "Could not resolve issue identifier from context." >&2
  exit 3
fi

current_state="$(current_issue_state)"
if [[ "${current_state}" != "Finalizing" ]]; then
  payload="$(build_payload "noop" "${current_state:-unknown}" "state_changed_before_finalizing_lane")"
  emit_payload "${payload}"
  exit 0
fi

if [[ -z "${delivery_mode}" ]]; then
  delivery_mode="$(infer_delivery_mode)"
fi

branch="$(branch_name)"
head_sha="$(branch_head_sha)"

set +e
finalize_output="$(run_finalize_helper 2>&1)"
finalize_rc=$?
set -e

printf '%s\n' "${finalize_output}" >&2

finalize_status="$(extract_kv "FINALIZE_STATUS" "${finalize_output}")"
finalize_next_state="$(extract_kv "FINALIZE_NEXT_STATE" "${finalize_output}")"
section_file="$(mktemp "${TMPDIR:-/tmp}/symphony-finalizing-summary-XXXXXX.md")"

case "${finalize_status}:${finalize_next_state}" in
  merged:Done|finalized_no_pr:Done|dry_run:Done)
    write_summary_section "${section_file}" "Finalization completed." "Done" "${finalize_status}" "${finalize_output}"
    workpad_status="$(append_finalization_summary "${section_file}")" || exit 3
    state_status="$(move_issue_state "Done")" || exit 3
    payload="$(build_payload "done" "Done" "${finalize_status}" "${finalize_status}" "${workpad_status}" "${state_status}")"
    emit_payload "${payload}"
    ;;
  merge_conflict:In\ Progress)
    write_summary_section "${section_file}" "Finalization found merge conflicts." "In Progress" "merge_conflict" "${finalize_output}"
    workpad_status="$(append_finalization_summary "${section_file}")" || exit 3
    state_status="$(move_issue_state "In Progress")" || exit 3
    payload="$(build_payload "returned_to_in_progress" "In Progress" "merge_conflict" "${finalize_status}" "${workpad_status}" "${state_status}")"
    emit_payload "${payload}"
    ;;
  blocked_missing_pr:Blocked|blocked_merge_failed:Blocked)
    write_summary_section "${section_file}" "Finalization could not complete safely." "Blocked" "${finalize_status}" "${finalize_output}"
    workpad_status="$(append_finalization_summary "${section_file}")" || exit 3
    state_status="$(move_issue_state "Blocked")" || exit 3
    payload="$(build_payload "blocked" "Blocked" "${finalize_status}" "${finalize_status}" "${workpad_status}" "${state_status}")"
    emit_payload "${payload}"
    ;;
  *)
    echo "Finalization helper returned unexpected status/next-state: status=${finalize_status:-unknown} next=${finalize_next_state:-unknown} rc=${finalize_rc}" >&2
    printf '%s\n' "${finalize_output}" >&2
    exit 3
    ;;
esac
