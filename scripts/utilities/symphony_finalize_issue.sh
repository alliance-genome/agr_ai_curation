#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_finalize_issue.sh --delivery-mode pr|no_pr --workspace-dir DIR [options]

Options:
  --delivery-mode VALUE        Required: pr or no_pr
  --workspace-dir DIR          Required: issue workspace directory
  --issue-identifier VALUE     Optional issue key for reporting
  --compose-project VALUE      Compose project name (default: basename of workspace)
  --repo VALUE                 GitHub repo in owner/name form (default: infer from workspace origin)
  --branch VALUE               Branch to inspect (default: current git branch)
  --pr-number VALUE            Explicit PR number for merge path
  --cleanup-script PATH        Cleanup helper to run
  --cleanup-max-attempts N     Max attempts for pre-cleanup (default: 2)
  --dry-run                    Do not merge; report intended actions
  --pr-json-file PATH          Test fixture override for `gh pr list` JSON
  --pr-view-json-file PATH     Test fixture override for `gh pr view` JSON
  --max-conflict-bounces N     Max Finalizing->In Progress bounces before Blocked (default: 1)
  --conflict-bounce-count N    Current bounce count (caller tracks via Linear history)
EOF
}

delivery_mode=""
workspace_dir=""
issue_identifier=""
compose_project=""
repo=""
branch=""
pr_number=""
cleanup_script=""
cleanup_max_attempts=2
dry_run=0
pr_json_file=""
pr_view_json_file=""
max_conflict_bounces=1
conflict_bounce_count=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --delivery-mode)
      delivery_mode="${2:-}"
      shift 2
      ;;
    --workspace-dir)
      workspace_dir="${2:-}"
      shift 2
      ;;
    --issue-identifier)
      issue_identifier="${2:-}"
      shift 2
      ;;
    --compose-project)
      compose_project="${2:-}"
      shift 2
      ;;
    --repo)
      repo="${2:-}"
      shift 2
      ;;
    --branch)
      branch="${2:-}"
      shift 2
      ;;
    --pr-number)
      pr_number="${2:-}"
      shift 2
      ;;
    --cleanup-script)
      cleanup_script="${2:-}"
      shift 2
      ;;
    --cleanup-max-attempts)
      cleanup_max_attempts="${2:-2}"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --pr-json-file)
      pr_json_file="${2:-}"
      shift 2
      ;;
    --pr-view-json-file)
      pr_view_json_file="${2:-}"
      shift 2
      ;;
    --max-conflict-bounces)
      max_conflict_bounces="${2:-1}"
      shift 2
      ;;
    --conflict-bounce-count)
      conflict_bounce_count="${2:-0}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${delivery_mode}" || -z "${workspace_dir}" ]]; then
  usage
  exit 2
fi

case "${delivery_mode}" in
  pr|no_pr)
    ;;
  *)
    echo "Unsupported delivery mode: ${delivery_mode}" >&2
    exit 2
    ;;
esac

if [[ -z "${compose_project}" ]]; then
  compose_project="$(basename "${workspace_dir}" | tr '[:upper:]' '[:lower:]')"
fi

if [[ -z "${branch}" ]]; then
  branch="$(git -C "${workspace_dir}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
fi

if [[ -z "${cleanup_script}" ]]; then
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cleanup_script="${script_dir}/symphony_pre_merge_cleanup.sh"
fi

infer_repo_from_origin() {
  local remote_url repo_path
  remote_url="$(git -C "${workspace_dir}" config --get remote.origin.url 2>/dev/null || true)"
  if [[ -z "${remote_url}" ]]; then
    return 1
  fi

  case "${remote_url}" in
    git@github.com:*)
      repo_path="${remote_url#git@github.com:}"
      ;;
    ssh://git@github.com/*)
      repo_path="${remote_url#ssh://git@github.com/}"
      ;;
    https://github.com/*)
      repo_path="${remote_url#https://github.com/}"
      ;;
    http://github.com/*)
      repo_path="${remote_url#http://github.com/}"
      ;;
    *)
      return 1
      ;;
  esac

  repo_path="${repo_path%.git}"
  repo_path="${repo_path%/}"
  if [[ "${repo_path}" =~ ^[^/]+/[^/]+$ ]]; then
    printf '%s' "${repo_path}"
    return 0
  fi

  return 1
}

