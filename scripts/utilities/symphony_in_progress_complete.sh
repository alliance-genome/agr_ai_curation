#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

WORKPAD_HELPER="${SYMPHONY_IN_PROGRESS_COMPLETE_WORKPAD_HELPER:-${SCRIPT_DIR}/symphony_linear_workpad.sh}"
STATE_HELPER="${SYMPHONY_IN_PROGRESS_COMPLETE_STATE_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_state.sh}"

usage() {
  cat <<'EOF'
Usage:
  symphony_in_progress_complete.sh --issue-identifier ISSUE [options]

Purpose:
  Provide the Symphony `In Progress` completion guard. Complete the
  implementation lane only when the workspace is ready for deterministic review.

Behavior:
  - Verifies the workspace is a clean git worktree.
  - Verifies required repo hooks exist and are executable.
  - Verifies the current branch has an upstream and local HEAD is synchronized
    with that upstream.
  - Writes or updates the `Review Handoff` workpad section.
  - Moves the issue from `In Progress` to `Needs Review` only after the guard
    passes. If the guard fails, it records exact repair context in the handoff
    and keeps the issue in `In Progress`.

Options:
  --issue-identifier VALUE    Linear issue identifier such as ALL-123.
  --issue-id VALUE            Linear issue id; accepted by helper calls.
  --workspace-dir PATH        Workspace checkout. Default: current directory.
  --linear-api-key VALUE      Linear API key forwarded to helper calls.
  --context-json-file PATH    Testing/debug override forwarded to helper calls.
  --linear-json-file PATH     Testing/debug override forwarded to helper calls.
  --workpad-helper PATH       Override workpad helper path.
  --state-helper PATH         Override state helper path.
  --section-file PATH         Review Handoff body written by the implementer.
  --section-stdin             Read Review Handoff body from stdin.
  --json-output-file PATH     Write JSON summary to this path.
  --format VALUE              One of: env, json, pretty. Default: env.
  --help                      Show this help.

Output contract:
  IN_PROGRESS_COMPLETE_STATUS=completed|blocked|error
  IN_PROGRESS_COMPLETE_ISSUE_IDENTIFIER=...
  IN_PROGRESS_COMPLETE_BRANCH=...
  IN_PROGRESS_COMPLETE_HEAD_SHA=...
  IN_PROGRESS_COMPLETE_TO_STATE=...
  IN_PROGRESS_COMPLETE_REASON=...
  IN_PROGRESS_COMPLETE_WORKSPACE_STATUS=...
  IN_PROGRESS_COMPLETE_HOOKS_STATUS=...
  IN_PROGRESS_COMPLETE_PUSH_STATUS=...
EOF
}

issue_identifier=""
issue_id=""
workspace_dir="$PWD"
linear_api_key=""
context_json_file=""
linear_json_file=""
section_file=""
section_stdin=0
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
    --section-file)
      section_file="${2:-}"
      shift 2
      ;;
    --section-stdin)
      section_stdin=1
      shift
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

if [[ -n "${section_file}" && "${section_stdin}" -eq 1 ]]; then
  echo "--section-file and --section-stdin are mutually exclusive." >&2
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
          "Symphony In Progress completion result",
          "",
          "Status: \(.in_progress_complete_status // "unknown")",
          "Issue: \(.in_progress_complete_issue_identifier // "")",
          "Branch: \(.in_progress_complete_branch // "")",
          "HEAD: \(.in_progress_complete_head_sha // "")",
          "To: \(.in_progress_complete_to_state // "")",
          "Reason: \(.in_progress_complete_reason // "none")",
          "Workspace: \(.in_progress_complete_workspace_status // "")",
          "Hooks: \(.in_progress_complete_hooks_status // "")",
          "Push: \(.in_progress_complete_push_status // "")"
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

if [[ "${section_stdin}" -eq 1 ]]; then
  section_file="$(mktemp "${TMPDIR:-/tmp}/symphony-in-progress-handoff-input-XXXXXX.md")"
  cat > "${section_file}"
fi

handoff_input_status="missing"
if [[ -n "${section_file}" ]]; then
  if [[ ! -f "${section_file}" ]]; then
    echo "Review Handoff section file does not exist: ${section_file}" >&2
    exit 2
  fi
  handoff_input_status="provided"
fi

if [[ ! -d "${workspace_dir}" ]]; then
  echo "Workspace directory does not exist: ${workspace_dir}" >&2
  exit 2
fi
workspace_dir="$(cd "${workspace_dir}" && pwd -P)"

