#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_ready_for_pr.sh --delivery-mode pr|no_pr --issue-identifier ISSUE [options]

Options:
  --delivery-mode VALUE       Required: pr or no_pr
  --issue-identifier VALUE    Required: issue key such as ALL-46
  --branch VALUE              Branch to inspect (default: current git branch)
  --repo VALUE                GitHub repo in owner/name form
  --create-if-missing         Create a PR when none exists (requires --repo and --title)
  --title VALUE               PR title to use when creating
  --body-file PATH            PR body file to use when creating
  --dry-run                   Do not create a PR; report intended action only
  --pr-json-file PATH         Test fixture override for `gh pr list` JSON
  --pr-view-json-file PATH    Test fixture override for `gh pr view` JSON
EOF
}

delivery_mode=""
issue_identifier=""
branch=""
repo=""
create_if_missing=0
title=""
body_file=""
dry_run=0
pr_json_file=""
pr_view_json_file=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --delivery-mode)
      delivery_mode="${2:-}"
      shift 2
      ;;
    --issue-identifier)
      issue_identifier="${2:-}"
      shift 2
      ;;
    --branch)
      branch="${2:-}"
      shift 2
      ;;
    --repo)
      repo="${2:-}"
      shift 2
      ;;
    --create-if-missing)
      create_if_missing=1
      shift
      ;;
    --title)
      title="${2:-}"
      shift 2
      ;;
    --body-file)
      body_file="${2:-}"
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

if [[ -z "${delivery_mode}" || -z "${issue_identifier}" ]]; then
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

if [[ -z "${branch}" ]]; then
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
fi

if [[ "${delivery_mode}" == "no_pr" ]]; then
  echo "READY_FOR_PR_STATUS=skip_no_pr"
  echo "READY_FOR_PR_NEXT_STATE=Human Review Prep"
  echo "READY_FOR_PR_BRANCH=${branch}"
  echo "READY_FOR_PR_MESSAGE=Ticket ${issue_identifier} uses workflow:no-pr; skip GitHub PR work and move directly to Human Review Prep."
  exit 0
fi

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

fetch_pr_view_json() {
  local pr_number="${1}"
  if [[ -n "${pr_view_json_file}" ]]; then
    cat "${pr_view_json_file}"
  else
    local -a cmd=(gh pr view "${pr_number}" --json number,title,url,mergeable,mergeStateStatus,headRefOid,headRefName,baseRefName)
    if [[ -n "${repo}" ]]; then
      cmd+=(--repo "${repo}")
    fi
    "${cmd[@]}"
  fi
}

pr_json="$(fetch_pr_json)"

PR_COUNT="$(printf '%s' "${pr_json}" | jq 'if type == "array" then length else 0 end')"
PR_NUMBER="$(printf '%s' "${pr_json}" | jq -r 'if type == "array" and length > 0 then (.[0].number // "") else "" end')"
PR_TITLE="$(printf '%s' "${pr_json}" | jq -r 'if type == "array" and length > 0 then (.[0].title // "") else "" end')"
PR_URL="$(printf '%s' "${pr_json}" | jq -r 'if type == "array" and length > 0 then (.[0].url // "") else "" end')"

if [[ "${PR_COUNT}" -gt 0 ]]; then
  pr_view_json="$(fetch_pr_view_json "${PR_NUMBER}")"
  PR_NUMBER="$(printf '%s' "${pr_view_json}" | jq -r '.number // $fallback' --arg fallback "${PR_NUMBER}")"
  PR_TITLE="$(printf '%s' "${pr_view_json}" | jq -r '.title // $fallback' --arg fallback "${PR_TITLE}")"
  PR_URL="$(printf '%s' "${pr_view_json}" | jq -r '.url // $fallback' --arg fallback "${PR_URL}")"
  PR_HEAD_REF_NAME="$(printf '%s' "${pr_view_json}" | jq -r '.headRefName // ""')"
  PR_BASE_REF_NAME="$(printf '%s' "${pr_view_json}" | jq -r '.baseRefName // ""')"
  PR_HEAD_SHA="$(printf '%s' "${pr_view_json}" | jq -r '.headRefOid // ""')"
  PR_MERGEABLE="$(printf '%s' "${pr_view_json}" | jq -r '.mergeable // ""')"
  PR_MERGE_STATE_STATUS="$(printf '%s' "${pr_view_json}" | jq -r '.mergeStateStatus // ""')"

  if [[ "${PR_MERGEABLE}" == "CONFLICTING" || "${PR_MERGE_STATE_STATUS}" == "DIRTY" ]]; then
    echo "READY_FOR_PR_STATUS=existing_pr_conflicted"
    echo "READY_FOR_PR_NEXT_STATE=In Progress"
    echo "READY_FOR_PR_BRANCH=${branch}"
    echo "READY_FOR_PR_PR_NUMBER=${PR_NUMBER}"
    echo "READY_FOR_PR_PR_TITLE=${PR_TITLE}"
    echo "READY_FOR_PR_PR_URL=${PR_URL}"
    echo "READY_FOR_PR_PR_HEAD_REF_NAME=${PR_HEAD_REF_NAME}"
    echo "READY_FOR_PR_PR_BASE_REF_NAME=${PR_BASE_REF_NAME}"
    echo "READY_FOR_PR_PR_HEAD_SHA=${PR_HEAD_SHA}"
    echo "READY_FOR_PR_PR_MERGEABLE=${PR_MERGEABLE}"
    echo "READY_FOR_PR_PR_MERGE_STATE_STATUS=${PR_MERGE_STATE_STATUS}"
    echo "READY_FOR_PR_MESSAGE=Open PR #${PR_NUMBER} exists for branch ${branch}, but it has merge conflicts. Refresh the branch against ${PR_BASE_REF_NAME:-the base branch}, push the updated head, and re-run PR gating before treating missing checks as external."
    exit 0
  fi

  echo "READY_FOR_PR_STATUS=existing_pr"
  echo "READY_FOR_PR_NEXT_STATE=Ready for PR"
  echo "READY_FOR_PR_BRANCH=${branch}"
  echo "READY_FOR_PR_PR_NUMBER=${PR_NUMBER}"
  echo "READY_FOR_PR_PR_TITLE=${PR_TITLE}"
  echo "READY_FOR_PR_PR_URL=${PR_URL}"
  echo "READY_FOR_PR_PR_HEAD_REF_NAME=${PR_HEAD_REF_NAME}"
  echo "READY_FOR_PR_PR_BASE_REF_NAME=${PR_BASE_REF_NAME}"
  echo "READY_FOR_PR_PR_HEAD_SHA=${PR_HEAD_SHA}"
  echo "READY_FOR_PR_PR_MERGEABLE=${PR_MERGEABLE}"
  echo "READY_FOR_PR_PR_MERGE_STATE_STATUS=${PR_MERGE_STATE_STATUS}"
  exit 0
