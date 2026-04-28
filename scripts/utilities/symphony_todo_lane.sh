#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

CONTEXT_HELPER="${SYMPHONY_TODO_CONTEXT_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_context.sh}"
WORKPAD_HELPER="${SYMPHONY_TODO_WORKPAD_HELPER:-${SCRIPT_DIR}/symphony_linear_workpad.sh}"
STATE_HELPER="${SYMPHONY_TODO_STATE_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_state.sh}"
BRANCH_HELPER="${SYMPHONY_TODO_BRANCH_HELPER:-${SCRIPT_DIR}/symphony_issue_branch.sh}"

usage() {
  cat <<'EOF'
Usage:
  symphony_todo_lane.sh --issue-identifier ISSUE [options]

Purpose:
  Deterministically handle the Symphony Todo lane without Codex.

Behavior:
  - Ensure the workspace is on the issue branch.
  - Write a short Todo Handoff section.
  - Move the issue from Todo to In Progress.
  - Move to Blocked only when branch setup finds a dirty or unexpected workspace.

Options:
  --issue-identifier VALUE    Linear issue identifier such as ALL-123.
  --issue-id VALUE            Linear issue id; accepted by helper calls.
  --workspace-dir PATH        Workspace checkout. Default: current directory.
  --base-branch VALUE         Base branch for issue branch helper. Default: main.
  --linear-api-key VALUE      Linear API key forwarded to helper calls.
  --context-json-file PATH    Testing/debug override: use a normalized context JSON file.
  --linear-json-file PATH     Testing/debug override forwarded to context helper.
  --context-helper PATH       Override context helper path.
  --workpad-helper PATH       Override workpad helper path.
  --state-helper PATH         Override state helper path.
  --branch-helper PATH        Override issue branch helper path.
  --json-output-file PATH     Write JSON summary to this path.
  --format VALUE              One of: env, json, pretty. Default: env.
  --help                      Show this help.

Output contract:
  TODO_LANE_STATUS=handed_off|blocked|noop|error
  TODO_LANE_ISSUE_IDENTIFIER=...
  TODO_LANE_BRANCH=...
  TODO_LANE_BRANCH_STATUS=...
  TODO_LANE_FROM_STATE=Todo
  TODO_LANE_TO_STATE=In Progress|Blocked|<current state>
  TODO_LANE_REASON=...
EOF
}

issue_identifier=""
issue_id=""
workspace_dir="$PWD"
base_branch="main"
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
    --base-branch)
      base_branch="${2:-}"
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
    --branch-helper)
      BRANCH_HELPER="${2:-}"
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
  env|json|pretty)
    ;;
  *)
    echo "--format must be one of: env, json, pretty" >&2
    exit 2
    ;;
esac

if [[ -z "${issue_identifier}" && -z "${issue_id}" ]]; then
  echo "One of --issue-identifier or --issue-id is required." >&2
  exit 2
fi

for helper in "${WORKPAD_HELPER}" "${STATE_HELPER}" "${BRANCH_HELPER}"; do
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
          "Symphony Todo lane result",
          "",
          "Status: \(.todo_lane_status // "unknown")",
          "Issue: \(.todo_lane_issue_identifier // "")",
          "Branch: \(.todo_lane_branch // "")",
          "Branch status: \(.todo_lane_branch_status // "")",
          "To: \(.todo_lane_to_state // "")",
          "Reason: \(.todo_lane_reason // "none")"
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
  local branch_status="${4:-}"
  local workpad_status="${5:-}"
  local state_status="${6:-}"

  jq -cn \
    --arg status "${status}" \
    --arg issue_identifier "${issue_identifier}" \
    --arg branch "${branch_name:-}" \
    --arg previous_branch "${previous_branch:-}" \
    --arg head_sha "${head_sha:-}" \
    --arg branch_status "${branch_status}" \
    --arg from_state "Todo" \
    --arg to_state "${to_state}" \
    --arg reason "${reason}" \
    --arg workpad_status "${workpad_status}" \
    --arg state_status "${state_status}" '
    {
      todo_lane_status: $status,
      todo_lane_issue_identifier: $issue_identifier,
      todo_lane_branch: $branch,
      todo_lane_previous_branch: $previous_branch,
      todo_lane_head_sha: $head_sha,
      todo_lane_branch_status: $branch_status,
      todo_lane_from_state: $from_state,
      todo_lane_to_state: $to_state,
      todo_lane_reason: $reason,
      todo_lane_workpad_status: $workpad_status,
      todo_lane_state_status: $state_status
    }'
}

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

