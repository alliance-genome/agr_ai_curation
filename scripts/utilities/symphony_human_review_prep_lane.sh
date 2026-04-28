#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"

CONTEXT_HELPER="${SYMPHONY_HRP_CONTEXT_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_context.sh}"
WORKPAD_HELPER="${SYMPHONY_HRP_WORKPAD_HELPER:-${SCRIPT_DIR}/symphony_linear_workpad.sh}"
STATE_HELPER="${SYMPHONY_HRP_STATE_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_state.sh}"
PREP_HELPER="${SYMPHONY_HRP_PREP_HELPER:-${SCRIPT_DIR}/symphony_human_review_prep.sh}"
GUARD_HELPER="${SYMPHONY_HRP_GUARD_HELPER:-${SCRIPT_DIR}/symphony_guard_no_code_changes.sh}"

usage() {
  cat <<'EOF'
Usage:
  symphony_human_review_prep_lane.sh --issue-identifier ISSUE [options]

Purpose:
  Deterministically handle the Symphony Human Review Prep lane without Codex.

Behavior:
  - Verify the workspace is clean for a no-code lane.
  - Run the Human Review prep wrapper.
  - Write a short objective Human Review Handoff section.
  - Move the issue to Human Review even when local containers are partial or unhealthy.
  - Move back to In Progress only when repository changes are present.

Options:
  --issue-identifier VALUE    Linear issue identifier such as ALL-123.
  --issue-id VALUE            Linear issue id; accepted by helper calls.
  --workspace-dir PATH        Workspace checkout. Default: current directory.
  --delivery-mode VALUE       pr or no_pr. Default: inferred from workflow:no-pr label.
  --review-host VALUE         Review host passed to prep helper.
  --linear-api-key VALUE      Linear API key forwarded to helper calls.
  --context-json-file PATH    Testing/debug override: use a normalized context JSON file.
  --linear-json-file PATH     Testing/debug override forwarded to context helper.
  --context-helper PATH       Override context helper path.
  --workpad-helper PATH       Override workpad helper path.
  --state-helper PATH         Override state helper path.
  --prep-helper PATH          Override prep helper path.
  --guard-helper PATH         Override no-code guard helper path.
  --json-output-file PATH     Write JSON summary to this path.
  --format VALUE              One of: env, json, pretty. Default: env.
  --help                      Show this help.

Output contract:
  HUMAN_REVIEW_PREP_STATUS=ready|returned_to_in_progress|noop|error
  HUMAN_REVIEW_PREP_ISSUE_IDENTIFIER=...
  HUMAN_REVIEW_PREP_DELIVERY_MODE=pr|no_pr
  HUMAN_REVIEW_PREP_BRANCH=...
  HUMAN_REVIEW_PREP_HEAD_SHA=...
  HUMAN_REVIEW_PREP_STACK_STARTUP=...
  HUMAN_REVIEW_PREP_TO_STATE=Human Review|In Progress
  HUMAN_REVIEW_PREP_REASON=...
EOF
}

issue_identifier=""
issue_id=""
workspace_dir="$PWD"
delivery_mode=""
review_host="${REVIEW_HOST:-${SYMPHONY_REVIEW_HOST:-}}"
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
    --review-host)
      review_host="${2:-}"
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
    --prep-helper)
      PREP_HELPER="${2:-}"
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

for helper in "${WORKPAD_HELPER}" "${STATE_HELPER}" "${PREP_HELPER}" "${GUARD_HELPER}"; do
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
snapshot_file="$(mktemp "${TMPDIR:-/tmp}/symphony-hrp-guard-snapshot-XXXXXX.env")"

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
          "Symphony Human Review Prep lane result",
          "",
          "Status: \(.human_review_prep_status // "unknown")",
          "Issue: \(.human_review_prep_issue_identifier // "")",
          "Delivery mode: \(.human_review_prep_delivery_mode // "")",
          "Branch: \(.human_review_prep_branch // "")",
          "HEAD: \(.human_review_prep_head_sha // "")",
          "To: \(.human_review_prep_to_state // "")",
          "Reason: \(.human_review_prep_reason // "none")"
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

