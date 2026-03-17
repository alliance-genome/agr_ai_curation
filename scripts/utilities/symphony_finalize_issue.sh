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
  --repo VALUE                 GitHub repo in owner/name form (required for PR merge)
  --branch VALUE               Branch to inspect (default: current git branch)
  --pr-number VALUE            Explicit PR number for merge path
  --cleanup-script PATH        Cleanup helper to run
  --cleanup-max-attempts N     Max attempts for pre-cleanup (default: 2)
  --dry-run                    Do not merge; report intended actions
  --pr-json-file PATH          Test fixture override for `gh pr list` JSON
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
    echo "Missing required argument for PR finalization: --repo" >&2
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
