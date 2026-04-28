#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

CONTEXT_HELPER="${SYMPHONY_RFP_CONTEXT_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_context.sh}"
WORKPAD_HELPER="${SYMPHONY_RFP_WORKPAD_HELPER:-${SCRIPT_DIR}/symphony_linear_workpad.sh}"
STATE_HELPER="${SYMPHONY_RFP_STATE_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_state.sh}"
READY_HELPER="${SYMPHONY_RFP_READY_HELPER:-${SCRIPT_DIR}/symphony_ready_for_pr.sh}"
GUARD_HELPER="${SYMPHONY_RFP_GUARD_HELPER:-${SCRIPT_DIR}/symphony_guard_no_code_changes.sh}"

usage() {
  cat <<'EOF'
Usage:
  symphony_ready_for_pr_lane.sh --issue-identifier ISSUE [options]

Purpose:
  Deterministically handle the Symphony Ready for PR lane without Codex.

Behavior:
  - Verify the workspace is clean for a no-code lane.
  - Run the canonical Ready for PR helper with GitHub checks before Claude review.
  - Let the helper auto-bounce failed checks or Claude feedback to In Progress.
  - Write PR Handoff and move clean PRs to Human Review Prep.
  - Keep pending checks/reviews in Ready for PR for a later rerun.

Options:
  --issue-identifier VALUE       Linear issue identifier such as ALL-123.
  --issue-id VALUE               Linear issue id; accepted by helper calls.
  --workspace-dir PATH           Workspace checkout. Default: current directory.
  --delivery-mode VALUE          pr or no_pr. Default: inferred from labels.
  --wait-for-review-seconds N    Default: 300.
  --review-poll-seconds N        Default: 30.
  --wait-for-checks-seconds N    Default: 600.
  --check-poll-seconds N         Default: 30.
  --linear-api-key VALUE         Linear API key forwarded to helper calls.
  --context-json-file PATH       Testing/debug override: use a normalized context JSON file.
  --linear-json-file PATH        Testing/debug override forwarded to context/workpad/state helpers.
  --context-helper PATH          Override context helper path.
  --workpad-helper PATH          Override workpad helper path.
  --state-helper PATH            Override state helper path.
  --ready-helper PATH            Override Ready for PR helper path.
  --guard-helper PATH            Override no-code guard helper path.
  --json-output-file PATH        Write JSON summary to this path.
  --format VALUE                 One of: env, json, pretty. Default: env.
  --help                         Show this help.

Output contract:
  READY_FOR_PR_LANE_STATUS=ready|bounced_to_in_progress|waiting|returned_to_in_progress|noop|error
  READY_FOR_PR_LANE_TO_STATE=Human Review Prep|In Progress|Ready for PR|<current state>
  READY_FOR_PR_LANE_REASON=...
EOF
}

issue_identifier=""
issue_id=""
workspace_dir="$PWD"
delivery_mode=""
wait_for_review_seconds=300
review_poll_seconds=30
wait_for_checks_seconds=600
check_poll_seconds=30
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
    --wait-for-review-seconds)
      wait_for_review_seconds="${2:-}"
      shift 2
      ;;
    --review-poll-seconds)
      review_poll_seconds="${2:-}"
      shift 2
      ;;
    --wait-for-checks-seconds)
      wait_for_checks_seconds="${2:-}"
      shift 2
      ;;
    --check-poll-seconds)
      check_poll_seconds="${2:-}"
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
    --ready-helper)
      READY_HELPER="${2:-}"
      shift 2
      ;;
    --guard-helper)
      GUARD_HELPER="${2:-}"
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