build_payload() {
  local status="$1"
  local to_state="$2"
  local reason="$3"
  local stack_startup="${4:-}"
  local prep_exit_code="${5:-}"
  local workpad_status="${6:-}"
  local state_status="${7:-}"

  jq -cn \
    --arg status "${status}" \
    --arg issue_identifier "${issue_identifier}" \
    --arg delivery_mode "${delivery_mode}" \
    --arg branch "${branch}" \
    --arg head_sha "${head_sha}" \
    --arg stack_startup "${stack_startup}" \
    --arg to_state "${to_state}" \
    --arg reason "${reason}" \
    --arg prep_exit_code "${prep_exit_code}" \
    --arg workpad_status "${workpad_status}" \
    --arg state_status "${state_status}" '
    {
      human_review_prep_status: $status,
      human_review_prep_issue_identifier: $issue_identifier,
      human_review_prep_delivery_mode: $delivery_mode,
      human_review_prep_branch: $branch,
      human_review_prep_head_sha: $head_sha,
      human_review_prep_stack_startup: $stack_startup,
      human_review_prep_to_state: $to_state,
      human_review_prep_reason: $reason,
      human_review_prep_prep_exit_code: $prep_exit_code,
      human_review_prep_workpad_status: $workpad_status,
      human_review_prep_state_status: $state_status
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

  local temp_json context_output
  temp_json="$(mktemp "${TMPDIR:-/tmp}/symphony-hrp-context-XXXXXX.json")"
  set +e
  context_output="$(bash "${CONTEXT_HELPER}" \
    --json-output-file "${temp_json}" \
    --include-team-states \
    "${helper_args_common[@]}" 2>&1)"
  local rc=$?
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    rm -f "${temp_json}"
    echo "${context_output}" >&2
    return 1
  fi

  printf '%s' "${temp_json}"
}

guard_command() {
  local subcommand="$1"
  shift

  local -a args=(
    "${subcommand}"
    --workspace-dir "${workspace_dir}"
    --state "Human Review Prep"
    --snapshot-file "${snapshot_file}"
  )
  if [[ -n "${issue_identifier}" ]]; then
    args+=(--issue-identifier "${issue_identifier}")
  fi
  args+=("$@")

  bash "${GUARD_HELPER}" "${args[@]}"
}

append_handoff_section() {
  local section_file="$1"
  local output rc

  set +e
  output="$(bash "${WORKPAD_HELPER}" append-section \
    "${helper_args_common[@]}" \
    --section-title "Human Review Handoff" \
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
    --from-state "Human Review Prep" 2>&1)"
  rc=$?
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    echo "${output}" >&2
    return "${rc}"
  fi

  extract_kv "LINEAR_STATE_STATUS" "${output}"
}

current_issue_state() {
  jq -r '.issue.state.name // ""' "${context_json_path}"
}

controlled_noop() {
  local reason="$1"
  local current_state="$2"
  local payload

  payload="$(build_payload "noop" "${current_state}" "${reason}" "" "" "" "skipped")"
  emit_payload "${payload}"
  exit 0
}

ensure_current_state_or_noop() {
  local expected_state="$1"
  local reason="$2"
  local current_state

  context_json_path="$(resolve_context_json_file)" || exit 3
  current_state="$(current_issue_state)"
  if [[ "${current_state}" != "${expected_state}" ]]; then
    controlled_noop "${reason}" "${current_state:-unknown}"
  fi
}