pre_cleanup_status="unavailable"
post_cleanup_status="unavailable"
merge_status="skipped"
merge_message="none"
resolved_pr_number="${pr_number}"
resolved_pr_url=""

run_cleanup() {
  local remove_workspace_flag="$1"
  local attempts="$2"
  local output rc
  set +e
  output="$("${cleanup_script}" --workspace-dir "${workspace_dir}" --compose-project "${compose_project}" --max-attempts "${attempts}" ${remove_workspace_flag} 2>&1)"
  rc=$?
  set -e
  printf '%s' "${output}"
  return "${rc}"
}

extract_cleanup_status() {
  local output="$1"
  local status
  status="$(printf '%s\n' "${output}" | sed -n 's/^CLEANUP_STATUS=//p' | tail -n 1)"
  if [[ -n "${status}" ]]; then
    printf '%s' "${status}"
  else
    printf 'unavailable'
  fi
}

fetch_pr_json() {
  if [[ -n "${pr_json_file}" ]]; then
    cat "${pr_json_file}"
  else
    local -a cmd=(gh pr list --state open --head "${branch}" --json number,title,url,headRefName)
    if [[ -n "${repo}" ]]; then
      cmd+=(--repo "${repo}")
    fi
    "${cmd[@]}"
  fi
}

# ── Conflict analysis helpers ──────────────────────────────────────

fetch_pr_view_json() {
  if [[ -n "${pr_view_json_file}" ]]; then
    cat "${pr_view_json_file}"
  else
    local -a cmd=(gh pr view "${resolved_pr_number}" --json mergeable,mergeStateStatus,files,headRefOid,baseRefName)
    if [[ -n "${repo}" ]]; then
      cmd+=(--repo "${repo}")
    fi
    "${cmd[@]}"
  fi
}

analyze_merge_conflict() {
  # Returns structured conflict context via stdout.
  # Requires: resolved_pr_number, repo, branch, workspace_dir, issue_identifier
  local pr_view_json pr_mergeable conflicting_files sibling_info

  pr_view_json="$(fetch_pr_view_json)"
  pr_mergeable="$(printf '%s' "${pr_view_json}" | jq -r '.mergeable // ""')"

  if [[ "${pr_mergeable}" != "CONFLICTING" ]]; then
    echo "CONFLICT_DETECTED=false"
    return 0
  fi

  echo "CONFLICT_DETECTED=true"

  # Get the list of files changed in this PR
  local pr_files
  pr_files="$(printf '%s' "${pr_view_json}" | jq -r '[.files[]?.path // empty] | join(",")')"
  echo "CONFLICT_PR_FILES=${pr_files}"

  # Identify which files actually conflict by attempting a local merge
  local conflict_files_list=""
  if [[ -d "${workspace_dir}" ]]; then
    set +e
    git -C "${workspace_dir}" fetch origin main --quiet 2>/dev/null
    local merge_test_output
    merge_test_output="$(git -C "${workspace_dir}" merge --no-commit --no-ff origin/main 2>&1)"
    local merge_test_rc=$?
    set -e

    if [[ "${merge_test_rc}" -ne 0 ]]; then
      # Extract conflicting file paths from merge output
      conflict_files_list="$(printf '%s\n' "${merge_test_output}" | sed -n 's/^CONFLICT.*Merge conflict in \(.*\)$/\1/p' | tr '\n' ',')"
      conflict_files_list="${conflict_files_list%,}"  # trim trailing comma
    fi
    # Abort the test merge
    git -C "${workspace_dir}" merge --abort 2>/dev/null || true
  fi
  echo "CONFLICT_FILES=${conflict_files_list}"

  # Identify sibling ticket(s) that landed on main touching the conflicting files
  local sibling_tickets=""
  if [[ -n "${conflict_files_list}" && -d "${workspace_dir}" ]]; then
    local IFS=','
    local -a conflict_arr=( ${conflict_files_list} )
    IFS=' '
    # Look at recent main commits touching conflicting files, extract ticket identifiers
    sibling_tickets="$(
      git -C "${workspace_dir}" log origin/main --oneline -20 -- "${conflict_arr[@]}" 2>/dev/null \
        | grep -oP 'ALL-\d+' \
        | if [[ -n "${issue_identifier}" ]]; then grep -v "${issue_identifier}"; else cat; fi \
        | sort -u \
        | tr '\n' ',' || true
    )"
    sibling_tickets="${sibling_tickets%,}"
  fi
  echo "CONFLICT_SIBLING_TICKETS=${sibling_tickets}"

  local base_ref
  base_ref="$(printf '%s' "${pr_view_json}" | jq -r '.baseRefName // "main"')"
  echo "CONFLICT_BASE_REF=${base_ref}"
}