for helper in "${WORKPAD_HELPER}" "${STATE_HELPER}" "${READY_HELPER}" "${GUARD_HELPER}"; do
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
snapshot_file="$(mktemp "${TMPDIR:-/tmp}/symphony-rfp-guard-snapshot-XXXXXX.env")"

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
  value="$(printf '%s' "${value}" | sed -E 's#(token|password|secret|authorization|api[_-]?key)=([^[:space:]]+)#\1=REDACTED#Ig')"
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
          "Symphony Ready for PR lane result",
          "",
          "Status: \(.ready_for_pr_lane_status // "unknown")",
          "Issue: \(.ready_for_pr_lane_issue_identifier // "")",
          "Branch: \(.ready_for_pr_lane_branch // "")",
          "To: \(.ready_for_pr_lane_to_state // "")",
          "Reason: \(.ready_for_pr_lane_reason // "none")"
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
  local ready_status="${4:-}"
  local check_status="${5:-}"
  local claude_status="${6:-}"
  local workpad_status="${7:-}"
  local state_status="${8:-}"

  jq -cn \
    --arg status "${status}" \
    --arg issue_identifier "${issue_identifier}" \
    --arg delivery_mode "${delivery_mode}" \
    --arg branch "${branch:-}" \
    --arg head_sha "${head_sha:-}" \
    --arg to_state "${to_state}" \
    --arg reason "${reason}" \
    --arg ready_status "${ready_status}" \
    --arg check_status "${check_status}" \
    --arg claude_status "${claude_status}" \
    --arg workpad_status "${workpad_status}" \
    --arg state_status "${state_status}" '
    {
      ready_for_pr_lane_status: $status,
      ready_for_pr_lane_issue_identifier: $issue_identifier,
      ready_for_pr_lane_delivery_mode: $delivery_mode,
      ready_for_pr_lane_branch: $branch,
      ready_for_pr_lane_head_sha: $head_sha,
      ready_for_pr_lane_to_state: $to_state,
      ready_for_pr_lane_reason: $reason,
      ready_for_pr_lane_ready_status: $ready_status,
      ready_for_pr_lane_check_status: $check_status,
      ready_for_pr_lane_claude_status: $claude_status,
      ready_for_pr_lane_workpad_status: $workpad_status,
      ready_for_pr_lane_state_status: $state_status
    }'
}

resolve_context_json_file() {
  if [[ -n "${context_json_file}" ]]; then
    printf '%s' "${context_json_file}"
    return 0
  fi

  local temp_json context_output rc
  temp_json="$(mktemp "${TMPDIR:-/tmp}/symphony-rfp-context-XXXXXX.json")"
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
  if jq -e '[.issue.labels[]? | ascii_downcase] | index("workflow:no-pr")' "${context_json_path}" >/dev/null; then
    printf 'no_pr'
  else
    printf 'pr'
  fi
}

issue_title() {
  jq -r '.issue.title // ""' "${context_json_path}"
}

issue_url() {
  jq -r '.issue.url // ""' "${context_json_path}"
}

branch_name() {
  git -C "${workspace_dir}" rev-parse --abbrev-ref HEAD 2>/dev/null || true
}

branch_head_sha() {
  git -C "${workspace_dir}" rev-parse HEAD 2>/dev/null || true
}

run_guard_snapshot() {
  bash "${GUARD_HELPER}" snapshot \
    --workspace-dir "${workspace_dir}" \
    --state "Ready for PR" \
    --issue-identifier "${issue_identifier}" \
    --snapshot-file "${snapshot_file}" >/dev/null
}

run_guard_verify() {
  bash "${GUARD_HELPER}" verify \
    --workspace-dir "${workspace_dir}" \
    --state "Ready for PR" \
    --issue-identifier "${issue_identifier}" \
    --snapshot-file "${snapshot_file}" \
    --check-head >/dev/null
}

run_guard_snapshot_captured() {
  bash "${GUARD_HELPER}" snapshot \
    --workspace-dir "${workspace_dir}" \
    --state "Ready for PR" \
    --issue-identifier "${issue_identifier}" \
    --snapshot-file "${snapshot_file}" 2>&1
}

run_guard_verify_captured() {
  bash "${GUARD_HELPER}" verify \
    --workspace-dir "${workspace_dir}" \
    --state "Ready for PR" \
    --issue-identifier "${issue_identifier}" \
    --snapshot-file "${snapshot_file}" \
    --check-head 2>&1
}

append_pr_handoff() {
  local section_file="$1"
  local output rc

  set +e
  output="$(bash "${WORKPAD_HELPER}" append-section \
    "${helper_args_common[@]}" \
    --section-title "PR Handoff" \
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
    --from-state "Ready for PR" 2>&1)"
  rc=$?
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    echo "${output}" >&2
    return "${rc}"
  fi

  extract_kv "LINEAR_STATE_STATUS" "${output}"
}

