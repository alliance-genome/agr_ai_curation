#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

WORKPAD_HELPER="${SYMPHONY_NEEDS_REVIEW_WORKPAD_HELPER:-${SCRIPT_DIR}/symphony_linear_workpad.sh}"
STATE_HELPER="${SYMPHONY_NEEDS_REVIEW_STATE_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_state.sh}"

usage() {
  cat <<'EOF'
Usage:
  symphony_needs_review_claim.sh --issue-identifier ISSUE [options]

Purpose:
  Deterministically handle the Symphony `Needs Review` claim-only lane.

Behavior:
  - Read the existing Review Handoff from the Symphony workpad.
  - Record a Review Claim with branch, HEAD, and reviewer focus.
  - Move the issue from Needs Review to In Review.
  - If the handoff is missing or the workspace is dirty, record the problem and
    move the issue back to In Progress for repair.

Options:
  --issue-identifier VALUE    Linear issue identifier such as ALL-123.
  --issue-id VALUE            Linear issue id; accepted by helper calls.
  --workspace-dir PATH        Workspace checkout. Default: current directory.
  --linear-api-key VALUE      Linear API key forwarded to helper calls.
  --context-json-file PATH    Testing/debug override forwarded to helper calls.
  --linear-json-file PATH     Testing/debug override forwarded to helper calls.
  --workpad-helper PATH       Override workpad helper path.
  --state-helper PATH         Override state helper path.
  --json-output-file PATH     Write JSON summary to this path.
  --format VALUE              One of: env, json, pretty. Default: env.
  --help                      Show this help.

Output contract:
  NEEDS_REVIEW_CLAIM_STATUS=claimed|returned_to_in_progress|error
  NEEDS_REVIEW_CLAIM_ISSUE_IDENTIFIER=...
  NEEDS_REVIEW_CLAIM_BRANCH=...
  NEEDS_REVIEW_CLAIM_HEAD_SHA=...
  NEEDS_REVIEW_CLAIM_TO_STATE=...
  NEEDS_REVIEW_CLAIM_REASON=...
EOF
}

issue_identifier=""
issue_id=""
workspace_dir="$PWD"
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
    --workpad-helper)
      WORKPAD_HELPER="${2:-}"
      shift 2
      ;;
    --state-helper)
      STATE_HELPER="${2:-}"
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

if [[ -z "${issue_identifier}" && -z "${issue_id}" ]]; then
  echo "One of --issue-identifier or --issue-id is required." >&2
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
          "Symphony Needs Review claim result",
          "",
          "Status: \(.needs_review_claim_status // "unknown")",
          "Issue: \(.needs_review_claim_issue_identifier // "")",
          "Branch: \(.needs_review_claim_branch // "")",
          "HEAD: \(.needs_review_claim_head_sha // "")",
          "To: \(.needs_review_claim_to_state // "")",
          "Reason: \(.needs_review_claim_reason // "none")"
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
if [[ -n "${context_json_file}" ]]; then
  helper_args_common+=(--context-json-file "${context_json_file}")
fi
if [[ -n "${linear_json_file}" ]]; then
  helper_args_common+=(--linear-json-file "${linear_json_file}")
fi

if [[ ! -x "${WORKPAD_HELPER}" ]]; then
  echo "Workpad helper is missing or not executable: ${WORKPAD_HELPER}" >&2
  exit 2
fi
if [[ ! -x "${STATE_HELPER}" ]]; then
  echo "State helper is missing or not executable: ${STATE_HELPER}" >&2
  exit 2
fi

workspace_dir="$(cd "${workspace_dir}" && pwd -P)"

branch=""
head_sha=""
status_short=""
dirty_status="clean"
if git -C "${workspace_dir}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch="$(git -C "${workspace_dir}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  head_sha="$(git -C "${workspace_dir}" rev-parse HEAD 2>/dev/null || true)"
  status_short="$(git -C "${workspace_dir}" status --short 2>/dev/null || true)"
  if [[ -n "${status_short}" ]]; then
    dirty_status="dirty"
  fi
else
  dirty_status="not_git_worktree"
fi

workpad_body_file="$(mktemp "${TMPDIR:-/tmp}/symphony-needs-review-workpad-XXXXXX.md")"
workpad_output=""
set +e
workpad_output="$(bash "${WORKPAD_HELPER}" show "${helper_args_common[@]}" --output-file "${workpad_body_file}" 2>&1)"
workpad_rc=$?
set -e

if [[ "${workpad_rc}" -ne 0 ]]; then
  payload="$(jq -cn \
    --arg status "error" \
    --arg issue_identifier "${issue_identifier}" \
    --arg branch "${branch}" \
    --arg head_sha "${head_sha}" \
    --arg reason "workpad_show_failed" \
    --arg details "${workpad_output}" '
    {
      needs_review_claim_status: $status,
      needs_review_claim_issue_identifier: $issue_identifier,
      needs_review_claim_branch: $branch,
      needs_review_claim_head_sha: $head_sha,
      needs_review_claim_reason: $reason,
      needs_review_claim_details: $details
    }')"
  emit_payload "${payload}"
  exit 3
fi

handoff_status_file="$(mktemp "${TMPDIR:-/tmp}/symphony-needs-review-handoff-XXXXXX.txt")"
python3 - "${workpad_body_file}" > "${handoff_status_file}" <<'PY'
import re
import sys

body = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"(?ms)^## Review Handoff\n(.*?)(?=^## |\Z)", body)
if not match:
    print("HANDOFF_FOUND=0")
    print("HANDOFF_FOCUS=Missing Review Handoff section.")
    raise SystemExit(0)

