#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_request_claude_rereview.sh --repo owner/repo --pr NUMBER --head-sha SHA [options]

Options:
  --repo VALUE               GitHub repo in owner/name form
  --pr VALUE                 Pull request number
  --head-sha VALUE           Current PR head SHA
  --head-committed-at VALUE  ISO8601 timestamp for the current head commit
  --author VALUE             Review author login to request (default: claude)
  --body VALUE               Override comment body (default posts @claude request)
  --dry-run                  Do not post; print what would happen
  --pr-json-file PATH        Test fixture override for gh pr view JSON
  --inline-json-file PATH    Test fixture override for gh api inline-comment JSON
EOF
}

repo=""
pr_number=""
head_sha=""
head_committed_at=""
review_author="claude"
body_override=""
dry_run=0
pr_json_file=""
inline_json_file=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      repo="${2:-}"
      shift 2
      ;;
    --pr)
      pr_number="${2:-}"
      shift 2
      ;;
    --head-sha)
      head_sha="${2:-}"
      shift 2
      ;;
    --head-committed-at)
      head_committed_at="${2:-}"
      shift 2
      ;;
    --author)
      review_author="${2:-}"
      shift 2
      ;;
    --body)
      body_override="${2:-}"
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
    --inline-json-file)
      inline_json_file="${2:-}"
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

if [[ -z "${repo}" || -z "${pr_number}" || -z "${head_sha}" ]]; then
  usage
  exit 2
fi

fetch_pr_json() {
  if [[ -n "${pr_json_file}" ]]; then
    cat "${pr_json_file}"
  else
    gh pr view "${pr_number}" --repo "${repo}" --json comments,reviews,url,commits,headRefOid
  fi
}

fetch_inline_json() {
  if [[ -n "${inline_json_file}" ]]; then
    cat "${inline_json_file}"
  else
    gh api "repos/${repo}/pulls/${pr_number}/comments?per_page=100"
  fi
}

pr_json="$(fetch_pr_json)"
inline_json="$(fetch_inline_json)"
marker="<!-- symphony-claude-rereview:${head_sha} -->"

analysis_output="$(
  PR_JSON="${pr_json}" INLINE_JSON="${inline_json}" python3 - "${review_author}" "${marker}" "${head_sha}" "${head_committed_at}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

author = sys.argv[1].strip().lower()
marker = sys.argv[2]
head_sha = sys.argv[3].strip()
head_committed_at_raw = sys.argv[4].strip()
payload = json.loads(os.environ["PR_JSON"])
inline = json.loads(os.environ.get("INLINE_JSON", "[]") or "[]")


def parse_ts(value):
    if not value:
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    dt = datetime.fromisoformat(candidate)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

has_prior_feedback = False
already_requested = False
latest_feedback = None


def consider_feedback(value):
    global latest_feedback
    ts = parse_ts(value)
    if ts is None:
        return
    if latest_feedback is None or ts > latest_feedback:
        latest_feedback = ts

for comment in payload.get("comments", []):
    login = ((comment.get("author") or {}).get("login") or "").strip().lower()
    body = comment.get("body") or ""
    if login == author:
        has_prior_feedback = True
        consider_feedback(comment.get("updatedAt") or comment.get("createdAt"))
    if marker in body:
        already_requested = True

for review in payload.get("reviews", []):
    login = ((review.get("author") or {}).get("login") or "").strip().lower()
    if login == author:
        has_prior_feedback = True
        consider_feedback(
            review.get("submittedAt") or review.get("updatedAt") or review.get("createdAt")
        )

for comment in inline:
    login = ((comment.get("user") or {}).get("login") or "").strip().lower()
    if login == author:
        has_prior_feedback = True
        consider_feedback(comment.get("updated_at") or comment.get("created_at"))

