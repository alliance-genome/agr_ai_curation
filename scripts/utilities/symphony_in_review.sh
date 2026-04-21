#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
CONTEXT_HELPER="${REPO_ROOT}/scripts/utilities/symphony_linear_issue_context.sh"

# =============================================================================
# symphony_in_review.sh
#
# Review lane helper for Symphony. Fetches the relevant Linear ticket context
# (description, current handoff signals, fetched comments) and any open PR with
# the latest Claude review, then emits a structured review brief the agent must
# read before reviewing.
#
# Exit codes:
#   0  — brief generated successfully
#   2  — error (missing args, API failure)
# =============================================================================

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_in_review.sh --issue-identifier ISSUE [options]

Options:
  --issue-identifier VALUE   Required: Linear issue key (e.g. ALL-102)
  --branch VALUE             Branch to check for open PRs (default: current git branch)
  --repo VALUE               GitHub repo in owner/name form (for PR lookup)
  --review-author VALUE      GitHub login for PR review comments (default: claude)
  --linear-api-key VALUE     Linear API key (default: from LINEAR_API_KEY or ~/.linear/api_key.txt)
  --output-file PATH         Write the review brief to this file (default: stdout)
  --context-json-file PATH   Test/debug override for normalized issue context JSON
  --pr-json-file PATH        Test fixture override for `gh pr list` JSON
  --pr-comments-file PATH    Test fixture override for `gh pr view` JSON (comments)
  --linear-json-file PATH    Test fixture override for Linear issue JSON
EOF
}

issue_identifier=""
branch=""
repo=""
review_author="claude"
linear_api_key=""
output_file=""
context_json_file=""
pr_json_file=""
pr_comments_file=""
linear_json_file=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue-identifier) issue_identifier="${2:-}"; shift 2 ;;
    --branch) branch="${2:-}"; shift 2 ;;
    --repo) repo="${2:-}"; shift 2 ;;
    --review-author) review_author="${2:-}"; shift 2 ;;
    --linear-api-key) linear_api_key="${2:-}"; shift 2 ;;
    --output-file) output_file="${2:-}"; shift 2 ;;
    --context-json-file) context_json_file="${2:-}"; shift 2 ;;
    --pr-json-file) pr_json_file="${2:-}"; shift 2 ;;
    --pr-comments-file) pr_comments_file="${2:-}"; shift 2 ;;
    --linear-json-file) linear_json_file="${2:-}"; shift 2 ;;
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

  local temp_context
  local -a cmd
  temp_context="$(mktemp /tmp/symphony-in-review-context-XXXXXX.json)"
  cmd=(bash "${CONTEXT_HELPER}" --issue-identifier "${issue_identifier}" --json-output-file "${temp_context}")
  if [[ -n "${linear_json_file}" ]]; then
    cmd+=(--linear-json-file "${linear_json_file}")
  elif [[ -n "${linear_api_key}" ]]; then
    cmd+=(--linear-api-key "${linear_api_key}")
  fi

  if ! "${cmd[@]}" >/dev/null 2>&1; then
    rm -f "${temp_context}"
    return 1
  fi

  printf '%s' "${temp_context}"
}

if ! context_json_file="$(resolve_context_json_file)"; then
  echo "IN_REVIEW_STATUS=error"
  echo "IN_REVIEW_ERROR=Could not fetch Linear issue ${issue_identifier}"
  exit 2
fi

context_json="$(cat "${context_json_file}")"

issue_title="$(printf '%s' "${context_json}" | jq -r '.issue.title // empty')"
if [[ -z "${issue_title}" ]]; then
  echo "IN_REVIEW_STATUS=error"
  echo "IN_REVIEW_ERROR=Could not fetch Linear issue ${issue_identifier}"
  exit 2
fi

# ── Fetch open PR data ──────────────────────────────────────────────

pr_number=""
pr_url=""
pr_claude_comment=""

