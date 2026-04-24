#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_claude_review_rounds.sh --repo owner/repo --pr NUMBER [options]

Options:
  --repo VALUE              GitHub repo in owner/name form
  --pr VALUE                Pull request number
  --author VALUE            Review author login to inspect (default: claude)
  --max-rounds VALUE        Max review rounds including initial before stopping loops (default: 5 = 1 initial + 4 re-reviews)
  --top-json-file PATH      Test fixture override for gh pr view JSON
  --inline-json-file PATH   Test fixture override for gh api inline-comment JSON
EOF
}

repo=""
pr_number=""
author_login="claude"
max_rounds=5
top_json_file=""
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
    --author)
      author_login="${2:-}"
      shift 2
      ;;
    --max-rounds)
      max_rounds="${2:-}"
      shift 2
      ;;
    --top-json-file)
      top_json_file="${2:-}"
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

if [[ -z "${repo}" || -z "${pr_number}" ]]; then
  usage
  exit 2
fi

if ! [[ "${max_rounds}" =~ ^[0-9]+$ ]]; then
  echo "max-rounds must be a non-negative integer" >&2
  exit 2
fi

fetch_top_json() {
  if [[ -n "${top_json_file}" ]]; then
    cat "${top_json_file}"
  else
    gh pr view "${pr_number}" --repo "${repo}" --json comments,reviews,url
  fi
}

fetch_inline_json() {
  if [[ -n "${inline_json_file}" ]]; then
    cat "${inline_json_file}"
  else
    gh api "repos/${repo}/pulls/${pr_number}/comments?per_page=100"
  fi
}

top_json="$(fetch_top_json)"
inline_json="$(fetch_inline_json)"

TOP_JSON="${top_json}" INLINE_JSON="${inline_json}" python3 - "${author_login}" "${max_rounds}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

author = sys.argv[1].strip().lower()
max_rounds = int(sys.argv[2])
marker_prefix = "<!-- symphony-claude-rereview:"

top = json.loads(os.environ.get("TOP_JSON", "{}") or "{}")
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


feedback_times = []
request_times = []

for comment in top.get("comments", []):
    ts = parse_ts(comment.get("updatedAt") or comment.get("createdAt"))
    if ts is None:
        continue
    body = comment.get("body") or ""
    login = ((comment.get("author") or {}).get("login") or "").strip().lower()
    if marker_prefix in body:
        request_times.append(ts)
    if login == author:
        feedback_times.append(ts)

for review in top.get("reviews", []):
    ts = parse_ts(review.get("submittedAt") or review.get("updatedAt") or review.get("createdAt"))
    if ts is None:
        continue
    login = ((review.get("author") or {}).get("login") or "").strip().lower()
    if login == author:
        feedback_times.append(ts)

for comment in inline:
    ts = parse_ts(comment.get("updated_at") or comment.get("created_at"))
    if ts is None:
        continue
    login = ((comment.get("user") or {}).get("login") or "").strip().lower()
    if login == author:
        feedback_times.append(ts)

feedback_times.sort()
request_times.sort()

initial_round = 0
feedback_index = 0

if feedback_times:
    if not request_times or feedback_times[0] < request_times[0]:
        initial_round = 1
        feedback_index = 1

responded_requests = 0
pending_requests = 0

for request_ts in request_times:
    while feedback_index < len(feedback_times) and feedback_times[feedback_index] <= request_ts:
        feedback_index += 1

    if feedback_index < len(feedback_times):
        responded_requests += 1
        feedback_index += 1
    else:
        pending_requests += 1

rounds = initial_round + responded_requests

if not feedback_times:
    status = "no_feedback"
elif rounds >= max_rounds:
    status = "maxed"
else:
    status = "below_limit"

latest_feedback = feedback_times[-1].isoformat().replace("+00:00", "Z") if feedback_times else ""
latest_request = request_times[-1].isoformat().replace("+00:00", "Z") if request_times else ""

print(f"CLAUDE_REVIEW_STATUS={status}")
print(f"CLAUDE_REVIEW_ROUNDS={rounds}")
print(f"CLAUDE_REVIEW_INITIAL_ROUND={'1' if initial_round else '0'}")
print(f"CLAUDE_REVIEW_RESPONDED_REQUESTS={responded_requests}")
print(f"CLAUDE_REVIEW_PENDING_REQUESTS={pending_requests}")
print(f"CLAUDE_REVIEW_TOTAL_REQUESTS={len(request_times)}")
print(f"CLAUDE_REVIEW_TOTAL_FEEDBACK_ITEMS={len(feedback_times)}")
print(f"CLAUDE_REVIEW_ROUNDS_MAXED={'1' if rounds >= max_rounds else '0'}")
print(f"CLAUDE_REVIEW_LATEST_AT={latest_feedback}")
print(f"CLAUDE_REVIEW_LATEST_REQUEST_AT={latest_request}")
print(f"CLAUDE_REVIEW_PR_URL={top.get('url') or ''}")
PY