head_committed_at = parse_ts(head_committed_at_raw)
if head_committed_at is None:
    head_ref_oid = (payload.get("headRefOid") or "").strip()
    commits = payload.get("commits") or []
    selected_commit = None

    if head_ref_oid:
        for commit in commits:
            if (commit.get("oid") or "").strip() == head_ref_oid:
                selected_commit = commit
                break

    if selected_commit is None and commits:
        selected_commit = commits[-1]

    if selected_commit is not None:
        head_committed_at = parse_ts(
            selected_commit.get("committedDate") or selected_commit.get("authoredDate")
        )

head_newer_than_feedback = (
    has_prior_feedback
    and head_committed_at is not None
    and latest_feedback is not None
    and head_committed_at > latest_feedback
)

print(f"HAS_PRIOR_FEEDBACK={'1' if has_prior_feedback else '0'}")
print(f"ALREADY_REQUESTED={'1' if already_requested else '0'}")
print(f"HEAD_NEWER_THAN_FEEDBACK={'1' if head_newer_than_feedback else '0'}")
print(
    "LATEST_FEEDBACK_AT="
    + (latest_feedback.isoformat().replace("+00:00", "Z") if latest_feedback else "")
)
print(
    "HEAD_COMMITTED_AT="
    + (head_committed_at.isoformat().replace("+00:00", "Z") if head_committed_at else "")
)
print(f"PR_URL={payload.get('url') or ''}")
PY
)"

eval "${analysis_output}"

if [[ "${HAS_PRIOR_FEEDBACK}" != "1" ]]; then
  echo "CLAUDE_REREVIEW_STATUS=skipped_no_prior_feedback"
  exit 0
fi

if [[ "${HEAD_NEWER_THAN_FEEDBACK}" != "1" ]]; then
  echo "CLAUDE_REREVIEW_STATUS=skipped_head_not_newer_than_feedback"
  echo "CLAUDE_REREVIEW_PR_URL=${PR_URL}"
  echo "CLAUDE_REREVIEW_HEAD_SHA=${head_sha}"
  echo "CLAUDE_REREVIEW_LATEST_FEEDBACK_AT=${LATEST_FEEDBACK_AT}"
  echo "CLAUDE_REREVIEW_HEAD_COMMITTED_AT=${HEAD_COMMITTED_AT}"
  exit 0
fi

if [[ "${ALREADY_REQUESTED}" == "1" ]]; then
  echo "CLAUDE_REREVIEW_STATUS=skipped_already_requested"
  echo "CLAUDE_REREVIEW_PR_URL=${PR_URL}"
  exit 0
fi

short_sha="${head_sha:0:7}"
default_body="${marker}
@${review_author} Please review these recent changes.

- Updated PR head: \`${short_sha}\`
- Context: addressed prior PR feedback
"
comment_body="${body_override:-${default_body}}"

if (( dry_run == 1 )); then
  echo "CLAUDE_REREVIEW_STATUS=dry_run"
  echo "CLAUDE_REREVIEW_PR_URL=${PR_URL}"
  echo "CLAUDE_REREVIEW_HEAD_SHA=${head_sha}"
  echo "CLAUDE_REREVIEW_LATEST_FEEDBACK_AT=${LATEST_FEEDBACK_AT}"
  echo "CLAUDE_REREVIEW_HEAD_COMMITTED_AT=${HEAD_COMMITTED_AT}"
  exit 0
fi

body_file="$(mktemp)"
trap 'rm -f "${body_file}"' EXIT
printf '%s\n' "${comment_body}" > "${body_file}"
gh pr comment "${pr_number}" --repo "${repo}" --body-file "${body_file}" >/dev/null

echo "CLAUDE_REREVIEW_STATUS=posted"
echo "CLAUDE_REREVIEW_PR_URL=${PR_URL}"
echo "CLAUDE_REREVIEW_HEAD_SHA=${head_sha}"
echo "CLAUDE_REREVIEW_LATEST_FEEDBACK_AT=${LATEST_FEEDBACK_AT}"
echo "CLAUDE_REREVIEW_HEAD_COMMITTED_AT=${HEAD_COMMITTED_AT}"
