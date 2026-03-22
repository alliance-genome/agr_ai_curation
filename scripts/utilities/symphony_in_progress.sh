#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
# shellcheck source=../lib/symphony_linear_common.sh
source "${REPO_ROOT}/scripts/lib/symphony_linear_common.sh"

CONTEXT_HELPER="${REPO_ROOT}/scripts/utilities/symphony_linear_issue_context.sh"

# =============================================================================
# symphony_in_progress.sh
#
# In Progress lane helper for Symphony.  Detects WHY the issue is in In
# Progress (first implementation, review bounce, CI failure, human feedback)
# by inspecting Linear state history, then assembles a focused brief with
# the full ticket context, PR status, and entry-specific instructions.
#
# Exit codes:
#   0  — brief generated successfully
#   2  — error (missing args, API failure)
# =============================================================================

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_in_progress.sh --issue-identifier ISSUE [options]

Options:
  --issue-identifier VALUE   Required: Linear issue key (e.g. ALL-106)
  --branch VALUE             Branch to check for open PRs (default: current git branch)
  --repo VALUE               GitHub repo in owner/name form (for PR lookup)
  --review-author VALUE      GitHub login for PR review comments (default: claude)
  --linear-api-key VALUE     Linear API key (default: from ~/.linear/api_key.txt)
  --output-file PATH         Write the brief to this file (default: temp file)
  --context-json-file PATH   Test/debug override for normalized issue context JSON
  --linear-json-file PATH    Test fixture override for Linear issue JSON
  --history-json-file PATH   Test fixture override for Linear history JSON
  --pr-json-file PATH        Test fixture override for gh pr list JSON
  --pr-view-json-file PATH   Test fixture override for gh pr view JSON (checks + comments)
EOF
}

issue_identifier=""
branch=""
repo=""
review_author="claude"
linear_api_key=""
output_file=""
context_json_file=""
linear_json_file=""
history_json_file=""
pr_json_file=""
pr_view_json_file=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue-identifier) issue_identifier="${2:-}"; shift 2 ;;
    --branch) branch="${2:-}"; shift 2 ;;
    --repo) repo="${2:-}"; shift 2 ;;
    --review-author) review_author="${2:-}"; shift 2 ;;
    --linear-api-key) linear_api_key="${2:-}"; shift 2 ;;
    --output-file) output_file="${2:-}"; shift 2 ;;
    --context-json-file) context_json_file="${2:-}"; shift 2 ;;
    --linear-json-file) linear_json_file="${2:-}"; shift 2 ;;
    --history-json-file) history_json_file="${2:-}"; shift 2 ;;
    --pr-json-file) pr_json_file="${2:-}"; shift 2 ;;
    --pr-view-json-file) pr_view_json_file="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "${issue_identifier}" ]]; then
  usage
  exit 2
fi

if [[ -z "${branch}" ]]; then
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
fi

# ── Load Linear API key ─────────────────────────────────────────────