resolve_open_pr() {
  if [[ -n "${resolved_pr_number}" ]]; then
    return 0
  fi

  local pr_json analysis
  pr_json="$(fetch_pr_json)"
  analysis="$(
    PR_JSON="${pr_json}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["PR_JSON"])
pr = payload[0] if isinstance(payload, list) and payload else {}
print(f"PR_COUNT={len(payload) if isinstance(payload, list) else 0}")
print(f"PR_NUMBER={pr.get('number') or ''}")
print(f"PR_URL={pr.get('url') or ''}")
PY
)"
  eval "${analysis}"
  if [[ "${PR_COUNT}" -gt 0 ]]; then
    resolved_pr_number="${PR_NUMBER}"
    resolved_pr_url="${PR_URL}"
  fi
}

set +e
pre_cleanup_output="$(run_cleanup "" "${cleanup_max_attempts}")"
pre_cleanup_rc=$?
set -e
pre_cleanup_status="$(extract_cleanup_status "${pre_cleanup_output}")"

if [[ "${delivery_mode}" == "pr" ]]; then
  if [[ -z "${repo}" ]]; then
    repo="$(infer_repo_from_origin || true)"
  fi

  if [[ -z "${repo}" ]]; then
    echo "Missing --repo and unable to infer GitHub repo from workspace origin." >&2
    exit 2
  fi

  resolve_open_pr

  if [[ -z "${resolved_pr_number}" ]]; then
    echo "FINALIZE_STATUS=blocked_missing_pr"
    echo "FINALIZE_NEXT_STATE=Blocked"
    echo "FINALIZE_DELIVERY_MODE=${delivery_mode}"
    echo "FINALIZE_ISSUE_IDENTIFIER=${issue_identifier}"
    echo "FINALIZE_BRANCH=${branch}"
    echo "FINALIZE_PRE_CLEANUP_STATUS=${pre_cleanup_status}"
    echo "FINALIZE_MESSAGE=No open PR found for branch ${branch}; cannot complete PR finalization."
    exit 20
  fi

  if (( dry_run == 1 )); then
    merge_status="dry_run"
    merge_message="Would merge PR ${resolved_pr_number}."
  else
    set +e
    merge_output="$(gh pr merge "${resolved_pr_number}" --repo "${repo}" --squash --delete-branch 2>&1)"
    merge_rc=$?
    set -e

    if [[ "${merge_rc}" -ne 0 ]]; then
      set +e
      merge_output="$(gh pr merge "${resolved_pr_number}" --repo "${repo}" --delete-branch 2>&1)"
      merge_rc=$?
      set -e
    fi

    if [[ "${merge_rc}" -ne 0 ]]; then
      # Analyze whether this is a merge conflict (recoverable) or something else
      set +e
      conflict_analysis="$(analyze_merge_conflict)"
      set -e
      conflict_detected="$(printf '%s\n' "${conflict_analysis}" | sed -n 's/^CONFLICT_DETECTED=//p')"

      if [[ "${conflict_detected}" == "true" && "${conflict_bounce_count}" -lt "${max_conflict_bounces}" ]]; then
        # Recoverable merge conflict — bounce to In Progress for LLM resolution
        echo "FINALIZE_STATUS=merge_conflict"
        echo "FINALIZE_NEXT_STATE=In Progress"
        echo "FINALIZE_DELIVERY_MODE=${delivery_mode}"
        echo "FINALIZE_ISSUE_IDENTIFIER=${issue_identifier}"
        echo "FINALIZE_BRANCH=${branch}"
        echo "FINALIZE_PR_NUMBER=${resolved_pr_number}"
        echo "FINALIZE_PR_URL=${resolved_pr_url}"
        echo "FINALIZE_PRE_CLEANUP_STATUS=${pre_cleanup_status}"
        echo "FINALIZE_MERGE_OUTPUT=$(printf '%s' "${merge_output}" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g')"
        # Emit conflict details for the LLM to use in its workpad comment
        printf '%s\n' "${conflict_analysis}"
        cat <<INST