fi

if [[ "${create_if_missing}" -ne 1 ]]; then
  echo "READY_FOR_PR_STATUS=missing_pr"
  echo "READY_FOR_PR_NEXT_STATE=Ready for PR"
  echo "READY_FOR_PR_BRANCH=${branch}"
  echo "READY_FOR_PR_MESSAGE=No open PR found for branch ${branch}; create one before leaving the PR lane."
  exit 20
fi

if [[ -z "${repo}" || -z "${title}" ]]; then
  echo "Missing required arguments for PR creation (--repo and --title)." >&2
  exit 2
fi

if (( dry_run == 1 )); then
  echo "READY_FOR_PR_STATUS=dry_run_create"
  echo "READY_FOR_PR_NEXT_STATE=Ready for PR"
  echo "READY_FOR_PR_BRANCH=${branch}"
  echo "READY_FOR_PR_PR_TITLE=${title}"
  echo "READY_FOR_PR_MESSAGE=Would create a PR for branch ${branch}."
  exit 0
fi

cmd=(gh pr create --repo "${repo}" --title "${title}" --head "${branch}")
if [[ -n "${body_file}" ]]; then
  cmd+=(--body-file "${body_file}")
else
  cmd+=(--body "")
fi

create_output="$("${cmd[@]}")"
PR_URL="$(printf '%s\n' "${create_output}" | grep -Eo 'https://[^[:space:]]+/pull/[0-9]+' | tail -1 || true)"
PR_NUMBER="${PR_URL##*/}"

if [[ -z "${PR_NUMBER}" || "${PR_NUMBER}" == "${PR_URL}" ]]; then
  pr_json="$(fetch_pr_json)"
  PR_COUNT="$(printf '%s' "${pr_json}" | jq 'if type == "array" then length else 0 end')"
  PR_NUMBER="$(printf '%s' "${pr_json}" | jq -r 'if type == "array" and length > 0 then (.[0].number // "") else "" end')"
  PR_TITLE="$(printf '%s' "${pr_json}" | jq -r 'if type == "array" and length > 0 then (.[0].title // "") else "" end')"
  PR_URL="$(printf '%s' "${pr_json}" | jq -r 'if type == "array" and length > 0 then (.[0].url // "") else "" end')"
fi

if [[ -z "${PR_NUMBER}" ]]; then
  echo "Unable to determine created PR number for branch ${branch}." >&2
  exit 1
fi

pr_view_json="$(fetch_pr_view_json "${PR_NUMBER}")"
PR_NUMBER="$(printf '%s' "${pr_view_json}" | jq -r '.number // $fallback' --arg fallback "${PR_NUMBER}")"
PR_TITLE="$(printf '%s' "${pr_view_json}" | jq -r '.title // $fallback' --arg fallback "${title}")"
PR_URL="$(printf '%s' "${pr_view_json}" | jq -r '.url // $fallback' --arg fallback "${PR_URL}")"

echo "READY_FOR_PR_STATUS=created_pr"
echo "READY_FOR_PR_NEXT_STATE=Ready for PR"
echo "READY_FOR_PR_BRANCH=${branch}"
echo "READY_FOR_PR_PR_NUMBER=${PR_NUMBER}"
echo "READY_FOR_PR_PR_TITLE=${PR_TITLE}"
echo "READY_FOR_PR_PR_URL=${PR_URL}"