resolve_context_json_file() {
  if [[ -n "${context_json_file}" ]]; then
    printf '%s' "${context_json_file}"
    return 0
  fi

  local temp_context temp_linear merged_raw
  local -a cmd
  temp_context="$(mktemp /tmp/symphony-in-progress-context-XXXXXX.json)"
  cmd=(bash "${CONTEXT_HELPER}" --issue-identifier "${issue_identifier}" --include-history --json-output-file "${temp_context}")

  if [[ -n "${linear_json_file}" || -n "${history_json_file}" ]]; then
    if [[ -z "${linear_json_file}" || -z "${history_json_file}" ]]; then
      echo "Both --linear-json-file and --history-json-file are required together." >&2
      rm -f "${temp_context}"
      return 1
    fi

    temp_linear="$(mktemp /tmp/symphony-in-progress-linear-raw-XXXXXX.json)"
    merged_raw="$(jq -n \
      --slurpfile issue "${linear_json_file}" \
      --slurpfile history "${history_json_file}" '
      {
        data: {
          issue: (
            ($issue[0].data.issue // {})
            + {history: (($history[0].data.issue.history // {nodes: []}))}
          )
        }
      }')"
    printf '%s\n' "${merged_raw}" > "${temp_linear}"
    cmd+=(--linear-json-file "${temp_linear}")
  elif [[ -n "${linear_api_key}" ]]; then
    cmd+=(--linear-api-key "${linear_api_key}")
  fi

  if ! "${cmd[@]}" >/dev/null 2>&1; then
    rm -f "${temp_context}" "${temp_linear:-}"
    return 1
  fi

  rm -f "${temp_linear:-}"
  printf '%s' "${temp_context}"
}

if ! context_json_file="$(resolve_context_json_file)"; then
  echo "IN_PROGRESS_STATUS=error"
  echo "IN_PROGRESS_ERROR=Could not fetch Linear issue ${issue_identifier}"
  exit 2
fi

context_json="$(cat "${context_json_file}")"

issue_title="$(printf '%s' "${context_json}" | jq -r '.issue.title // empty')"
if [[ -z "${issue_title}" ]]; then
  echo "IN_PROGRESS_STATUS=error"
  echo "IN_PROGRESS_ERROR=Could not fetch Linear issue ${issue_identifier}"
  exit 2
fi

# ── Detect entry context from history ────────────────────────────────

# Find the most recent transition INTO "In Progress" and what state it came from
entry_context="$(printf '%s' "${context_json}" | jq -r '
  [.history[]
   | select(.to_state.name == "In Progress" and .from_state != null)]
  | sort_by(.created_at)
  | last
  // {from_state: {name: "unknown"}, created_at: "unknown"}
  | {from: .from_state.name, at: .created_at}
  | "\(.from)|\(.at)"
')"

entry_from="$(echo "${entry_context}" | cut -d'|' -f1)"
entry_at="$(echo "${entry_context}" | cut -d'|' -f2)"

# Count how many times it's been in In Progress (to detect first vs bounce)
in_progress_count="$(printf '%s' "${context_json}" | jq '
  [.history[]
   | select(.to_state.name == "In Progress" and .from_state != null)]
  | length
')"

# Floor: the first time an issue is worked on is pass #1, even if no
# transition with a non-null fromState exists (e.g. created directly
# in In Progress).
if [[ "${in_progress_count}" -eq 0 ]]; then
  in_progress_count=1
fi

# ── Fetch open PR data ──────────────────────────────────────────────

pr_number=""
pr_url=""
pr_head_sha=""
pr_claude_comment=""
failing_checks=""
pending_checks=""

if [[ -n "${branch}" ]]; then
  fetch_pr_list_json() {
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

  pr_list_json="$(fetch_pr_list_json)"
  pr_count="$(printf '%s' "${pr_list_json}" | jq 'if type == "array" then length else 0 end')"

  if [[ "${pr_count}" -gt 0 ]]; then
    pr_number="$(printf '%s' "${pr_list_json}" | jq -r '.[0].number // empty')"
    pr_url="$(printf '%s' "${pr_list_json}" | jq -r '.[0].url // empty')"

    # Fetch PR details: checks + comments
    fetch_pr_view_json() {
      if [[ -n "${pr_view_json_file}" ]]; then
        cat "${pr_view_json_file}"
      else
        local -a cmd=(gh pr view "${pr_number}" --json statusCheckRollup,comments,reviews,headRefOid)
        if [[ -n "${repo}" ]]; then
          cmd+=(--repo "${repo}")
        fi
        "${cmd[@]}"
      fi
    }

    pr_view_json="$(fetch_pr_view_json)"
    pr_head_sha="$(printf '%s' "${pr_view_json}" | jq -r '.headRefOid // empty')"

    # Extract failing and pending checks
    failing_checks="$(printf '%s' "${pr_view_json}" | jq -r '
      [.statusCheckRollup // []
       | .[]
       | select(.status == "COMPLETED" and .conclusion != "SUCCESS" and .conclusion != "SKIPPED" and .conclusion != "NEUTRAL" and .conclusion != "")
       | .name]
      | join(", ")
    ')"

    pending_checks="$(printf '%s' "${pr_view_json}" | jq -r '
      [.statusCheckRollup // []
       | .[]
       | select(.status != "COMPLETED")
       | .name]
      | join(", ")
    ')"

    # Extract latest Claude review comment
    pr_claude_comment="$(printf '%s' "${pr_view_json}" | jq -r --arg author "${review_author}" '
      [
        (.comments // [] | map(select((.author.login // "") | ascii_downcase == $author)) | map({at: (.updatedAt // .createdAt), body: .body})),
        (.reviews // [] | map(select((.author.login // "") | ascii_downcase == $author)) | map({at: (.submittedAt // .updatedAt // .createdAt), body: .body, state: .state}))
      ] | flatten | sort_by(.at) | last // empty |
      if .state then "Review status: \(.state)\n\n\(.body)" else .body end
    ')"
  fi
fi

# ── Compute comment count ────────────────────────────────────────────

comment_count="$(printf '%s' "${context_json}" | jq '.comments_count // 0')"

# ── Build the brief ─────────────────────────────────────────────────

build_brief() {
  echo "# In Progress Brief: ${issue_identifier}"
  echo ""
  echo "**Title**: ${issue_title}"
  echo "**State**: In Progress"
  echo "**URL**: $(printf '%s' "${context_json}" | jq -r '.issue.url // ""')"
  echo ""

  # Section 1: Entry context
  echo "## 1. Why You Are In Progress"
  echo ""

  case "${entry_from}" in
    Todo|Backlog)
      echo "**First implementation pass.** This issue just moved from ${entry_from} to In Progress."
      echo "Read the ticket description below and implement the scope."
      ;;
    "In Review")
      echo "**Bounced from reviewer.** A reviewer found blocking issues and sent this back."
      echo "Check the workpad (in Comments below) for the reviewer's findings — look for"
      echo "entries marked \`blocking\` with file/line evidence. Address those specific issues."
      ;;
    "Ready for PR")
      echo "**Bounced from Ready for PR.**"
      if [[ -n "${failing_checks}" ]]; then
        echo "CI checks failed: **${failing_checks}**"
        echo "Fix the failing tests/checks, then push and cycle back through review."
      elif [[ -n "${pr_claude_comment}" ]]; then
        echo "Claude left review feedback that needs addressing."
        echo "Read Claude's review in Section 4 below and address the findings."
      else
        echo "The Ready for PR lane sent this back — check the workpad for details."
      fi
      ;;
    "Human Review")
      echo "**Sent back from Human Review.** Chris reviewed this and sent it back."
      echo "Check the Comments section below for the latest human feedback and address it."
      ;;
    Finalizing)
      echo "**Bounced from Finalizing — PR merge conflict.** The PR was approved and ready"
      echo "to merge, but another ticket landed on \`main\` first and introduced conflicts."
      echo ""
      echo "The finalize helper identified the conflict details and wrote them to the workpad."
      echo "Check the Comments section below for:"
      echo "- Which files conflict"
      echo "- Which sibling ticket(s) caused the conflict"
      echo "- The merge error output"
      echo ""
      echo "Your job: rebase this branch against \`main\`, resolve the conflicts while"
      echo "preserving both this ticket's scope and the sibling's changes, push, and"
      echo "move to Needs Review. Do not expand into sibling scope during resolution."
      ;;
    *)
      echo "**Entry from: ${entry_from}** (at ${entry_at})"
      echo "Check the workpad and comments for context on why this is back in progress."
      ;;
  esac

  echo ""
  echo "This is implementation pass **#${in_progress_count}** for this issue."
  echo ""

  # Section 2: Issue description
  echo "## 2. Issue Description"
  echo ""
  printf '%s' "${context_json}" | jq -r '.issue.description // "No description provided."'
  echo ""

  # Section 3: All comments
  echo "## 3. Issue Comments (${comment_count} total)"
  echo ""

  if [[ "${comment_count}" -eq 0 ]]; then
    echo "No comments on this issue."
    echo ""
  else
    printf '%s' "${context_json}" | jq -r '
      .comments
      | sort_by(.created_at)
      | to_entries[]
      | "### Comment \(.key + 1) — \(.value.user_name // "Unknown") (\(.value.created_at // "unknown time"))\n\n\(.value.body)\n"
    '
  fi

  # Section 4: Open PR status
  echo "## 4. Open Pull Request"
  echo ""

  if [[ -z "${pr_number}" ]]; then
    echo "No open PR found for branch \`${branch}\`."
    echo ""
  else
    echo "**PR #${pr_number}**: ${pr_url}"
    echo "**Head SHA**: ${pr_head_sha}"
    echo ""

    if [[ -n "${failing_checks}" ]]; then
      echo "### Failing Checks"
      echo ""
      echo "${failing_checks}" | tr ',' '\n' | while read -r check; do
        check="$(echo "${check}" | xargs)"
        [[ -n "${check}" ]] && echo "- ${check}"
      done
      echo ""
    fi

    if [[ -n "${pending_checks}" ]]; then
      echo "### Pending Checks"
      echo ""
      echo "${pending_checks}" | tr ',' '\n' | while read -r check; do
        check="$(echo "${check}" | xargs)"
        [[ -n "${check}" ]] && echo "- ${check}"
      done
      echo ""
    fi

    if [[ -n "${pr_claude_comment}" ]]; then
      echo "### Latest ${review_author} Review Comment"
      echo ""
      echo "${pr_claude_comment}"
      echo ""
    else
      echo "No ${review_author} review comments found on this PR."
      echo ""
    fi
  fi

  # Section 5: Instructions
  echo "## 5. Implementation Instructions"
  echo ""
  echo "You are the IMPLEMENTER for this issue. Use the information above:"
  echo ""
  echo "1. **Read Section 1** to understand WHY you are in In Progress and what"
  echo "   specifically needs to be done in this pass."
  echo ""
  echo "2. **Read the Issue Description** (Section 2) for scope, out-of-scope"
  echo "   boundaries, design constraints, and architecture guidance."
  echo ""
  echo "3. **Read the Comments** (Section 3) for workpad history, prior review"
  echo "   findings, Claude Feedback Dispositions, and human guidance."
  echo ""
  echo "4. **Check PR Status** (Section 4) for failing checks and Claude review"
  echo "   feedback that needs addressing."
  echo ""

  case "${entry_from}" in
    Todo|Backlog)
      echo "5. **Implement** the scope checkboxes from the issue description."
      ;;
    "In Review")
      echo "5. **Fix the blocking reviewer findings** documented in the workpad,"
      echo "   then push and move to Needs Review."
      ;;
    "Ready for PR")
      if [[ -n "${failing_checks}" ]]; then
        echo "5. **Fix the failing CI checks**: ${failing_checks}"
        echo "   Then push and move to Needs Review."
      else
        echo "5. **Address the feedback** from the Ready for PR lane, push, and"
        echo "   move to Needs Review."
      fi
      ;;
    "Human Review")
      echo "5. **Address Chris's feedback** from the latest comment, push, and"
      echo "   move to Needs Review."
      ;;
    Finalizing)
      echo "5. **Resolve the merge conflict**: rebase against \`main\`, resolve"
      echo "   conflicting files using the workpad details, push, and move to"
      echo "   Needs Review. Keep both tickets' changes — do not drop sibling work."
      ;;
    *)
      echo "5. **Address whatever caused the bounce**, push, and move to Needs Review."
      ;;
  esac
  echo ""
}