write_pr_body_file() {
  local body_file="$1"
  local title url
  title="$(issue_title)"
  url="$(issue_url)"

  {
    echo "## Summary"
    echo "- Symphony opened this PR for ${issue_identifier}."
    if [[ -n "${title}" ]]; then
      echo "- Linear title: $(sanitize_value "${title}")"
    fi
    if [[ -n "${url}" ]]; then
      echo "- Linear issue: ${url}"
    fi
    echo
    echo "## Test Plan"
    echo "- GitHub checks and Claude review are gated by the Symphony Ready for PR lane."
  } > "${body_file}"
}

write_handoff_section() {
  local section_file="$1"
  local outcome="$2"
  local next_state="$3"
  local reason="$4"

  {
    echo "- Outcome: ${outcome}"
    echo "- Branch: \`${branch:-unknown}\`"
    echo "- Head SHA: \`${head_sha:-unknown}\`"
    echo "- Ready status: \`${ready_status:-unknown}\`"
    echo "- Check status: \`${check_status:-unknown}\`"
    echo "- Claude status: \`${claude_status:-unknown}\`"
    if [[ -n "${pr_number:-}" ]]; then
      echo "- PR: #${pr_number} ${pr_url:-}"
    fi
    echo "- Reason: ${reason}"
    echo "- Next state: ${next_state}"
    if [[ -n "${instructions:-}" ]]; then
      echo "- Helper instruction: $(sanitize_value "${instructions}")"
    fi
  } > "${section_file}"
}

write_dirty_handoff_section() {
  local section_file="$1"
  local reason="$2"
  local guard_output="$3"
  local guard_status guard_message artifact_dir

  guard_status="$(extract_kv "NO_CODE_GUARD_STATUS" "${guard_output}")"
  guard_message="$(extract_kv "NO_CODE_GUARD_MESSAGE" "${guard_output}")"
  artifact_dir="$(extract_kv "NO_CODE_GUARD_ARTIFACT_DIR" "${guard_output}")"

  {
    echo "- Outcome: Ready for PR found repository changes in a no-code lane; moving back to In Progress for implementation triage."
    echo "- Branch: \`${branch:-unknown}\`"
    echo "- Head SHA: \`${head_sha:-unknown}\`"
    echo "- Guard status: \`${guard_status:-unknown}\`"
    if [[ -n "${artifact_dir}" ]]; then
      echo "- Guard artifacts: \`${artifact_dir}\`"
    fi
    echo "- Reason: ${reason}"
    if [[ -n "${guard_message}" ]]; then
      echo "- Guard message: $(sanitize_value "${guard_message}")"
    fi
    echo "- Next state: In Progress"
  } > "${section_file}"
}

return_to_in_progress_for_guard_violation() {
  local reason="$1"
  local guard_output="$2"
  local section_file workpad_status state_status guard_status

  section_file="$(mktemp "${TMPDIR:-/tmp}/symphony-rfp-dirty-handoff-XXXXXX.md")"
  write_dirty_handoff_section "${section_file}" "${reason}" "${guard_output}"
  workpad_status="$(append_pr_handoff "${section_file}")" || exit 3
  state_status="$(move_issue_state "In Progress")" || exit 3
  guard_status="$(extract_kv "NO_CODE_GUARD_STATUS" "${guard_output}")"
  payload="$(build_payload "returned_to_in_progress" "In Progress" "${reason}" "${ready_status:-}" "${check_status:-${guard_status}}" "${claude_status:-}" "${workpad_status}" "${state_status}")"
  emit_payload "${payload}"
}

verify_guard_or_return_to_progress() {
  local reason="$1"
  local guard_output guard_rc

  set +e
  guard_output="$(run_guard_verify_captured)"
  guard_rc=$?
  set -e

  if [[ "${guard_rc}" -eq 20 || "${guard_rc}" -eq 21 ]]; then
    return_to_in_progress_for_guard_violation "${reason}" "${guard_output}"
    exit 0
  elif [[ "${guard_rc}" -ne 0 ]]; then
    echo "${guard_output}" >&2
    exit 3
  fi
}

