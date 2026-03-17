#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_wait_for_claude_review.sh --repo owner/repo --pr NUMBER --since ISO8601 [options]

Options:
  --repo VALUE            GitHub repo in owner/name form
  --pr VALUE              Pull request number
  --since VALUE           Only treat Claude feedback newer than this timestamp as fresh
  --author VALUE          Review author login to watch (default: claude)
  --wait-seconds VALUE    Total wait window in seconds (default: 0)
  --poll-seconds VALUE    Poll interval in seconds (default: 30)
  --top-json-file PATH    Test fixture override for gh pr view JSON
  --inline-json-file PATH Test fixture override for gh api inline-comment JSON
EOF
}

repo=""
pr_number=""
since=""
author_login="claude"
wait_seconds=0
poll_seconds=30
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
    --since)
      since="${2:-}"
      shift 2
      ;;
    --author)
      author_login="${2:-}"
      shift 2
      ;;
    --wait-seconds)
      wait_seconds="${2:-}"
      shift 2
      ;;
    --poll-seconds)
      poll_seconds="${2:-}"
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

if [[ -z "${repo}" || -z "${pr_number}" || -z "${since}" ]]; then
  usage
  exit 2
fi

if ! [[ "${wait_seconds}" =~ ^[0-9]+$ && "${poll_seconds}" =~ ^[0-9]+$ ]]; then
  echo "wait/poll values must be non-negative integers" >&2
  exit 2
fi

if (( wait_seconds > 0 && poll_seconds == 0 )); then
  echo "poll interval must be greater than 0 when wait-seconds is non-zero" >&2
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

inspect_feedback() {
  local top_json="$1"
  local inline_json="$2"

  TOP_JSON="${top_json}" INLINE_JSON="${inline_json}" python3 - "${author_login}" "${since}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

author = sys.argv[1].strip().lower()
since_raw = sys.argv[2].strip()
top_raw = os.environ.get("TOP_JSON", "")
inline_raw = os.environ.get("INLINE_JSON", "")

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

try:
    since_dt = parse_ts(since_raw)
    top = json.loads(top_raw or "{}")
    inline = json.loads(inline_raw or "[]")
except Exception as exc:
    print("CLAUDE_REVIEW_STATUS=error")
    print(f"CLAUDE_REVIEW_ERROR={type(exc).__name__}")
    sys.exit(2)

latest = None

def consider(source, created_at, url):
    global latest
    dt = parse_ts(created_at)
    if dt is None or dt <= since_dt:
        return
    candidate = {
        "source": source,
        "created_at": dt,
        "url": url or ""
    }
    if latest is None or candidate["created_at"] > latest["created_at"]:
        latest = candidate

for comment in top.get("comments", []):
    login = ((comment.get("author") or {}).get("login") or "").strip().lower()
    if login == author:
        consider("top_level_comment", comment.get("updatedAt") or comment.get("createdAt"), comment.get("url"))

for review in top.get("reviews", []):
    login = ((review.get("author") or {}).get("login") or "").strip().lower()
    if login == author:
        consider("review", review.get("submittedAt") or review.get("updatedAt") or review.get("createdAt"), review.get("url"))

for comment in inline:
    login = ((comment.get("user") or {}).get("login") or "").strip().lower()
    if login == author:
        consider("inline_comment", comment.get("updated_at") or comment.get("created_at"), comment.get("html_url"))

if latest is None:
    print("CLAUDE_REVIEW_STATUS=quiet")
    sys.exit(0)

print("CLAUDE_REVIEW_STATUS=detected")
print(f"CLAUDE_REVIEW_SOURCE={latest['source']}")
print(f"CLAUDE_REVIEW_LATEST_AT={latest['created_at'].isoformat().replace('+00:00', 'Z')}")
print(f"CLAUDE_REVIEW_URL={latest['url']}")
sys.exit(10)
PY
}

deadline=$(( $(date +%s) + wait_seconds ))

while true; do
  top_json="$(fetch_top_json)"
  inline_json="$(fetch_inline_json)"

  set +e
  output="$(inspect_feedback "${top_json}" "${inline_json}")"
  rc=$?
  set -e

  printf '%s\n' "${output}"

  if (( rc == 10 || rc == 2 )); then
    exit "${rc}"
  fi

  now="$(date +%s)"
  if (( now >= deadline || wait_seconds == 0 )); then
    exit 0
  fi

  sleep "${poll_seconds}"
done