section = match.group(1).strip()
focus = "No special reviewer focus stated in Review Handoff."
for line in section.splitlines():
    normalized = line.lower()
    if "reviewer focus" in normalized or "review focus" in normalized or "open question" in normalized:
        cleaned = re.sub(r"^\s*[-*]\s*", "", line).strip()
        focus = cleaned or focus
        break

print("HANDOFF_FOUND=1")
print(f"HANDOFF_FOCUS={focus}")
PY

handoff_found="$(awk -F= '/^HANDOFF_FOUND=/{print $2}' "${handoff_status_file}" | tail -n 1)"
handoff_focus="$(awk -F= '/^HANDOFF_FOCUS=/{sub(/^HANDOFF_FOCUS=/, ""); print}' "${handoff_status_file}" | tail -n 1)"
claim_time="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

target_state="In Review"
claim_status="claimed"
reason="ready_for_review"

if [[ "${handoff_found}" != "1" ]]; then
  target_state="In Progress"
  claim_status="returned_to_in_progress"
  reason="missing_review_handoff"
elif [[ "${dirty_status}" != "clean" ]]; then
  target_state="In Progress"
  claim_status="returned_to_in_progress"
  reason="workspace_${dirty_status}"
fi

section_file="$(mktemp "${TMPDIR:-/tmp}/symphony-needs-review-claim-XXXXXX.md")"
{
  if [[ "${claim_status}" == "claimed" ]]; then
    printf '%s\n' "- Outcome: Review lane claimed."
  else
    printf '%s\n' "- Outcome: Claim not accepted; returning to \`In Progress\` for repair."
  fi
  printf '%s\n' "- Reviewer start time (UTC): ${claim_time}"
  printf '%s\n' "- Branch: \`${branch:-unknown}\`"
  printf '%s\n' "- Head SHA: \`${head_sha:-unknown}\`"
  printf '%s\n' "- Workspace status: ${dirty_status}"
  printf '%s\n' "- Review handoff: $(if [[ "${handoff_found}" == "1" ]]; then printf 'found'; else printf 'missing'; fi)"
  printf '%s\n' "- Special focus: ${handoff_focus}"
  if [[ "${dirty_status}" == "dirty" ]]; then
    printf '%s\n' "- Dirty files:"
    printf '%s\n' "${status_short}" | sed 's/^/  - `/' | sed 's/$/`/'
  fi
  if [[ "${claim_status}" != "claimed" ]]; then
    printf '%s\n' "- Next: The implementation lane should fix the handoff/workspace issue, then move back to \`Needs Review\`."
  fi
} > "${section_file}"

workpad_append_output="$(bash "${WORKPAD_HELPER}" append-section "${helper_args_common[@]}" --section-title "Review Claim" --section-file "${section_file}")"

state_output="$(bash "${STATE_HELPER}" "${helper_args_common[@]}" --state "${target_state}" --from-state "Needs Review")"
workpad_append_status="$(awk -F= '/^WORKPAD_STATUS=/{print $2}' <<< "${workpad_append_output}" | tail -n 1)"
state_status="$(awk -F= '/^LINEAR_STATE_STATUS=/{print $2}' <<< "${state_output}" | tail -n 1)"

payload="$(jq -cn \
  --arg status "${claim_status}" \
  --arg issue_identifier "${issue_identifier}" \
  --arg branch "${branch}" \
  --arg head_sha "${head_sha}" \
  --arg from_state "Needs Review" \
  --arg to_state "${target_state}" \
  --arg reason "${reason}" \
  --arg workspace_status "${dirty_status}" \
  --arg handoff_found "${handoff_found}" \
  --arg handoff_focus "${handoff_focus}" \
  --arg workpad_status "${workpad_append_status}" \
  --arg state_status "${state_status}" '
  {
    needs_review_claim_status: $status,
    needs_review_claim_issue_identifier: $issue_identifier,
    needs_review_claim_branch: $branch,
    needs_review_claim_head_sha: $head_sha,
    needs_review_claim_from_state: $from_state,
    needs_review_claim_to_state: $to_state,
    needs_review_claim_reason: $reason,
    needs_review_claim_workspace_status: $workspace_status,
    needs_review_claim_handoff_found: $handoff_found,
    needs_review_claim_handoff_focus: $handoff_focus,
    needs_review_claim_workpad_status: $workpad_status,
    needs_review_claim_state_status: $state_status
  }')"
emit_payload "${payload}"