verify_guard_after_helper_bounce() {
  local guard_output guard_rc guard_status

  set +e
  guard_output="$(run_guard_verify_captured)"
  guard_rc=$?
  set -e

  if [[ "${guard_rc}" -eq 20 || "${guard_rc}" -eq 21 ]]; then
    guard_status="$(extract_kv "NO_CODE_GUARD_STATUS" "${guard_output}")"
    payload="$(build_payload "bounced_to_in_progress" "In Progress" "helper_auto_bounced_with_workspace_changes" "${ready_status:-}" "${check_status:-${guard_status}}" "${claude_status:-}" "" "already_moved")"
    emit_payload "${payload}"
    exit 0
  elif [[ "${guard_rc}" -ne 0 ]]; then
    echo "${guard_output}" >&2
    exit 3
  fi
}

extract_disposition_file() {
  local show_output body_file disposition_file

  set +e
  show_output="$(bash "${WORKPAD_HELPER}" show "${helper_args_common[@]}" 2>/dev/null)"
  set -e

  body_file="$(extract_kv "WORKPAD_BODY_FILE" "${show_output}")"
  if [[ -z "${body_file}" || ! -f "${body_file}" ]]; then
    return 0
  fi
  if ! grep -q '^## Claude Feedback Disposition$' "${body_file}"; then
    return 0
  fi

  disposition_file="$(mktemp "${TMPDIR:-/tmp}/symphony-rfp-disposition-XXXXXX.md")"
  sed -n '/^## Claude Feedback Disposition$/,/^## /{ /^## Claude Feedback Disposition$/d; /^## /d; p; }' "${body_file}" > "${disposition_file}"
  if [[ -s "${disposition_file}" ]]; then
    printf '%s' "${disposition_file}"
  else
    rm -f "${disposition_file}"
  fi
}

run_ready_helper() {
  local body_file="$1"
  local disposition_file="$2"
  local -a cmd

  cmd=(bash "${READY_HELPER}"
    --delivery-mode "${delivery_mode}"
    --issue-identifier "${issue_identifier}"
    --branch "${branch}"
    --create-if-missing
    --body-file "${body_file}"
    --wait-for-review-seconds "${wait_for_review_seconds}"
    --review-poll-seconds "${review_poll_seconds}"
    --wait-for-checks-seconds "${wait_for_checks_seconds}"
    --check-poll-seconds "${check_poll_seconds}"
    --auto-bounce-claude-feedback
    --auto-bounce-failing-checks)

  if [[ -n "${disposition_file}" ]]; then
    cmd+=(--disposition-file "${disposition_file}")
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
if [[ "${current_state}" != "Ready for PR" ]]; then
  payload="$(build_payload "noop" "${current_state:-unknown}" "state_changed_before_ready_for_pr_lane")"
  emit_payload "${payload}"
  exit 0
fi

if [[ -z "${delivery_mode}" ]]; then
  delivery_mode="$(infer_delivery_mode)"
fi

branch="$(branch_name)"
head_sha="$(branch_head_sha)"
body_file="$(mktemp "${TMPDIR:-/tmp}/symphony-rfp-pr-body-XXXXXX.md")"
section_file="$(mktemp "${TMPDIR:-/tmp}/symphony-rfp-handoff-XXXXXX.md")"
disposition_file="$(extract_disposition_file || true)"

write_pr_body_file "${body_file}"
set +e
guard_output="$(run_guard_snapshot_captured)"
guard_rc=$?
set -e
if [[ "${guard_rc}" -eq 20 || "${guard_rc}" -eq 21 ]]; then
  return_to_in_progress_for_guard_violation "workspace_dirty_at_entry" "${guard_output}"
  exit 0
elif [[ "${guard_rc}" -ne 0 ]]; then
  echo "${guard_output}" >&2
  exit 3
fi

set +e
ready_output="$(run_ready_helper "${body_file}" "${disposition_file}" 2>&1)"
ready_rc=$?
set -e

printf '%s\n' "${ready_output}" >&2

ready_status="$(extract_kv "READY_FOR_PR_STATUS" "${ready_output}")"
ready_next_state="$(extract_kv "READY_FOR_PR_NEXT_STATE" "${ready_output}")"
check_status="$(extract_kv "READY_FOR_PR_CHECK_STATUS" "${ready_output}")"
check_action="$(extract_kv "READY_FOR_PR_CHECK_ACTION" "${ready_output}")"
claude_status="$(extract_kv "READY_FOR_PR_CLAUDE_STATUS" "${ready_output}")"
claude_action="$(extract_kv "READY_FOR_PR_CLAUDE_ACTION" "${ready_output}")"
pr_number="$(extract_kv "READY_FOR_PR_PR_NUMBER" "${ready_output}")"
pr_url="$(extract_kv "READY_FOR_PR_PR_URL" "${ready_output}")"
instructions="$(extract_kv "READY_FOR_PR_INSTRUCTIONS" "${ready_output}")"

case "${ready_next_state}:${check_action}:${claude_action}" in
  In\ Progress:bounced_to_in_progress:*|In\ Progress:*:bounced_to_in_progress)
    verify_guard_after_helper_bounce
    payload="$(build_payload "bounced_to_in_progress" "In Progress" "helper_auto_bounced" "${ready_status}" "${check_status}" "${claude_status}")"
    emit_payload "${payload}"
    exit 0
    ;;
