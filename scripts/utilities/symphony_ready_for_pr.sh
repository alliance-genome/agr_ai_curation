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
  --create-if-missing            Create a PR when none exists (requires --repo)
  --title VALUE                  PR title to use when creating (default: inferred)
  --body-file PATH               PR body file to use when creating
  --wait-for-review-seconds N    Wait N seconds for Claude Code review after PR success (default: 0)
  --review-poll-seconds N        Poll interval during Claude wait (default: 30)
  --wait-for-checks-seconds N    Wait N seconds for GitHub checks to settle (default: 0)
  --check-poll-seconds N         Poll interval during GitHub check wait (default: 30)
  --review-author VALUE          GitHub login to watch for reviews (default: claude)
  --disposition-file PATH        File with feedback disposition context (passed to Claude review loop)
  --auto-bounce-claude-feedback  Move to In Progress when Claude feedback is detected (default)
  --no-auto-bounce-claude-feedback
  --auto-bounce-failing-checks   Move to In Progress when GitHub checks fail (default)
  --no-auto-bounce-failing-checks
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
wait_for_checks_seconds=0
check_poll_seconds=30
review_author="claude"
disposition_file=""
auto_bounce_claude_feedback=1
auto_bounce_failing_checks=1
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
    --wait-for-checks-seconds)
      wait_for_checks_seconds="${2:-}"
      shift 2
      ;;
    --check-poll-seconds)
      check_poll_seconds="${2:-}"
      shift 2
      ;;
    --review-author)
      review_author="${2:-}"
      shift 2
      ;;
    --disposition-file)
      disposition_file="${2:-}"
      shift 2
      ;;
    --auto-bounce-claude-feedback)
      auto_bounce_claude_feedback=1
      shift
      ;;
    --no-auto-bounce-claude-feedback)
      auto_bounce_claude_feedback=0
      shift
      ;;
    --auto-bounce-failing-checks)
      auto_bounce_failing_checks=1
      shift
      ;;
    --no-auto-bounce-failing-checks)
      auto_bounce_failing_checks=0
      shift
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
if ! [[ "${wait_for_checks_seconds}" =~ ^[0-9]+$ ]]; then
  echo "wait-for-checks-seconds must be a non-negative integer, got: ${wait_for_checks_seconds}" >&2
  exit 2
fi
if ! [[ "${check_poll_seconds}" =~ ^[0-9]+$ ]]; then
  echo "check-poll-seconds must be a non-negative integer, got: ${check_poll_seconds}" >&2
  exit 2
fi
if (( wait_for_checks_seconds > 0 && check_poll_seconds == 0 )); then
  echo "check-poll-seconds must be greater than 0 when wait-for-checks-seconds is non-zero" >&2
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
CLAUDE_LOOP_HELPER="${SYMPHONY_READY_FOR_PR_CLAUDE_LOOP_HELPER:-${SCRIPT_DIR}/symphony_claude_review_loop.sh}"
WORKPAD_HELPER="${SYMPHONY_READY_FOR_PR_WORKPAD_HELPER:-${SCRIPT_DIR}/symphony_linear_workpad.sh}"
STATE_HELPER="${SYMPHONY_READY_FOR_PR_STATE_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_state.sh}"

is_base_like_branch() {
  local branch_name="$1"

  case "${branch_name}" in
    ""|HEAD|main|master|trunk|develop|development)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
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