if [[ -n "${branch}" ]]; then
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

  pr_json="$(fetch_pr_json)"
  pr_count="$(printf '%s' "${pr_json}" | jq 'if type == "array" then length else 0 end')"

  if [[ "${pr_count}" -gt 0 ]]; then
    pr_number="$(printf '%s' "${pr_json}" | jq -r '.[0].number // empty')"
    pr_url="$(printf '%s' "${pr_json}" | jq -r '.[0].url // empty')"

    # Fetch PR comments to find latest Claude review
    fetch_pr_comments_json() {
      if [[ -n "${pr_comments_file}" ]]; then
        cat "${pr_comments_file}"
      else
        local -a cmd=(gh pr view "${pr_number}" --json comments,reviews)
        if [[ -n "${repo}" ]]; then
          cmd+=(--repo "${repo}")
        fi
        "${cmd[@]}"
      fi
    }

    pr_comments_json="$(fetch_pr_comments_json)"

    # Extract latest Claude comment (top-level comments + reviews)
    pr_claude_comment="$(printf '%s' "${pr_comments_json}" | jq -r --arg author "${review_author}" '
      [
        (.comments // [] | map(select((.author.login // "") | ascii_downcase == $author)) | map({at: (.updatedAt // .createdAt), body: .body})),
        (.reviews // [] | map(select((.author.login // "") | ascii_downcase == $author)) | map({at: (.submittedAt // .updatedAt // .createdAt), body: .body, state: .state}))
      ] | flatten | sort_by(.at) | last //empty |
      if .state then "Review status: \(.state)\n\n\(.body)" else .body end
    ')"
  fi
fi

# ── Compute comment count at module level (used in brief and output) ─

comment_count="$(printf '%s' "${context_json}" | jq '.comments_count // 0')"

# ── Build the review brief ──────────────────────────────────────────

build_brief() {
  echo "# Review Brief: ${issue_identifier}"
  echo ""
  echo "**Title**: ${issue_title}"
  echo "**State**: $(printf '%s' "${context_json}" | jq -r '.issue.state.name // "unknown"')"
  echo "**URL**: $(printf '%s' "${context_json}" | jq -r '.issue.url // ""')"
  echo ""

  # Section 1: Issue description
  echo "## 1. Issue Description"
  echo ""
  local description
  description="$(printf '%s' "${context_json}" | jq -r '.issue.description // "No description provided."')"
  echo "${description}"
  echo ""

  # Section 2: baton + latest non-workpad guidance
  echo "## 2. Current Handoff Signals"
  echo ""

  if printf '%s' "${context_json}" | jq -e '.workpad_comment != null' >/dev/null; then
    echo "### Latest Workpad Baton"
    echo ""
    echo "- Updated: $(printf '%s' "${context_json}" | jq -r '.workpad_comment.updated_at // "unknown"')"
    echo "- Author: $(printf '%s' "${context_json}" | jq -r '.workpad_comment.user_name // "Unknown"')"
    echo ""
    printf '%s' "${context_json}" | jq -r '.workpad_comment.body // ""'
    echo ""
  else
    echo "### Latest Workpad Baton"
    echo ""
    echo "No Symphony workpad comment found yet."
    echo ""
  fi

  if printf '%s' "${context_json}" | jq -e '.latest_non_workpad_comment != null' >/dev/null; then
    echo "### Latest Non-Workpad Guidance"
    echo ""
    echo "- Updated: $(printf '%s' "${context_json}" | jq -r '.latest_non_workpad_comment.updated_at // "unknown"')"
    echo "- Author: $(printf '%s' "${context_json}" | jq -r '.latest_non_workpad_comment.user_name // "Unknown"')"
    echo ""
    printf '%s' "${context_json}" | jq -r '.latest_non_workpad_comment.body // ""'
    echo ""
  else
    echo "### Latest Non-Workpad Guidance"
    echo ""
    echo "No non-workpad guidance comment is currently present."
    echo ""
  fi

  # Section 3: fetched comments in chronological order
  echo "## 3. Fetched Issue Comments (${comment_count} fetched)"
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

  # Section 4: Open PR + latest Claude review
  echo "## 4. Open Pull Request"
  echo ""

  if [[ -z "${pr_number}" ]]; then
    echo "No open PR found for branch \`${branch}\`."
    echo ""
  else
    echo "**PR #${pr_number}**: ${pr_url}"
    echo ""

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

  # Section 5: Review instructions
  echo "## 5. Review Instructions"
  echo ""
  echo "You are the REVIEWER for this issue. Your job is to verify the implementation"
  echo "matches what was requested. Use the information above as your primary reference:"
  echo ""
  echo "1. **Read the Issue Description** (Section 1) — this is the source of truth for"
  echo "   what was requested, including scope checkboxes, out-of-scope boundaries,"
  echo "   design constraints, and architecture guidance."
  echo ""
  echo "2. **Read the Current Handoff Signals** (Section 2) first — they surface the"
  echo "   latest workpad baton and newest human guidance."
  echo ""
  echo "3. **Read the fetched comments** (Section 3) when you need broader history,"
  echo "   prior review rounds, and older workpad context."
  echo ""
  echo "4. **Check PR Feedback** (Section 4) — if a PR exists with Claude review"
  echo "   comments, verify those suggestions were addressed or explicitly marked"
  echo "   \`not taken\` with a reason in the workpad."
  echo ""
  echo "5. **Review the code changes** against all of the above. Classify findings as"
  echo "   \`blocking\` (with evidence: file, line, unmet criterion) or \`non-blocking\`."
  echo ""
}

if [[ -n "${output_file}" ]]; then
  build_brief > "${output_file}"
else
  brief_file="$(mktemp /tmp/review-brief-XXXXXX.md)"
  build_brief > "${brief_file}"
fi

target_file="${output_file:-${brief_file}}"

# ── Emit machine-readable output ────────────────────────────────────

echo "IN_REVIEW_STATUS=ok"
echo "IN_REVIEW_BRIEF_FILE=${target_file}"
echo "IN_REVIEW_ISSUE=${issue_identifier}"
echo "IN_REVIEW_COMMENT_COUNT=${comment_count}"
echo "IN_REVIEW_PR_NUMBER=${pr_number:-none}"
if [[ -n "${pr_claude_comment}" ]]; then
  echo "IN_REVIEW_PR_CLAUDE_REVIEW=present"
else
  echo "IN_REVIEW_PR_CLAUDE_REVIEW=absent"
fi

cat <<INST
IN_REVIEW_INSTRUCTIONS=Review brief generated for ${issue_identifier}. YOU MUST:
1. Read the FULL review brief at: ${target_file}
2. The brief contains the issue description, current handoff signals, fetched comment context, and any PR feedback.
3. Review the code changes against the brief — check scope, out-of-scope boundaries, and design constraints.
4. Classify findings as blocking (with evidence) or non-blocking.
5. If clean, move forward. If blocking issues exist, move to In Progress with evidence.
INST