if [[ -n "${output_file}" ]]; then
  build_brief > "${output_file}"
  target_file="${output_file}"
else
  brief_file="$(mktemp /tmp/in-progress-brief-XXXXXX.md)"
  build_brief > "${brief_file}"
  target_file="${brief_file}"
fi

# ── Emit machine-readable output ────────────────────────────────────

echo "IN_PROGRESS_STATUS=ok"
echo "IN_PROGRESS_BRIEF_FILE=${target_file}"
echo "IN_PROGRESS_ISSUE=${issue_identifier}"
echo "IN_PROGRESS_ENTRY_FROM=${entry_from}"
echo "IN_PROGRESS_ENTRY_AT=${entry_at}"
echo "IN_PROGRESS_PASS_NUMBER=${in_progress_count}"
echo "IN_PROGRESS_COMMENT_COUNT=${comment_count}"
echo "IN_PROGRESS_PR_NUMBER=${pr_number:-none}"
if [[ -n "${failing_checks}" ]]; then
  echo "IN_PROGRESS_FAILING_CHECKS=${failing_checks}"
fi
if [[ -n "${pending_checks}" ]]; then
  echo "IN_PROGRESS_PENDING_CHECKS=${pending_checks}"
fi
if [[ -n "${pr_claude_comment}" ]]; then
  echo "IN_PROGRESS_PR_CLAUDE_REVIEW=present"
else
  echo "IN_PROGRESS_PR_CLAUDE_REVIEW=absent"
fi

cat <<INST
IN_PROGRESS_INSTRUCTIONS=In Progress brief generated for ${issue_identifier} (pass #${in_progress_count}, from ${entry_from}). YOU MUST:
1. Read the FULL brief at: ${target_file}
2. Section 1 tells you WHY you are in In Progress and what to focus on.
3. The brief contains the issue description, all comments, and PR status.
4. After addressing the issue, push and move to Needs Review.
INST
