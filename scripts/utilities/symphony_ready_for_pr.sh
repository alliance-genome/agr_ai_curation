#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_ready_for_pr.sh --delivery-mode pr|no_pr --issue-identifier ISSUE [options]

Options:
  --delivery-mode VALUE          Required: pr or no_pr
  --issue-identifier VALUE       Required: issue key such as ALL-46
  --branch VALUE                 Branch to inspect (default: current git branch)
  --repo VALUE                   GitHub repo in owner/name form
  --create-if-missing            Create a PR when none exists (requires --repo and --title)
  --title VALUE                  PR title to use when creating
  --body-file PATH               PR body file to use when creating
  --wait-for-review-seconds N    Wait N seconds for Claude Code review after PR success (default: 0)
  --review-poll-seconds N        Poll interval during Claude wait (default: 30)
  --review-author VALUE          GitHub login to watch for reviews (default: claude)
  --dry-run                      Do not create a PR; report intended action only
  --pr-json-file PATH            Test fixture override for `gh pr list` JSON
  --pr-view-json-file PATH       Test fixture override for `gh pr view` JSON
EOF
}

delivery_mode=""
issue_identifier=""
branch=""
repo=""
create_if_missing=0
title=""
body_file=""
wait_for_review_seconds=0
review_poll_seconds=30
review_author="claude"
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
    --wait-for-review-seconds)
      wait_for_review_seconds="${2:-}"
      shift 2
      ;;
    --review-poll-seconds)
      review_poll_seconds="${2:-}"
      shift 2
      ;;
    --review-author)
      review_author="${2:-}"
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

if ! [[ "${wait_for_review_seconds}" =~ ^[0-9]+$ ]]; then
  echo "wait-for-review-seconds must be a non-negative integer, got: ${wait_for_review_seconds}" >&2
  exit 2
fi
if ! [[ "${review_poll_seconds}" =~ ^[0-9]+$ ]]; then
  echo "review-poll-seconds must be a non-negative integer, got: ${review_poll_seconds}" >&2
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Claude review check via unified loop script ─────────────────────
#
# Called after a PR is successfully found or created. Delegates to
# symphony_claude_review_loop.sh which handles waiting, re-review
# requests, round counting, and report generation in one place.
#
# Sets these variables for the caller:
#   CLAUDE_STATUS       quiet | detected | maxed_out | error
#   CLAUDE_REPORT_FILE  path to the report file (empty if quiet)

emit_pr_success_with_claude_check() {
  local status="$1"
  local pr_num="$2"
  local pr_created_at="${3:-}"

  CLAUDE_STATUS="quiet"
  CLAUDE_REPORT_FILE=""

  if (( wait_for_review_seconds <= 0 )); then
    echo "READY_FOR_PR_CLAUDE_STATUS=${CLAUDE_STATUS}"
    cat <<INST
READY_FOR_PR_INSTRUCTIONS=PR #${pr_num} is open. No Claude review wait requested. Proceed to gate GitHub checks, then move to Human Review Prep.
INST
    return 0
  fi

  if [[ -z "${repo}" ]]; then
    echo "READY_FOR_PR_CLAUDE_WARNING=--wait-for-review-seconds requires --repo; skipping Claude wait" >&2
    echo "READY_FOR_PR_CLAUDE_STATUS=${CLAUDE_STATUS}"
    cat <<INST
READY_FOR_PR_INSTRUCTIONS=PR #${pr_num} is open. Claude Code did not leave feedback (--repo not set). Proceed to gate GitHub checks, then move to Human Review Prep.
INST
    return 0
  fi

  local loop_script="${SCRIPT_DIR}/symphony_claude_review_loop.sh"
  if [[ ! -x "${loop_script}" ]]; then
    echo "READY_FOR_PR_CLAUDE_WARNING=symphony_claude_review_loop.sh not found; skipping Claude wait" >&2
    echo "READY_FOR_PR_CLAUDE_STATUS=${CLAUDE_STATUS}"
    cat <<INST
READY_FOR_PR_INSTRUCTIONS=PR #${pr_num} is open. Claude review loop script not found. Proceed to gate GitHub checks, then move to Human Review Prep.
INST
    return 0
  fi

  # Use PR creation time as --since floor. Falls back to HEAD commit time.
  local since_ts="${pr_created_at}"
  if [[ -z "${since_ts}" ]]; then
    since_ts="$(git show -s --format=%cI HEAD 2>/dev/null || date -Iseconds)"
  fi

  echo "READY_FOR_PR_CLAUDE_WAIT_STARTED=true" >&2
  echo "Waiting up to ${wait_for_review_seconds}s for ${review_author} to review PR #${pr_num}..." >&2

  local loop_output
  set +e
  loop_output="$(bash "${loop_script}" \
    --repo "${repo}" \
    --pr "${pr_num}" \
    --since "${since_ts}" \
    --author "${review_author}" \
    --wait-seconds "${wait_for_review_seconds}" \
    --poll-seconds "${review_poll_seconds}" \
    --max-rounds 3)"
  local loop_rc=$?
  set -e

  # Extract variables from loop output
  local loop_status loop_report loop_round loop_max
  loop_status="$(echo "${loop_output}" | grep '^CLAUDE_LOOP_STATUS=' | cut -d= -f2)"
  loop_report="$(echo "${loop_output}" | grep '^CLAUDE_LOOP_REPORT_FILE=' | cut -d= -f2)"
  loop_round="$(echo "${loop_output}" | grep '^CLAUDE_LOOP_ROUND=' | cut -d= -f2)"
  loop_max="$(echo "${loop_output}" | grep '^CLAUDE_LOOP_MAX_ROUNDS=' | cut -d= -f2)"

  CLAUDE_STATUS="${loop_status:-quiet}"
  CLAUDE_REPORT_FILE="${loop_report:-}"

  echo "READY_FOR_PR_CLAUDE_STATUS=${CLAUDE_STATUS}"

  if [[ "${CLAUDE_STATUS}" == "detected" && -n "${CLAUDE_REPORT_FILE}" ]]; then
    echo "READY_FOR_PR_CLAUDE_REPORT_FILE=${CLAUDE_REPORT_FILE}"
    echo "READY_FOR_PR_CLAUDE_ROUND=${loop_round:-1}"
    echo "READY_FOR_PR_CLAUDE_MAX_ROUNDS=${loop_max:-3}"

    cat <<INST
READY_FOR_PR_INSTRUCTIONS=Claude Code left a review on PR #${pr_num} (round ${loop_round:-1}/${loop_max:-3}). YOU MUST:
1. Read the full Claude review report at: ${CLAUDE_REPORT_FILE}
2. For EACH comment, decide: fix it, or skip it with a concrete reason.
   Default posture: fix most suggestions. Claude is usually right about code quality,
   missing edge cases, and style issues. Only skip if the suggestion is incorrect,
   redundant, or clearly outside the ticket scope.
3. Write your decisions into the workpad as a 'Claude Feedback Disposition' section:
   - For each comment: one line with 'fixed', 'deferred', or 'not taken' plus the reason.
4. Move the issue to In Progress and stop this run.
5. The next In Progress agent will read the workpad and the PR comments, then implement your decisions.
INST
  elif [[ "${CLAUDE_STATUS}" == "maxed_out" ]]; then
    cat <<INST
READY_FOR_PR_INSTRUCTIONS=PR #${pr_num} has completed ${loop_round:-3}/${loop_max:-3} Claude review rounds. Proceed to Human Review Prep — further automated review rounds would not be productive.
INST
  else
    cat <<INST
READY_FOR_PR_INSTRUCTIONS=PR #${pr_num} is open. Claude Code did not leave feedback within the ${wait_for_review_seconds}s wait window. Proceed to gate GitHub checks, then move to Human Review Prep.
INST
  fi
}