write_dirty_handoff() {
  local section_file="$1"
  local reason="$2"
  local guard_output="$3"
  local guard_status artifact_dir message

  guard_status="$(extract_kv "NO_CODE_GUARD_STATUS" "${guard_output}")"
  artifact_dir="$(extract_kv "NO_CODE_GUARD_ARTIFACT_DIR" "${guard_output}")"
  message="$(extract_kv "NO_CODE_GUARD_MESSAGE" "${guard_output}")"

  {
    echo "- Outcome: Human Review Prep did not run local prep because this no-code lane found repository changes."
    echo "- Branch: \`${branch:-unknown}\`"
    echo "- Head SHA: \`${head_sha:-unknown}\`"
    echo "- Delivery mode: \`${delivery_mode:-unknown}\`"
    echo "- Prep Result: skipped"
    echo "- Prep Notes: ${reason}; guard status \`${guard_status:-unknown}\`."
    if [[ -n "${artifact_dir}" ]]; then
      echo "- Guard artifact directory: \`${artifact_dir}\`"
    fi
    if [[ -n "${message}" ]]; then
      echo "- Guard message: $(sanitize_value "${message}")"
    fi
    echo "- Next step: inspect the workspace changes in \`In Progress\`, then rerun review prep once clean."
  } > "${section_file}"
}

write_prep_handoff() {
  local section_file="$1"
  local prep_output="$2"
  local prep_rc="$3"
  local key value
  local keys=(
    human_review_prep_wrapper_status
    human_review_prep_wrapper_reason
    start_test_containers
    stack_startup
    dependency_start_status
    frontend_health
    backend_health
    curation_db_health
    pdf_extraction_health
    backend_root_cause
  )

  {
    echo "- Outcome: Human Review Prep completed deterministic local prep."
    echo "- Branch: \`${branch:-unknown}\`"
    echo "- Head SHA: \`${head_sha:-unknown}\`"
    echo "- Delivery mode: \`${delivery_mode:-unknown}\`"
    echo "- Prep exit code: \`${prep_rc}\`"
    echo
    echo "### Prep Result"
    echo
    echo "- Command: \`bash scripts/utilities/symphony_human_review_prep.sh --workspace-dir <workspace>\`"
    for key in "${keys[@]}"; do
      value="$(extract_kv "${key}" "${prep_output}")"
      if [[ -n "${value}" ]]; then
        echo "- ${key}: \`$(sanitize_value "${value}")\`"
      fi
    done

    frontend_url="$(extract_kv "review_frontend_url" "${prep_output}")"
    backend_url="$(extract_kv "review_backend_url" "${prep_output}")"
    if [[ -n "${frontend_url}" || -n "${backend_url}" ]]; then
      echo
      echo "### Review URLs"
      echo
      if [[ -n "${frontend_url}" ]]; then
        echo "- Frontend: $(sanitize_value "${frontend_url}")"
      fi
      if [[ -n "${backend_url}" ]]; then
        echo "- Backend health: $(sanitize_value "${backend_url}")"
      fi
    fi

    wrapper_status="$(extract_kv "human_review_prep_wrapper_status" "${prep_output}")"
    stack_startup="$(extract_kv "stack_startup" "${prep_output}")"
    if [[ "${prep_rc}" != "0" || "${wrapper_status}" == "partial" || "${stack_startup}" == "skipped_by_flag" ]]; then
      echo
      echo "### Prep Notes"
      echo
      if [[ "${stack_startup}" == "skipped_by_flag" ]]; then
        echo "- Local stack startup was intentionally skipped. Add \`start_test_containers=true\` to the ticket description to boot containers on the next prep run."
      fi
      if [[ "${prep_rc}" != "0" ]]; then
        echo "- Prep helper exited non-zero, but container health is review context rather than a lane blocker. See the summary lines above."
      fi
      if [[ "${wrapper_status}" == "partial" ]]; then
        echo "- Local prep is partial. Human Review can proceed with the recorded health details."
      fi
    fi
  } > "${section_file}"
}

branch="$(git -C "${workspace_dir}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
head_sha="$(git -C "${workspace_dir}" rev-parse HEAD 2>/dev/null || true)"

context_json_path="$(resolve_context_json_file)" || exit 3
resolved_issue_identifier="$(jq -r '.issue.identifier // ""' "${context_json_path}")"
if [[ -z "${issue_identifier}" ]]; then
  issue_identifier="${resolved_issue_identifier}"
fi