infer_repo_from_origin() {
  local remote normalized

  remote="$(git remote get-url origin 2>/dev/null || true)"
  if [[ -z "${remote}" ]]; then
    return 1
  fi

  case "${remote}" in
    git@github.com:*)
      normalized="${remote#git@github.com:}"
      ;;
    https://github.com/*)
      normalized="${remote#https://github.com/}"
      ;;
    ssh://git@github.com/*)
      normalized="${remote#ssh://git@github.com/}"
      ;;
    *)
      return 1
      ;;
  esac

  normalized="${normalized%.git}"
  if [[ "${normalized}" == */* ]]; then
    printf '%s\n' "${normalized}"
    return 0
  fi

  return 1
}

infer_pr_title() {
  local subject

  subject="$(git log -1 --format=%s 2>/dev/null || true)"
  if [[ -z "${subject}" ]]; then
    subject="${branch}"
  fi

  if [[ "${subject}" == *"${issue_identifier}"* ]]; then
    printf '%s\n' "${subject}"
  else
    printf '%s: %s\n' "${issue_identifier}" "${subject}"
  fi
}

if [[ -z "${repo}" && -z "${pr_json_file}" && -z "${pr_view_json_file}" ]]; then
  repo="$(infer_repo_from_origin || true)"
fi

auto_bounce_to_in_progress_for_claude() {
  local pr_num="$1"
  local report_file="$2"
  local loop_round="${3:-1}"
  local loop_max="${4:-5}"
  local section_file workpad_output workpad_rc state_output state_rc

  section_file="$(mktemp /tmp/ready-for-pr-claude-handoff-XXXXXX.md)"
  cat > "${section_file}" <<EOF
- Outcome: Claude Code left PR feedback on PR #${pr_num}; Ready for PR moved this issue back to In Progress automatically.
- PR: ${PR_URL:-}
- Branch: ${branch}
- Head SHA: ${PR_HEAD_SHA:-}
- Claude review round: ${loop_round}/${loop_max}
- Claude report: ${report_file}
- Implementation focus: triage the latest Claude feedback first. If it is actionable, fix it, push, and move to Needs Review. If it is an approval, confirmation, or otherwise has no actionable work, write that disposition and move directly to Human Review Prep without editing code.
EOF

  set +e
  workpad_output="$(bash "${WORKPAD_HELPER}" append-section \
    --issue-identifier "${issue_identifier}" \
    --section-title "PR Handoff" \
    --section-file "${section_file}" 2>&1)"
  workpad_rc=$?
  set -e
  rm -f "${section_file}"

  if (( workpad_rc != 0 )); then
    echo "READY_FOR_PR_CLAUDE_ACTION=bounce_failed"
    echo "READY_FOR_PR_CLAUDE_BOUNCE_ERROR=Failed to append PR Handoff: ${workpad_output//$'\n'/ }"
    return 30
  fi

  set +e
  state_output="$(bash "${STATE_HELPER}" \
    --issue-identifier "${issue_identifier}" \
    --state "In Progress" \
    --from-state "Ready for PR" 2>&1)"
  state_rc=$?
  set -e

  if (( state_rc != 0 )); then
    echo "READY_FOR_PR_CLAUDE_ACTION=bounce_failed"
    echo "READY_FOR_PR_CLAUDE_BOUNCE_ERROR=Failed to move issue to In Progress: ${state_output//$'\n'/ }"
    return 31
  fi

  echo "READY_FOR_PR_CLAUDE_ACTION=bounced_to_in_progress"
  echo "READY_FOR_PR_NEXT_STATE=In Progress"
}

auto_bounce_to_in_progress_for_checks() {
  local pr_num="$1"
  local check_report_file="$2"
  local section_file workpad_output workpad_rc state_output state_rc

  section_file="$(mktemp /tmp/ready-for-pr-check-handoff-XXXXXX.md)"
  {
    cat <<EOF
- Outcome: GitHub PR checks failed on PR #${pr_num}; Ready for PR moved this issue back to In Progress automatically.
- PR: ${PR_URL:-}
- Branch: ${branch}
- Head SHA: ${PR_HEAD_SHA:-}
- Failed checks:
EOF
    if [[ -s "${check_report_file}" ]]; then
      sed 's/^/  /' "${check_report_file}"
    else
      echo "  - Failed check details were unavailable."
    fi
    cat <<'EOF'
- Implementation focus: address the failed PR checks first, then push the fix and move to Needs Review. For security scanners such as GitGuardian, inspect the PR comment/check details for the affected file and line.
EOF
  } > "${section_file}"

  set +e
  workpad_output="$(bash "${WORKPAD_HELPER}" append-section \
    --issue-identifier "${issue_identifier}" \
    --section-title "PR Handoff" \
    --section-file "${section_file}" 2>&1)"
  workpad_rc=$?
  set -e
  rm -f "${section_file}"

  if (( workpad_rc != 0 )); then
    echo "READY_FOR_PR_CHECK_ACTION=bounce_failed"
    echo "READY_FOR_PR_CHECK_BOUNCE_ERROR=Failed to append PR Handoff: ${workpad_output//$'\n'/ }"
    return 32
  fi

  set +e
  state_output="$(bash "${STATE_HELPER}" \
    --issue-identifier "${issue_identifier}" \
    --state "In Progress" \
    --from-state "Ready for PR" 2>&1)"
  state_rc=$?
  set -e

  if (( state_rc != 0 )); then
    echo "READY_FOR_PR_CHECK_ACTION=bounce_failed"
    echo "READY_FOR_PR_CHECK_BOUNCE_ERROR=Failed to move issue to In Progress: ${state_output//$'\n'/ }"
    return 33
  fi

  echo "READY_FOR_PR_CHECK_ACTION=bounced_to_in_progress"
  echo "READY_FOR_PR_NEXT_STATE=In Progress"
}

analyze_check_rollup() {
  local rollup_json="$1"
  local failures_file="$2"
  local pending_file="$3"

  CHECK_ROLLUP_JSON="${rollup_json}" python3 - "${failures_file}" "${pending_file}" <<'PY'
import json
import os
import sys

failures_path = sys.argv[1]
pending_path = sys.argv[2]

try:
    data = json.loads(os.environ.get("CHECK_ROLLUP_JSON", "{}") or "{}")
except Exception as exc:
    print("CHECK_STATUS=error")
    print(f"CHECK_ERROR={type(exc).__name__}: {exc}")
    sys.exit(2)

if isinstance(data, dict):
    rollup = data.get("statusCheckRollup") or []
elif isinstance(data, list):
    rollup = data
else:
    rollup = []

success_values = {"SUCCESS", "SKIPPED", "NEUTRAL"}
pending_values = {"", "EXPECTED", "PENDING", "QUEUED", "REQUESTED", "WAITING", "IN_PROGRESS"}
failure_values = {"FAILURE", "ERROR", "TIMED_OUT", "ACTION_REQUIRED", "CANCELLED", "STARTUP_FAILURE"}

failures = []
pending = []

def describe(item, state):
    name = (
        item.get("name")
        or item.get("context")
        or item.get("workflowName")
        or "unknown check"
    )
    url = item.get("detailsUrl") or item.get("targetUrl") or item.get("url") or ""
    if url:
        return f"- {name}: {state} ({url})"
    return f"- {name}: {state}"

for item in rollup:
    if not isinstance(item, dict):
        continue

    typename = item.get("__typename") or ""
    status = str(item.get("status") or "").upper()
    conclusion = str(item.get("conclusion") or "").upper()
    state = str(item.get("state") or "").upper()

    if typename == "StatusContext" or (state and not status):
        if state in success_values:
            continue
        if state in pending_values:
            pending.append(describe(item, state or "PENDING"))
            continue
        failures.append(describe(item, state or "UNKNOWN"))
        continue

    if status and status != "COMPLETED":
        pending.append(describe(item, status))
        continue

    if conclusion in success_values:
        continue
    if conclusion in pending_values:
        pending.append(describe(item, conclusion or status or "PENDING"))
        continue
    if conclusion in failure_values:
        failures.append(describe(item, conclusion))
        continue

    failures.append(describe(item, conclusion or status or "UNKNOWN"))

with open(failures_path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(failures))
    if failures:
        fh.write("\n")

with open(pending_path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(pending))
    if pending:
        fh.write("\n")

if failures:
    status = "failed"
elif pending:
    status = "pending"
elif rollup:
    status = "clean"
else:
    status = "missing"

print(f"CHECK_STATUS={status}")
print(f"CHECK_TOTAL={len(rollup)}")
print(f"CHECK_FAILED_COUNT={len(failures)}")
print(f"CHECK_PENDING_COUNT={len(pending)}")
PY
}

gate_github_checks() {
  local pr_num="$1"
  local current_json="${2:-}"
  local deadline failures_file pending_file check_output check_rc status total failed_count pending_count now
  local first_fetch=1

  deadline=$(( $(date +%s) + wait_for_checks_seconds ))

  while true; do
    if (( first_fetch == 1 )) && [[ -n "${current_json}" ]]; then
      first_fetch=0
    else
      current_json="$(fetch_pr_view_json "${pr_num}")"
    fi

    failures_file="$(mktemp /tmp/ready-for-pr-failed-checks-XXXXXX.txt)"
    pending_file="$(mktemp /tmp/ready-for-pr-pending-checks-XXXXXX.txt)"

    set +e
    check_output="$(analyze_check_rollup "${current_json}" "${failures_file}" "${pending_file}")"
    check_rc=$?
    set -e

    if (( check_rc != 0 )); then
      rm -f "${failures_file}" "${pending_file}"
      printf '%s\n' "${check_output}"
      echo "READY_FOR_PR_CHECK_STATUS=error"
      return 2
    fi

    status="$(extract_kv "CHECK_STATUS" "${check_output}")"
    total="$(extract_kv "CHECK_TOTAL" "${check_output}")"
    failed_count="$(extract_kv "CHECK_FAILED_COUNT" "${check_output}")"
    pending_count="$(extract_kv "CHECK_PENDING_COUNT" "${check_output}")"

    case "${status}" in
      clean)
        rm -f "${failures_file}" "${pending_file}"
        echo "READY_FOR_PR_CHECK_STATUS=clean"
        echo "READY_FOR_PR_CHECK_TOTAL=${total:-0}"
        cat <<INST
READY_FOR_PR_INSTRUCTIONS=PR #${pr_num} is open, Claude feedback does not require another implementation pass, and GitHub checks are clean. Write PR Handoff, move to Human Review Prep, and stop this run.
INST
        return 0
        ;;
      failed)
        rm -f "${pending_file}"
        echo "READY_FOR_PR_CHECK_STATUS=failed"
        echo "READY_FOR_PR_CHECK_TOTAL=${total:-0}"
        echo "READY_FOR_PR_CHECK_FAILED_COUNT=${failed_count:-0}"
        echo "READY_FOR_PR_CHECK_FAILURES_FILE=${failures_file}"
        return 10
        ;;
      pending|missing)
        now="$(date +%s)"
        if (( wait_for_checks_seconds > 0 && now < deadline )); then
          rm -f "${failures_file}" "${pending_file}"
          sleep "${check_poll_seconds}"
          continue
        fi

        rm -f "${failures_file}"
        echo "READY_FOR_PR_CHECK_STATUS=${status}"
        echo "READY_FOR_PR_CHECK_TOTAL=${total:-0}"
        echo "READY_FOR_PR_CHECK_PENDING_COUNT=${pending_count:-0}"
        if [[ -s "${pending_file}" ]]; then
          echo "READY_FOR_PR_CHECK_PENDING_FILE=${pending_file}"
        else
          rm -f "${pending_file}"
        fi
        cat <<INST
READY_FOR_PR_INSTRUCTIONS=PR #${pr_num} still has ${status} GitHub checks after waiting ${wait_for_checks_seconds}s. Do not move to Human Review Prep yet. Write the pending/missing check status into PR Handoff and keep the issue in Ready for PR or move to Blocked only if this is a true external service problem.
INST
        return 11
        ;;
      *)
        rm -f "${failures_file}" "${pending_file}"
        echo "READY_FOR_PR_CHECK_STATUS=error"
        echo "READY_FOR_PR_CHECK_ERROR=unknown_status:${status}"
        return 2
        ;;
    esac
  done
}

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
  local pr_num="$1"
  local pr_created_at="${2:-}"
  local loop_round=""
  local loop_max=""

  CLAUDE_STATUS="quiet"
  CLAUDE_REPORT_FILE=""

  if [[ -z "${repo}" ]]; then
    echo "READY_FOR_PR_CLAUDE_WARNING=Could not infer --repo; skipping Claude feedback scan" >&2
    echo "READY_FOR_PR_CLAUDE_STATUS=${CLAUDE_STATUS}"
  else
    local loop_script="${CLAUDE_LOOP_HELPER}"
    if [[ ! -x "${loop_script}" ]]; then
    echo "READY_FOR_PR_CLAUDE_WARNING=symphony_claude_review_loop.sh not found; skipping Claude wait" >&2
    echo "READY_FOR_PR_CLAUDE_STATUS=${CLAUDE_STATUS}"
    else

      # Use PR creation time as --since floor. Falls back to HEAD commit time.
      local since_ts="${pr_created_at}"
      if [[ -z "${since_ts}" ]]; then
        since_ts="$(git show -s --format=%cI HEAD 2>/dev/null || date -Iseconds)"
      fi

      echo "READY_FOR_PR_CLAUDE_WAIT_STARTED=true" >&2
      if (( wait_for_review_seconds > 0 )); then
        echo "Waiting up to ${wait_for_review_seconds}s for ${review_author} to review PR #${pr_num}..." >&2
      else
        echo "Inspecting existing ${review_author} feedback for PR #${pr_num} without waiting..." >&2
      fi

      local loop_output
      set +e
      local -a loop_cmd=(bash "${loop_script}"
        --repo "${repo}"
        --pr "${pr_num}"
        --since "${since_ts}"
        --author "${review_author}"
        --wait-seconds "${wait_for_review_seconds}"
        --poll-seconds "${review_poll_seconds}"
        --max-rounds 5)
      if [[ -n "${disposition_file}" && -s "${disposition_file}" ]]; then
        loop_cmd+=(--disposition-file "${disposition_file}")
      fi
      loop_output="$("${loop_cmd[@]}")"
      local loop_rc=$?
      set -e

      # Extract variables from loop output
      local loop_status loop_report
      loop_status="$(extract_kv "CLAUDE_LOOP_STATUS" "${loop_output}")"
      loop_report="$(extract_kv "CLAUDE_LOOP_REPORT_FILE" "${loop_output}")"
      loop_round="$(extract_kv "CLAUDE_LOOP_ROUND" "${loop_output}")"
      loop_max="$(extract_kv "CLAUDE_LOOP_MAX_ROUNDS" "${loop_output}")"

      if (( loop_rc != 0 && loop_rc != 10 )); then
        echo "READY_FOR_PR_CLAUDE_STATUS=error"
        echo "READY_FOR_PR_CLAUDE_ERROR=Claude review loop failed with exit code ${loop_rc}"
        cat <<INST
READY_FOR_PR_INSTRUCTIONS=Claude review loop failed before PR gating could complete. Write the error into PR Handoff, move to Blocked or In Progress based on whether the failure is infrastructure or implementation-related, and stop this run.
INST
        return "${loop_rc}"
      fi

      CLAUDE_STATUS="${loop_status:-quiet}"
      CLAUDE_REPORT_FILE="${loop_report:-}"

      echo "READY_FOR_PR_CLAUDE_STATUS=${CLAUDE_STATUS}"

      if [[ "${CLAUDE_STATUS}" == "detected" && -n "${CLAUDE_REPORT_FILE}" ]]; then
        echo "READY_FOR_PR_CLAUDE_REPORT_FILE=${CLAUDE_REPORT_FILE}"
        echo "READY_FOR_PR_CLAUDE_ROUND=${loop_round:-1}"
        echo "READY_FOR_PR_CLAUDE_MAX_ROUNDS=${loop_max:-5}"

        if (( auto_bounce_claude_feedback == 1 )); then
          if ! auto_bounce_to_in_progress_for_claude "${pr_num}" "${CLAUDE_REPORT_FILE}" "${loop_round:-1}" "${loop_max:-5}"; then
            cat <<INST
READY_FOR_PR_INSTRUCTIONS=Claude Code left feedback on PR #${pr_num}, but the helper could not move the issue back to In Progress automatically. Do not edit, commit, amend, push, or rerun review from Ready for PR. Record the failure and move the issue manually.
INST
            return 30
          fi
          cat <<INST
READY_FOR_PR_INSTRUCTIONS=Claude Code left feedback on PR #${pr_num}. The helper already wrote PR Handoff, moved the issue to In Progress, and stopped this PR lane. Do not edit, commit, amend, push, or rerun review from Ready for PR.
INST
          return 0
        fi

        cat <<INST
READY_FOR_PR_INSTRUCTIONS=Claude Code left a review on PR #${pr_num} (round ${loop_round:-1}/${loop_max:-5}). YOU MUST:
1. Read the latest Claude feedback report at: ${CLAUDE_REPORT_FILE}
2. Check whether the review is truly clean:
   - A review is clean ONLY if it is 'Approve' with zero findings of any kind — no critical
     issues, no warnings, no suggestions, no improvement ideas. Pure approval with no comments.
   - If all findings in the report were already addressed in prior runs (check the workpad
     for existing 'Claude Feedback Disposition' entries), the review is already resolved.
   - Only in these two cases: write a short workpad note confirming, then proceed to gate
     GitHub checks and move to Human Review Prep. Do NOT bounce to In Progress.
3. If the review contains ANY findings — critical, warning, suggestion, or improvement idea:
   a. Default posture: FIX THEM. Claude suggestions improve code quality and polish.
      Treat suggestions the same as warnings — they are work items, not optional notes.
      Only skip a suggestion if it is factually incorrect, contradicts the ticket scope,
      or would introduce a regression. "Non-blocking" does not mean "optional."
   b. Write your plan into the workpad as a 'Claude Feedback Disposition' section:
      - For each finding: one line with 'will fix' or 'not taken: <concrete reason>'.
      - Do not use 'deferred' — either fix it now or explain why it is wrong/out of scope.
   c. Move the issue to In Progress and stop this run.
   d. The next In Progress agent will address the latest feedback first, then check GitHub only to confirm older fixed feedback did not regress.
INST
        return 0
      fi
    fi
  fi

  local gate_output check_rc check_report
  set +e
  gate_output="$(gate_github_checks "${pr_num}" "${pr_view_json:-}")"
  check_rc=$?
  set -e
  printf '%s\n' "${gate_output}"

  if (( check_rc == 10 )); then
    check_report="$(extract_kv "READY_FOR_PR_CHECK_FAILURES_FILE" "${gate_output}")"

    if (( auto_bounce_failing_checks == 1 )); then
      if ! auto_bounce_to_in_progress_for_checks "${pr_num}" "${check_report}"; then
        cat <<INST
READY_FOR_PR_INSTRUCTIONS=GitHub checks failed on PR #${pr_num}, but the helper could not move the issue back to In Progress automatically. Record the failed checks and move the issue manually.
INST
        return 32
      fi
      cat <<INST
READY_FOR_PR_INSTRUCTIONS=GitHub checks failed on PR #${pr_num}. The helper already wrote PR Handoff, moved the issue to In Progress, and stopped this PR lane. Do not move to Human Review Prep.
INST
      return 0
    fi

    cat <<INST
READY_FOR_PR_INSTRUCTIONS=GitHub checks failed on PR #${pr_num}. Read ${check_report}, write PR Handoff, move to In Progress, and stop this run.
INST
    return 0
  fi

  return "${check_rc}"
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

if is_base_like_branch "${branch}"; then
  echo "READY_FOR_PR_STATUS=invalid_branch"
  echo "READY_FOR_PR_NEXT_STATE=In Progress"
  echo "READY_FOR_PR_BRANCH=${branch}"
  echo "READY_FOR_PR_MESSAGE=Current branch ${branch} is a base branch, not an issue branch. Create or switch to the issue branch before running PR prep."
  cat <<INST
READY_FOR_PR_INSTRUCTIONS=Current branch ${branch} is a base branch. Create or switch to the issue branch for ${issue_identifier}, commit/push the ticket changes there, then re-run this script.
INST
  exit 21
fi

fetch_pr_json() {
  if [[ -n "${pr_json_file}" ]]; then
    cat "${pr_json_file}"
  else
    local -a cmd=(gh pr list --state open --head "${branch}" --json "number,title,url,headRefName")
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
    local -a cmd=(gh pr view "${pr_number}" --json "number,title,url,mergeable,mergeStateStatus,headRefOid,headRefName,baseRefName,createdAt,statusCheckRollup")
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
  emit_pr_success_with_claude_check "${PR_NUMBER}" "${PR_CREATED_AT}"
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

if [[ -z "${repo}" ]]; then
  echo "Missing required argument for PR creation: --repo." >&2
  exit 2
fi

if [[ -z "${title}" ]]; then
  title="$(infer_pr_title)"
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
PR_HEAD_REF_NAME="$(printf '%s' "${pr_view_json}" | jq -r '.headRefName // ""')"
PR_BASE_REF_NAME="$(printf '%s' "${pr_view_json}" | jq -r '.baseRefName // ""')"
PR_HEAD_SHA="$(printf '%s' "${pr_view_json}" | jq -r '.headRefOid // ""')"
PR_CREATED_AT="$(printf '%s' "${pr_view_json}" | jq -r '.createdAt // ""')"

echo "READY_FOR_PR_STATUS=created_pr"
echo "READY_FOR_PR_NEXT_STATE=Ready for PR"
echo "READY_FOR_PR_BRANCH=${branch}"
echo "READY_FOR_PR_PR_NUMBER=${PR_NUMBER}"
echo "READY_FOR_PR_PR_TITLE=${PR_TITLE}"
echo "READY_FOR_PR_PR_URL=${PR_URL}"
emit_pr_success_with_claude_check "${PR_NUMBER}" "${PR_CREATED_AT}"