resolve_context_json_file() {
  if [[ -n "${context_json_file}" ]]; then
    printf '%s' "${context_json_file}"
    return 0
  fi

  local temp_json context_output rc
  temp_json="$(mktemp "${TMPDIR:-/tmp}/symphony-todo-context-XXXXXX.json")"
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

context_issue_id() {
  jq -r '.issue.id // ""' "${context_json_path}"
}

issue_title() {
  jq -r '.issue.title // ""' "${context_json_path}"
}

branch_head_sha() {
  git -C "${workspace_dir}" rev-parse HEAD 2>/dev/null || true
}

worktree_dirty() {
  [[ -n "$(git -C "${workspace_dir}" status --porcelain --untracked-files=normal 2>/dev/null || true)" ]]
}

append_todo_handoff() {
  local section_file="$1"
  local output rc

  set +e
  output="$(bash "${WORKPAD_HELPER}" append-section \
    "${helper_args_common[@]}" \
    --section-title "Todo Handoff" \
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
    --from-state "Todo" 2>&1)"
  rc=$?
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    echo "${output}" >&2
    return "${rc}"
  fi

  extract_kv "LINEAR_STATE_STATUS" "${output}"
}

write_handoff_section() {
  local section_file="$1"
  local outcome="$2"
  local reason="$3"
  local title
  title="$(issue_title)"

  {
    echo "- Outcome: ${outcome}"
    if [[ -n "${title}" ]]; then
      echo "- Issue title: $(sanitize_value "${title}")"
    fi
    echo "- Branch: \`${branch_name:-unknown}\`"
    echo "- Previous branch: \`${previous_branch:-unknown}\`"
    echo "- Head SHA: \`${head_sha:-unknown}\`"
    echo "- Branch helper status: \`${branch_status:-unknown}\`"
    echo "- Branch helper message: $(sanitize_value "${branch_message:-none}")"
    echo "- Reason: ${reason}"
    echo "- Next lane: In Progress should read the Linear description and latest non-workpad comment, then implement only the ticket scope."
  } > "${section_file}"
}

run_branch_helper() {
  local output rc

  set +e
  output="$(
    cd "${workspace_dir}" &&
      bash "${BRANCH_HELPER}" \
        --issue-identifier "${issue_identifier}" \
        --base-branch "${base_branch}" 2>&1
  )"
  rc=$?
  set -e

  branch_output="${output}"
  branch_rc="${rc}"
}

context_json_path="$(resolve_context_json_file)" || exit 3

if [[ -z "${issue_identifier}" ]]; then
  issue_identifier="$(context_issue_identifier)"
fi
if [[ -z "${issue_id}" ]]; then
  issue_id="$(context_issue_id)"
fi
if [[ -z "${issue_identifier}" ]]; then
  echo "Todo lane requires an issue identifier for branch preparation." >&2
  exit 2
fi
rebuild_helper_args_common

current_state="$(current_issue_state)"

if [[ "${current_state}" != "Todo" ]]; then
  payload="$(build_payload "noop" "${current_state:-unknown}" "state_changed_before_todo_lane" "" "" "skipped")"
  emit_payload "${payload}"
  exit 0
fi

branch_output=""
branch_rc=0
run_branch_helper

branch_status="$(extract_kv "ISSUE_BRANCH_STATUS" "${branch_output}")"
branch_name="$(extract_kv "ISSUE_BRANCH_NAME" "${branch_output}")"
previous_branch="$(extract_kv "ISSUE_BRANCH_PREVIOUS_BRANCH" "${branch_output}")"
branch_message="$(extract_kv "ISSUE_BRANCH_MESSAGE" "${branch_output}")"
head_sha="$(branch_head_sha)"

if [[ "${branch_status}" == "already_on_target" ]] && worktree_dirty; then
  branch_status="blocked_dirty_worktree"
  branch_message="Workspace has uncommitted changes on ${branch_name:-${issue_identifier}}. Clean or stash them before handing Todo to In Progress."
fi

section_file="$(mktemp "${TMPDIR:-/tmp}/symphony-todo-handoff-XXXXXX.md")"

case "${branch_status}" in
  already_on_target|created|switched|switched_remote)
    write_handoff_section "${section_file}" \
      "Todo intake completed deterministically; workspace is ready for implementation." \
      "issue_branch_ready"
    workpad_status="$(append_todo_handoff "${section_file}")" || exit 3
    state_status="$(move_issue_state "In Progress")" || exit 3
    payload="$(build_payload "handed_off" "In Progress" "issue_branch_ready" "${branch_status}" "${workpad_status}" "${state_status}")"
    emit_payload "${payload}"
    ;;
  blocked_dirty_worktree|blocked_unexpected_branch)
    write_handoff_section "${section_file}" \
      "Todo intake could not safely prepare the implementation branch." \
      "${branch_status}"
    workpad_status="$(append_todo_handoff "${section_file}")" || exit 3
    state_status="$(move_issue_state "Blocked")" || exit 3
    payload="$(build_payload "blocked" "Blocked" "${branch_status}" "${branch_status}" "${workpad_status}" "${state_status}")"
    emit_payload "${payload}"
    ;;
  *)
    exit_status="${branch_rc}"
    if [[ "${exit_status}" -eq 0 ]]; then
      exit_status=3
    fi
    {
      echo "Todo branch helper failed."
      echo "Exit status: ${branch_rc}"
      echo "Output:"
      printf '%s\n' "${branch_output}"
    } >&2
    exit "${exit_status}"
    ;;
esac