if [[ -z "${delivery_mode}" ]]; then
  if jq -e '.labels[]? | select(.name == "workflow:no-pr")' "${context_json_path}" >/dev/null; then
    delivery_mode="no_pr"
  else
    delivery_mode="pr"
  fi
fi

if [[ "$(current_issue_state)" != "Human Review Prep" ]]; then
  controlled_noop "state_changed_before_prep" "$(current_issue_state)"
fi

set +e
guard_output="$(guard_command snapshot 2>&1)"
guard_rc=$?
set -e
if [[ "${guard_rc}" -eq 20 || "${guard_rc}" -eq 21 ]]; then
  section_file="$(mktemp "${TMPDIR:-/tmp}/symphony-hrp-dirty-handoff-XXXXXX.md")"
  write_dirty_handoff "${section_file}" "workspace_dirty_at_entry" "${guard_output}"
  workpad_status="$(append_handoff_section "${section_file}")" || exit 3
  state_status="$(move_issue_state "In Progress")" || exit 3
  payload="$(build_payload "returned_to_in_progress" "In Progress" "workspace_dirty_at_entry" "" "" "${workpad_status}" "${state_status}")"
  emit_payload "${payload}"
  exit 0
elif [[ "${guard_rc}" -ne 0 ]]; then
  echo "${guard_output}" >&2
  exit 3
fi

description="$(jq -r '.issue.description // ""' "${context_json_path}")"
prep_args=(--workspace-dir "${workspace_dir}")
if [[ -n "${review_host}" ]]; then
  prep_args+=(--review-host "${review_host}")
fi
if grep -Fq "start_test_containers=true" <<< "${description}"; then
  prep_args+=(--start-test-containers true)
fi

set +e
prep_output="$(bash "${PREP_HELPER}" "${prep_args[@]}" 2>&1)"
prep_rc=$?
set -e

wrapper_status="$(extract_kv "human_review_prep_wrapper_status" "${prep_output}")"
wrapper_reason="$(extract_kv "human_review_prep_wrapper_reason" "${prep_output}")"
if [[ -z "${wrapper_status}" || -z "${wrapper_reason}" ]]; then
  echo "Human Review Prep helper did not emit required human_review_prep_wrapper_status/reason lines." >&2
  printf '%s\n' "${prep_output}" >&2
  exit 3
fi

section_file="$(mktemp "${TMPDIR:-/tmp}/symphony-hrp-handoff-XXXXXX.md")"
write_prep_handoff "${section_file}" "${prep_output}" "${prep_rc}"
workpad_status="$(append_handoff_section "${section_file}")" || exit 3

set +e
guard_verify_output="$(guard_command verify --check-head 2>&1)"
guard_verify_rc=$?
set -e
if [[ "${guard_verify_rc}" -eq 20 || "${guard_verify_rc}" -eq 21 ]]; then
  section_file="$(mktemp "${TMPDIR:-/tmp}/symphony-hrp-dirty-after-handoff-XXXXXX.md")"
  write_dirty_handoff "${section_file}" "workspace_dirty_after_prep" "${guard_verify_output}"
  workpad_status="$(append_handoff_section "${section_file}")" || exit 3
  state_status="$(move_issue_state "In Progress")" || exit 3
  payload="$(build_payload "returned_to_in_progress" "In Progress" "workspace_dirty_after_prep" "$(extract_kv "stack_startup" "${prep_output}")" "${prep_rc}" "${workpad_status}" "${state_status}")"
  emit_payload "${payload}"
  exit 0
elif [[ "${guard_verify_rc}" -ne 0 ]]; then
  echo "${guard_verify_output}" >&2
  exit 3
fi

ensure_current_state_or_noop "Human Review Prep" "state_changed_before_final_transition"

state_status="$(move_issue_state "Human Review")" || exit 3
payload="$(build_payload "ready" "Human Review" "ready_for_human_review" "$(extract_kv "stack_startup" "${prep_output}")" "${prep_rc}" "${workpad_status}" "${state_status}")"
emit_payload "${payload}"