esac

case "${ready_rc}:${ready_status}:${check_status}:${claude_status}" in
  0:skip_no_pr:*:*)
    write_handoff_section "${section_file}" "No PR is required for this ticket." "Human Review Prep" "workflow_no_pr"
    workpad_status="$(append_pr_handoff "${section_file}")" || exit 3
    verify_guard_or_return_to_progress "workspace_dirty_after_ready_for_pr"
    state_status="$(move_issue_state "Human Review Prep")" || exit 3
    payload="$(build_payload "ready" "Human Review Prep" "workflow_no_pr" "${ready_status}" "${check_status}" "${claude_status}" "${workpad_status}" "${state_status}")"
    emit_payload "${payload}"
    ;;
  0:existing_pr_conflicted:*:*|21:invalid_branch:*:*)
    write_handoff_section "${section_file}" "Ready for PR found branch/PR repair work." "In Progress" "${ready_status:-invalid_branch}"
    workpad_status="$(append_pr_handoff "${section_file}")" || exit 3
    verify_guard_or_return_to_progress "workspace_dirty_after_ready_for_pr"
    state_status="$(move_issue_state "In Progress")" || exit 3
    payload="$(build_payload "returned_to_in_progress" "In Progress" "${ready_status:-invalid_branch}" "${ready_status}" "${check_status}" "${claude_status}" "${workpad_status}" "${state_status}")"
    emit_payload "${payload}"
    ;;
  0:*:clean:quiet|0:*:clean:maxed_out|0:created_pr:clean:*|0:existing_pr:clean:*)
    write_handoff_section "${section_file}" "PR gate is clean and ready for human review prep." "Human Review Prep" "pr_gate_clean"
    workpad_status="$(append_pr_handoff "${section_file}")" || exit 3
    verify_guard_or_return_to_progress "workspace_dirty_after_ready_for_pr"
    state_status="$(move_issue_state "Human Review Prep")" || exit 3
    payload="$(build_payload "ready" "Human Review Prep" "pr_gate_clean" "${ready_status}" "${check_status}" "${claude_status}" "${workpad_status}" "${state_status}")"
    emit_payload "${payload}"
    ;;
  11:*:*:pending|11:*:pending:*|11:*:missing:*)
    write_handoff_section "${section_file}" "PR gate is still waiting for GitHub or Claude." "Ready for PR" "gate_pending"
    workpad_status="$(append_pr_handoff "${section_file}")" || exit 3
    verify_guard_or_return_to_progress "workspace_dirty_after_ready_for_pr"
    payload="$(build_payload "waiting" "Ready for PR" "gate_pending" "${ready_status}" "${check_status}" "${claude_status}" "${workpad_status}" "unchanged")"
    emit_payload "${payload}"
    ;;
  *)
    echo "Ready for PR helper failed or returned an unsupported state." >&2
    echo "Exit status: ${ready_rc}" >&2
    exit "${ready_rc:-3}"
    ;;
esac