join_lines() {
  paste -sd '; ' - 2>/dev/null || true
}

filtered_porcelain_status() {
  local line

  git -C "${workspace_dir}" status \
    --porcelain=v1 \
    --untracked-files=all \
    --ignore-submodules=dirty 2>/dev/null |
    while IFS= read -r line; do
      case "${line}" in
        "?? .symphony/"*|\
        "?? .symphony-docker-config"*|\
        "?? scripts/local_db_tunnel_env.sh"|\
        "?? scripts/utilities/symphony_main_sandbox.sh")
          continue
          ;;
      esac

      printf '%s\n' "${line}"
    done
}

resolve_git_common_dir() {
  local repo_path="$1"
  local git_common_dir=""

  if ! git_common_dir="$(git -C "${repo_path}" rev-parse --git-common-dir 2>/dev/null)"; then
    return 1
  fi

  if [[ "${git_common_dir}" != /* ]]; then
    git_common_dir="${repo_path}/${git_common_dir}"
  fi

  (
    cd "${git_common_dir}" && pwd -P
  )
}

branch=""
head_sha=""
status_short=""
workspace_status="not_git_worktree"
hooks_status="not_checked"
hook_issues=""
upstream=""
push_status="not_checked"
ahead_count="0"
behind_count="0"

if git -C "${workspace_dir}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  workspace_status="clean"
  branch="$(git -C "${workspace_dir}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  head_sha="$(git -C "${workspace_dir}" rev-parse HEAD 2>/dev/null || true)"
  status_short="$(filtered_porcelain_status)"
  if [[ -n "${status_short}" ]]; then
    workspace_status="dirty"
  fi

  if git_common_dir="$(resolve_git_common_dir "${workspace_dir}" 2>/dev/null)"; then
    hooks_status="ok"
    for hook_name in pre-commit pre-push; do
      hook_path="${git_common_dir}/hooks/${hook_name}"
      if [[ ! -e "${hook_path}" ]]; then
        hook_issues+="${hook_path}: missing"$'\n'
        hooks_status="missing_required_hooks"
      elif [[ ! -x "${hook_path}" ]]; then
        hook_issues+="${hook_path}: not executable"$'\n'
        if [[ "${hooks_status}" == "ok" ]]; then
          hooks_status="hooks_not_executable"
        fi
      fi
    done
  else
    hooks_status="git_common_dir_unavailable"
  fi

  if [[ "${branch}" == "HEAD" || -z "${branch}" ]]; then
    push_status="detached_head"
  else
    upstream="$(git -C "${workspace_dir}" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
    if [[ -z "${upstream}" ]]; then
      push_status="missing_upstream"
    else
      counts="$(git -C "${workspace_dir}" rev-list --left-right --count "${upstream}...HEAD" 2>/dev/null || true)"
      if [[ -z "${counts}" ]]; then
        push_status="upstream_unavailable"
      else
        read -r behind_count ahead_count <<< "${counts}"
        behind_count="${behind_count:-0}"
        ahead_count="${ahead_count:-0}"
        if [[ "${ahead_count}" == "0" && "${behind_count}" == "0" ]]; then
          push_status="synced"
        elif [[ "${ahead_count}" != "0" && "${behind_count}" != "0" ]]; then
          push_status="branch_diverged"
        elif [[ "${ahead_count}" != "0" ]]; then
          push_status="unpushed_commits"
        else
          push_status="behind_upstream"
        fi
      fi
    fi
  fi
fi

completion_status="completed"
target_state="Needs Review"
reason="ready_for_review"

if [[ "${workspace_status}" != "clean" ]]; then
  completion_status="blocked"
  target_state="In Progress"
  reason="workspace_${workspace_status}"
elif [[ "${hooks_status}" != "ok" ]]; then
  completion_status="blocked"
  target_state="In Progress"
  reason="required_hooks_${hooks_status}"
elif [[ "${push_status}" != "synced" ]]; then
  completion_status="blocked"
  target_state="In Progress"
  reason="branch_${push_status}"
elif [[ "${handoff_input_status}" != "provided" ]]; then
  completion_status="blocked"
  target_state="In Progress"
  reason="missing_review_handoff_input"
fi

completion_time="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
assembled_section_file="$(mktemp "${TMPDIR:-/tmp}/symphony-in-progress-complete-XXXXXX.md")"

{
  if [[ "${handoff_input_status}" == "provided" ]]; then
    sed -e '${/^$/d;}' "${section_file}"
    printf '\n\n'
  fi

  if [[ "${completion_status}" == "completed" ]]; then
    printf '%s\n' "- Completion guard: passed; moving to \`Needs Review\`."
  else
    printf '%s\n' "- Completion guard: blocked; staying in \`In Progress\`."
  fi
  printf '%s\n' "- Completion time (UTC): ${completion_time}"
  printf '%s\n' "- Branch: \`${branch:-unknown}\`"
  printf '%s\n' "- Head SHA: \`${head_sha:-unknown}\`"
  printf '%s\n' "- Workspace status: ${workspace_status}"
  printf '%s\n' "- Required hooks: ${hooks_status}"
  printf '%s\n' "- Upstream: \`${upstream:-none}\`"
  printf '%s\n' "- Push status: ${push_status} (ahead=${ahead_count}, behind=${behind_count})"
  printf '%s\n' "- Review Handoff input: ${handoff_input_status}"
  printf '%s\n' "- Next state: \`${target_state}\`"
  if [[ "${workspace_status}" == "dirty" ]]; then
    printf '%s\n' "- Dirty workspace entries:"
    printf '%s\n' "${status_short}" | sed 's/^/  - `/' | sed 's/$/`/'
  fi
  if [[ -n "${hook_issues}" ]]; then
    printf '%s\n' "- Hook issues:"
    printf '%s' "${hook_issues}" | sed 's/^/  - `/' | sed 's/$/`/'
  fi
  if [[ "${push_status}" != "synced" && "${push_status}" != "not_checked" ]]; then
    printf '%s\n' "- Branch sync issue: ${push_status}; commit, restore, pull/rebase as appropriate, and push until local HEAD matches the upstream branch."
  fi
  if [[ "${handoff_input_status}" != "provided" ]]; then
    printf '%s\n' "- Missing handoff issue: provide the implementation summary, files changed, validation, and reviewer focus via \`--section-file\` or \`--section-stdin\`."
  fi
  if [[ "${completion_status}" == "blocked" ]]; then
    printf '%s\n' "- Next: repair the guard failure, rerun validation if needed, then rerun \`symphony_in_progress_complete.sh\`."
  fi
} > "${assembled_section_file}"

set +e
workpad_append_output="$(
  bash "${WORKPAD_HELPER}" append-section \
    "${helper_args_common[@]}" \
    --section-title "Review Handoff" \
    --section-file "${assembled_section_file}" 2>&1
)"
workpad_rc=$?
set -e

if [[ "${workpad_rc}" -ne 0 ]]; then
  payload="$(jq -cn \
    --arg status "error" \
    --arg issue_identifier "${issue_identifier}" \
    --arg branch "${branch}" \
    --arg head_sha "${head_sha}" \
    --arg from_state "In Progress" \
    --arg to_state "In Progress" \
    --arg reason "workpad_append_failed" \
    --arg workspace_status "${workspace_status}" \
    --arg hooks_status "${hooks_status}" \
    --arg push_status "${push_status}" \
    --arg details "${workpad_append_output}" '
    {
      in_progress_complete_status: $status,
      in_progress_complete_issue_identifier: $issue_identifier,
      in_progress_complete_branch: $branch,
      in_progress_complete_head_sha: $head_sha,
      in_progress_complete_from_state: $from_state,
      in_progress_complete_to_state: $to_state,
      in_progress_complete_reason: $reason,
      in_progress_complete_workspace_status: $workspace_status,
      in_progress_complete_hooks_status: $hooks_status,
      in_progress_complete_push_status: $push_status,
      in_progress_complete_details: $details
    }')"
  emit_payload "${payload}"
  exit 3
fi

workpad_append_status="$(awk -F= '/^WORKPAD_STATUS=/{print $2}' <<< "${workpad_append_output}" | tail -n 1)"
state_status="unchanged"
state_output=""

if [[ "${completion_status}" == "completed" ]]; then
  if [[ ! -x "${STATE_HELPER}" ]]; then
    payload="$(jq -cn \
      --arg status "error" \
      --arg issue_identifier "${issue_identifier}" \
      --arg branch "${branch}" \
      --arg head_sha "${head_sha}" \
      --arg from_state "In Progress" \
      --arg to_state "In Progress" \
      --arg reason "state_helper_unavailable" \
      --arg workspace_status "${workspace_status}" \
      --arg hooks_status "${hooks_status}" \
      --arg push_status "${push_status}" \
      --arg workpad_status "${workpad_append_status}" \
      --arg details "State helper is missing or not executable: ${STATE_HELPER}" '
      {
        in_progress_complete_status: $status,
        in_progress_complete_issue_identifier: $issue_identifier,
        in_progress_complete_branch: $branch,
        in_progress_complete_head_sha: $head_sha,
        in_progress_complete_from_state: $from_state,
        in_progress_complete_to_state: $to_state,
        in_progress_complete_reason: $reason,
        in_progress_complete_workspace_status: $workspace_status,
        in_progress_complete_hooks_status: $hooks_status,
        in_progress_complete_push_status: $push_status,
        in_progress_complete_workpad_status: $workpad_status,
        in_progress_complete_details: $details
      }')"
    emit_payload "${payload}"
    exit 3
  fi

  set +e
  state_output="$(
    bash "${STATE_HELPER}" \
      "${helper_args_common[@]}" \
      --state "Needs Review" \
      --from-state "In Progress" 2>&1
  )"
  state_rc=$?
  set -e

  if [[ "${state_rc}" -ne 0 ]]; then
    payload="$(jq -cn \
      --arg status "error" \
      --arg issue_identifier "${issue_identifier}" \
      --arg branch "${branch}" \
      --arg head_sha "${head_sha}" \
      --arg from_state "In Progress" \
      --arg to_state "In Progress" \
      --arg reason "state_transition_failed" \
      --arg workspace_status "${workspace_status}" \
      --arg hooks_status "${hooks_status}" \
      --arg push_status "${push_status}" \
      --arg workpad_status "${workpad_append_status}" \
      --arg details "${state_output}" '
      {
        in_progress_complete_status: $status,
        in_progress_complete_issue_identifier: $issue_identifier,
        in_progress_complete_branch: $branch,
        in_progress_complete_head_sha: $head_sha,
        in_progress_complete_from_state: $from_state,
        in_progress_complete_to_state: $to_state,
        in_progress_complete_reason: $reason,
        in_progress_complete_workspace_status: $workspace_status,
        in_progress_complete_hooks_status: $hooks_status,
        in_progress_complete_push_status: $push_status,
        in_progress_complete_workpad_status: $workpad_status,
        in_progress_complete_details: $details
      }')"
    emit_payload "${payload}"
    exit 3
  fi

  state_status="$(awk -F= '/^LINEAR_STATE_STATUS=/{print $2}' <<< "${state_output}" | tail -n 1)"
fi

status_short_one_line=""
if [[ -n "${status_short}" ]]; then
  status_short_one_line="$(printf '%s\n' "${status_short}" | join_lines)"
fi
hook_issues_one_line=""
if [[ -n "${hook_issues}" ]]; then
  hook_issues_one_line="$(printf '%s' "${hook_issues}" | join_lines)"
fi

payload="$(jq -cn \
  --arg status "${completion_status}" \
  --arg issue_identifier "${issue_identifier}" \
  --arg branch "${branch}" \
  --arg head_sha "${head_sha}" \
  --arg from_state "In Progress" \
  --arg to_state "${target_state}" \
  --arg reason "${reason}" \
  --arg workspace_status "${workspace_status}" \
  --arg hooks_status "${hooks_status}" \
  --arg push_status "${push_status}" \
  --arg upstream "${upstream}" \
  --arg ahead_count "${ahead_count}" \
  --arg behind_count "${behind_count}" \
  --arg handoff_input_status "${handoff_input_status}" \
  --arg dirty_entries "${status_short_one_line}" \
  --arg hook_issues "${hook_issues_one_line}" \
  --arg workpad_status "${workpad_append_status}" \
  --arg state_status "${state_status}" '
  {
    in_progress_complete_status: $status,
    in_progress_complete_issue_identifier: $issue_identifier,
    in_progress_complete_branch: $branch,
    in_progress_complete_head_sha: $head_sha,
    in_progress_complete_from_state: $from_state,
    in_progress_complete_to_state: $to_state,
    in_progress_complete_reason: $reason,
    in_progress_complete_workspace_status: $workspace_status,
    in_progress_complete_hooks_status: $hooks_status,
    in_progress_complete_push_status: $push_status,
    in_progress_complete_upstream: $upstream,
    in_progress_complete_ahead_count: $ahead_count,
    in_progress_complete_behind_count: $behind_count,
    in_progress_complete_handoff_input_status: $handoff_input_status,
    in_progress_complete_dirty_entries: $dirty_entries,
    in_progress_complete_hook_issues: $hook_issues,
    in_progress_complete_workpad_status: $workpad_status,
    in_progress_complete_state_status: $state_status
  }')"
emit_payload "${payload}"