FINALIZE_MESSAGE=PR #${resolved_pr_number} has merge conflicts. Write a workpad comment with the conflict details above, then move to In Progress and stop this run. The In Progress lane will handle conflict resolution.
INST
        exit 22
      fi

      # Non-conflict failure or bounce limit exceeded — fall through to Blocked
      echo "FINALIZE_STATUS=blocked_merge_failed"
      echo "FINALIZE_NEXT_STATE=Blocked"
      echo "FINALIZE_DELIVERY_MODE=${delivery_mode}"
      echo "FINALIZE_ISSUE_IDENTIFIER=${issue_identifier}"
      echo "FINALIZE_BRANCH=${branch}"
      echo "FINALIZE_PR_NUMBER=${resolved_pr_number}"
      echo "FINALIZE_PR_URL=${resolved_pr_url}"
      echo "FINALIZE_PRE_CLEANUP_STATUS=${pre_cleanup_status}"
      echo "FINALIZE_MESSAGE=$(printf '%s' "${merge_output}" | tr '\n' ' ' | sed 's/[[:space:]]\+/ /g')"
      exit 21
    fi

    merge_status="merged"
    merge_message="Merged PR ${resolved_pr_number}."
  fi
else
  merge_status="skipped_no_pr"
  merge_message="No PR merge required for delivery mode no_pr."
fi

set +e
post_cleanup_output="$(run_cleanup "--remove-workspace" 1)"
post_cleanup_rc=$?
set -e
post_cleanup_status="$(extract_cleanup_status "${post_cleanup_output}")"

if (( dry_run == 1 )); then
  final_status="dry_run"
else
  if [[ "${delivery_mode}" == "pr" ]]; then
    final_status="merged"
  else
    final_status="finalized_no_pr"
  fi
fi

echo "FINALIZE_STATUS=${final_status}"
echo "FINALIZE_NEXT_STATE=Done"
echo "FINALIZE_DELIVERY_MODE=${delivery_mode}"
echo "FINALIZE_ISSUE_IDENTIFIER=${issue_identifier}"
echo "FINALIZE_BRANCH=${branch}"
echo "FINALIZE_PR_NUMBER=${resolved_pr_number}"
echo "FINALIZE_PR_URL=${resolved_pr_url}"
echo "FINALIZE_PRE_CLEANUP_STATUS=${pre_cleanup_status}"
echo "FINALIZE_POST_CLEANUP_STATUS=${post_cleanup_status}"
echo "FINALIZE_MERGE_STATUS=${merge_status}"
echo "FINALIZE_MESSAGE=${merge_message}"