if [[ "${delivery_mode}" == "no_pr" ]]; then
  echo "READY_FOR_PR_STATUS=skip_no_pr"
  echo "READY_FOR_PR_NEXT_STATE=Human Review Prep"
  echo "READY_FOR_PR_BRANCH=${branch}"
  echo "READY_FOR_PR_MESSAGE=Ticket ${issue_identifier} uses workflow:no-pr; skip GitHub PR work and move directly to Human Review Prep."
  cat <<'INST'
READY_FOR_PR_INSTRUCTIONS=No PR required for this ticket. Move directly to Human Review Prep and stop this run.
INST
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
    local -a cmd=(gh pr view "${pr_number}" --json number,title,url,mergeable,mergeStateStatus,headRefOid,headRefName,baseRefName,createdAt)
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
  PR_CREATED_AT="$(printf '%s' "${pr_view_json}" | jq -r '.createdAt // ""')"

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
    cat <<INST
READY_FOR_PR_INSTRUCTIONS=PR #${PR_NUMBER} has merge conflicts. Refresh the branch against ${PR_BASE_REF_NAME:-the base branch}, push the updated head, move to In Progress, and stop this run.
INST
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
  emit_pr_success_with_claude_check "existing_pr" "${PR_NUMBER}" "${PR_CREATED_AT}"
  exit 0
fi

if [[ "${create_if_missing}" -ne 1 ]]; then
  echo "READY_FOR_PR_STATUS=missing_pr"
  echo "READY_FOR_PR_NEXT_STATE=Ready for PR"
  echo "READY_FOR_PR_BRANCH=${branch}"
  echo "READY_FOR_PR_MESSAGE=No open PR found for branch ${branch}; create one before leaving the PR lane."
  cat <<INST
READY_FOR_PR_INSTRUCTIONS=No open PR found for branch ${branch}. Create the PR using gh pr create, then re-run this script.
INST
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
  cat <<'INST'
READY_FOR_PR_INSTRUCTIONS=Dry run only. No PR was created. Re-run without --dry-run to create the PR.
INST
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
PR_CREATED_AT="$(printf '%s' "${pr_view_json}" | jq -r '.createdAt // ""')"

echo "READY_FOR_PR_STATUS=created_pr"
echo "READY_FOR_PR_NEXT_STATE=Ready for PR"
echo "READY_FOR_PR_BRANCH=${branch}"
echo "READY_FOR_PR_PR_NUMBER=${PR_NUMBER}"
echo "READY_FOR_PR_PR_TITLE=${PR_TITLE}"
echo "READY_FOR_PR_PR_URL=${PR_URL}"
emit_pr_success_with_claude_check "created_pr" "${PR_NUMBER}" "${PR_CREATED_AT}"
