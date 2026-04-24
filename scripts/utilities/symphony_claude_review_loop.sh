#!/usr/bin/env bash

set -euo pipefail

# =============================================================================
# symphony_claude_review_loop.sh
#
# Unified Claude Code review lifecycle manager for Symphony PRs.
# Replaces the separate wait / rereview / rounds scripts with a single
# stateless script that is called once per round.
#
# Each invocation inspects the PR comment history, determines the current
# round, takes the appropriate action (wait for initial review, post a
# re-review request, or report maxed-out), and returns a status.
#
# Exit codes:
#   0  — quiet (no feedback within wait window) or maxed_out
#  10  — detected (feedback found, report file written)
#   2  — error
# =============================================================================

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_claude_review_loop.sh --repo owner/repo --pr NUMBER --since ISO8601 [options]

Options:
  --repo VALUE              GitHub repo in owner/name form
  --pr VALUE                Pull request number
  --since VALUE             Floor timestamp — only consider feedback after this (typically PR creation time)
  --author VALUE            Review author login to watch (default: claude)
  --max-rounds VALUE        Max review rounds including initial (default: 5 = 1 initial + 4 re-reviews)
  --wait-seconds VALUE      Poll timeout per round in seconds (default: 300)
  --poll-seconds VALUE      Poll interval in seconds (default: 30)
  --head-sha VALUE          Current PR head SHA for re-review marker dedup (optional; auto-detected from PR if omitted)
  --disposition-file PATH   File containing feedback disposition context (items intentionally not addressed and why)
  --top-json-file PATH      Test fixture override for gh pr view JSON
  --inline-json-file PATH   Test fixture override for gh api inline-comment JSON
  --dry-run                 Do not post re-review requests; report what would happen
EOF
}

repo=""
pr_number=""
since=""
author_login="claude"
max_rounds=5
wait_seconds=300
poll_seconds=30
head_sha=""
disposition_file=""
top_json_file=""
inline_json_file=""
dry_run=0

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
    --max-rounds)
      max_rounds="${2:-}"
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
    --head-sha)
      head_sha="${2:-}"
      shift 2
      ;;
    --disposition-file)
      disposition_file="${2:-}"
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
    --dry-run)
      dry_run=1
      shift
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

if ! [[ "${max_rounds}" =~ ^[0-9]+$ && "${wait_seconds}" =~ ^[0-9]+$ && "${poll_seconds}" =~ ^[0-9]+$ ]]; then
  echo "max-rounds, wait-seconds, and poll-seconds must be non-negative integers" >&2
  exit 2
fi

if (( wait_seconds > 0 && poll_seconds == 0 )); then
  echo "poll-seconds must be greater than 0 when wait-seconds is non-zero" >&2
  exit 2
fi

# ── Data fetchers ─────────────────────────────────────────────────────

fetch_top_json() {
  if [[ -n "${top_json_file}" ]]; then
    cat "${top_json_file}"
  else
    gh pr view "${pr_number}" --repo "${repo}" \
      --json comments,reviews,url,headRefOid,commits
  fi
}

fetch_inline_json() {
  if [[ -n "${inline_json_file}" ]]; then
    cat "${inline_json_file}"
  else
    gh api "repos/${repo}/pulls/${pr_number}/comments?per_page=100"
  fi
}

# ── Analysis engine (Python) ─────────────────────────────────────────
#
# Given fetched PR data, determines:
#   - LOOP_ACTION: wait | request_and_wait | report_current | maxed_out | error
#   - LOOP_ROUND: current round number (1-based)
#   - LOOP_LATEST_FEEDBACK_AT: timestamp of most recent Claude feedback
#   - LOOP_HEAD_SHA: resolved head SHA
#   - LOOP_PR_URL: PR URL
#   - LOOP_ALREADY_REQUESTED: whether a re-review request was already posted for this SHA
#   - LOOP_WAIT_SINCE: timestamp to use as floor when polling for new feedback

analyze_state() {
  local top_json="$1"
  local inline_json="$2"

  TOP_JSON="${top_json}" INLINE_JSON="${inline_json}" \
    python3 - "${author_login}" "${since}" "${max_rounds}" "${head_sha}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

author = sys.argv[1].strip().lower()
since_raw = sys.argv[2].strip()
max_rounds = int(sys.argv[3])
head_sha_arg = sys.argv[4].strip()

marker_prefix = "<!-- symphony-claude-rereview:"

try:
    top = json.loads(os.environ.get("TOP_JSON", "{}") or "{}")
    inline = json.loads(os.environ.get("INLINE_JSON", "[]") or "[]")
except Exception as exc:
    print(f"LOOP_ACTION=error")
    print(f"LOOP_ERROR={type(exc).__name__}: {exc}")
    sys.exit(2)


# NOTE: parse_ts is duplicated in the poll_for_feedback and generate_report
# heredocs below.  They share no Python scope.  Keep all copies in sync.
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


def fmt_ts(dt):
    if dt is None:
        return ""
    return dt.isoformat().replace("+00:00", "Z")


since_dt = parse_ts(since_raw)
pr_url = top.get("url") or ""

# Resolve head SHA from PR data if not provided
head_sha = head_sha_arg
if not head_sha:
    head_sha = (top.get("headRefOid") or "").strip()

# Resolve head commit timestamp
head_committed_at = None
commits = top.get("commits") or []
if head_sha and commits:
    for commit in commits:
        if (commit.get("oid") or "").strip() == head_sha:
            head_committed_at = parse_ts(
                commit.get("committedDate") or commit.get("authoredDate")
            )
            break
if head_committed_at is None and commits:
    last_commit = commits[-1]
    head_committed_at = parse_ts(
        last_commit.get("committedDate") or last_commit.get("authoredDate")
    )

# ── Collect all Claude feedback and re-review request markers ──

feedback_times = []
request_shas = set()
request_times = []

for comment in top.get("comments", []):
    ts = parse_ts(comment.get("updatedAt") or comment.get("createdAt"))
    if ts is None:
        continue
    # Skip feedback before our floor
    body = comment.get("body") or ""
    login = ((comment.get("author") or {}).get("login") or "").strip().lower()

    # Track re-review request markers
    if marker_prefix in body:
        request_times.append(ts)
        # Extract SHA from marker
        start = body.index(marker_prefix) + len(marker_prefix)
        end = body.index("-->", start) if "-->" in body[start:] else -1
        if end > 0:
            request_shas.add(body[start:start + (end - start)].strip())

    # Track Claude feedback (only after since)
    if login == author and ts > since_dt:
        feedback_times.append(ts)

for review in top.get("reviews", []):
    ts = parse_ts(review.get("submittedAt") or review.get("updatedAt") or review.get("createdAt"))
    if ts is None:
        continue
    login = ((review.get("author") or {}).get("login") or "").strip().lower()
    if login == author and ts > since_dt:
        feedback_times.append(ts)

for comment in inline:
    ts = parse_ts(comment.get("updated_at") or comment.get("created_at"))
    if ts is None:
        continue
    login = ((comment.get("user") or {}).get("login") or "").strip().lower()
    if login == author and ts > since_dt:
        feedback_times.append(ts)

feedback_times.sort()
request_times.sort()

# ── Count rounds (same algorithm as symphony_claude_review_rounds.sh) ──

initial_round = 0
feedback_index = 0

if feedback_times:
    if not request_times or feedback_times[0] < request_times[0]:
        initial_round = 1
        feedback_index = 1

responded_requests = 0
for request_ts in request_times:
    while feedback_index < len(feedback_times) and feedback_times[feedback_index] <= request_ts:
        feedback_index += 1
    if feedback_index < len(feedback_times):
        responded_requests += 1
        feedback_index += 1

rounds = initial_round + responded_requests

latest_feedback = feedback_times[-1] if feedback_times else None
already_requested = head_sha in request_shas

# ── Determine action ──

if rounds >= max_rounds:
    action = "maxed_out"
    wait_since = None
elif not feedback_times:
    # No feedback at all — wait for initial review
    action = "wait"
    wait_since = since_dt
elif not request_times:
    # Feedback exists but no re-review markers have been posted yet.
    if head_committed_at is not None and latest_feedback is not None and head_committed_at > latest_feedback:
        # Head is newer than the initial review — the agent likely
        # addressed the feedback and pushed a fix.  Advance to
        # request_and_wait so a re-review marker is posted and the
        # round counter can progress.  Without this, the loop stays
        # stuck on report_current forever because no marker is ever
        # created.
        action = "request_and_wait"
        wait_since = latest_feedback
    else:
        # Head is at or before the review — report the initial
        # review so the agent can read and address it.
        action = "report_current"
        wait_since = None
elif head_committed_at is not None and latest_feedback is not None and head_committed_at > latest_feedback:
    # Re-review cycle: markers exist and head is newer than latest feedback
    if already_requested:
        # Already requested re-review for this SHA, just wait for response
        action = "wait"
        wait_since = latest_feedback
    else:
        # Need to post re-review request, then wait
        action = "request_and_wait"
        wait_since = latest_feedback
else:
    # Feedback is current — report it
    action = "report_current"
    wait_since = None

print(f"LOOP_ACTION={action}")
print(f"LOOP_ROUND={rounds + (1 if action in ('wait', 'request_and_wait') and rounds == 0 else 0)}")
print(f"LOOP_ROUNDS_COMPLETED={rounds}")
print(f"LOOP_MAX_ROUNDS={max_rounds}")
print(f"LOOP_LATEST_FEEDBACK_AT={fmt_ts(latest_feedback)}")
print(f"LOOP_HEAD_SHA={head_sha}")
print(f"LOOP_HEAD_COMMITTED_AT={fmt_ts(head_committed_at)}")
print(f"LOOP_ALREADY_REQUESTED={'1' if already_requested else '0'}")
print(f"LOOP_WAIT_SINCE={fmt_ts(wait_since)}")
print(f"LOOP_PR_URL={pr_url}")
PY
}

# ── Poll for new Claude feedback ──────────────────────────────────────

poll_for_feedback() {
  local wait_since="$1"
  local deadline=$(( $(date +%s) + wait_seconds ))

  while true; do
    local top_json inline_json
    top_json="$(fetch_top_json)"
    inline_json="$(fetch_inline_json)"

    local check_output
    set +e
    check_output="$(
      TOP_JSON="${top_json}" INLINE_JSON="${inline_json}" \
        python3 - "${author_login}" "${wait_since}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

author = sys.argv[1].strip().lower()
since_raw = sys.argv[2].strip()

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

since_dt = parse_ts(since_raw)
top = json.loads(os.environ.get("TOP_JSON", "{}") or "{}")
inline = json.loads(os.environ.get("INLINE_JSON", "[]") or "[]")

latest = None

def consider(source, created_at, url):
    global latest
    dt = parse_ts(created_at)
    if dt is None or dt <= since_dt:
        return
    candidate = {"source": source, "created_at": dt, "url": url or ""}
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
    print("POLL_STATUS=quiet")
    sys.exit(0)

ts_str = latest["created_at"].isoformat().replace("+00:00", "Z")
print("POLL_STATUS=detected")
print(f"POLL_SOURCE={latest['source']}")
print(f"POLL_LATEST_AT={ts_str}")
print(f"POLL_URL={latest['url']}")
sys.exit(10)
PY
    )"
    local check_rc=$?
    set -e

    if (( check_rc == 10 )); then
      printf '%s\n' "${check_output}"
      return 10
    fi

    if (( check_rc == 2 )); then
      printf '%s\n' "${check_output}"
      return 2
    fi

    local now
    now="$(date +%s)"
    if (( now >= deadline || wait_seconds == 0 )); then
      printf '%s\n' "${check_output}"
      return 0
    fi

    sleep "${poll_seconds}"
  done
}

# ── Generate report file ─────────────────────────────────────────────

generate_report() {
  local report_file="$1"

  local top_json inline_json
  top_json="$(fetch_top_json)"
  inline_json="$(fetch_inline_json)"

  local combined_comments combined_reviews
  combined_comments="$(printf '%s' "${top_json}" | jq '.comments // []')"
  combined_reviews="$(printf '%s' "${top_json}" | jq '.reviews // []')"

  COMMENTS_JSON="${combined_comments}" REVIEWS_JSON="${combined_reviews}" INLINE_JSON="${inline_json}" \
    python3 - "${author_login}" "${since}" "${pr_number}" > "${report_file}" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

author = sys.argv[1].strip().lower()
since_raw = sys.argv[2].strip()
pr_num = sys.argv[3].strip()

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

since_dt = parse_ts(since_raw)
comments = json.loads(os.environ.get("COMMENTS_JSON", "[]") or "[]")
reviews = json.loads(os.environ.get("REVIEWS_JSON", "[]") or "[]")
inline = json.loads(os.environ.get("INLINE_JSON", "[]") or "[]")

marker_prefix = "<!-- symphony-claude-rereview:"
items = []

for c in comments:
    login = ((c.get("author") or {}).get("login") or "").strip().lower()
    if login != author:
        continue
    ts = parse_ts(c.get("updatedAt") or c.get("createdAt"))
    if ts and since_dt and ts <= since_dt:
        continue
    body = (c.get("body") or "").strip()
    # Skip re-review request markers from Symphony itself
    if marker_prefix in body:
        continue
    items.append({
        "type": "PR comment",
        "body": body,
        "at": ts.isoformat() if ts else "",
        "url": c.get("url", ""),
    })

for r in reviews:
    login = ((r.get("author") or {}).get("login") or "").strip().lower()
    if login != author:
        continue
    ts = parse_ts(r.get("submittedAt") or r.get("updatedAt") or r.get("createdAt"))
    if ts and since_dt and ts <= since_dt:
        continue
    body = (r.get("body") or "").strip()
    state = r.get("state", "")
    items.append({
        "type": f"Review ({state})",
        "body": body,
        "at": ts.isoformat() if ts else "",
        "url": r.get("url", ""),
    })

for c in inline:
    login = ((c.get("user") or {}).get("login") or "").strip().lower()
    if login != author:
        continue
    ts = parse_ts(c.get("updated_at") or c.get("created_at"))
    if ts and since_dt and ts <= since_dt:
        continue
    path = c.get("path", "")
    line = c.get("original_line") or c.get("line") or ""
    body = (c.get("body") or "").strip()
    items.append({
        "type": f"Inline comment on {path}:{line}" if path else "Inline comment",
        "body": body,
        "at": ts.isoformat() if ts else "",
        "url": c.get("html_url", ""),
    })

items.sort(key=lambda x: x["at"])

print(f"# Claude Code Review Report — PR #{pr_num}")
print()
print(f"**{len(items)} comment(s) found since {since_raw}**")
print()

for i, item in enumerate(items, 1):
    print(f"## {i}. {item['type']}")
    if item["url"]:
        print(f"URL: {item['url']}")
    print()
    print(item["body"])
    print()

PY
}

# ── Post re-review request ───────────────────────────────────────────

post_rereview_request() {
  local resolved_head_sha="$1"
  local short_sha="${resolved_head_sha:0:7}"
  local marker="<!-- symphony-claude-rereview:${resolved_head_sha} -->"
  local comment_body="${marker}
@${author_login} Please review these recent changes.

- Updated PR head: \`${short_sha}\`
- Context: addressed prior PR feedback
"

  # Append feedback disposition context if provided, so Claude knows
  # which prior suggestions were intentionally not addressed and why.
  if [[ -n "${disposition_file}" && -s "${disposition_file}" ]]; then
    comment_body+="
### Prior Feedback Disposition

The following is the agent's disposition on your prior review comments. Items marked **not taken** were intentionally skipped for the stated reason — please do not re-raise them unless the stated reason is incorrect.

$(cat "${disposition_file}")
"
  fi

  if (( dry_run == 1 )); then
    echo "DRY_RUN: would post re-review request for ${short_sha}" >&2
    return 0
  fi

  local body_file
  body_file="$(mktemp)"
  printf '%s\n' "${comment_body}" > "${body_file}"
  gh pr comment "${pr_number}" --repo "${repo}" --body-file "${body_file}" >/dev/null
  rm -f "${body_file}"
}

# ── Main ──────────────────────────────────────────────────────────────

top_json="$(fetch_top_json)"
inline_json="$(fetch_inline_json)"

set +e
analysis_output="$(analyze_state "${top_json}" "${inline_json}")"
analysis_rc=$?
set -e

if (( analysis_rc == 2 )); then
  printf '%s\n' "${analysis_output}"
  echo "CLAUDE_LOOP_STATUS=error"
  exit 2
fi

# Output is KEY=VALUE pairs from controlled Python code; values are
# timestamps, SHAs, and GitHub URLs — no user-supplied shell metacharacters.
eval "${analysis_output}"

case "${LOOP_ACTION}" in

  maxed_out)
    echo "CLAUDE_LOOP_STATUS=maxed_out"
    echo "CLAUDE_LOOP_ROUND=${LOOP_ROUNDS_COMPLETED}"
    echo "CLAUDE_LOOP_MAX_ROUNDS=${LOOP_MAX_ROUNDS}"
    echo "CLAUDE_LOOP_LATEST_AT=${LOOP_LATEST_FEEDBACK_AT}"
    echo "CLAUDE_LOOP_PR_URL=${LOOP_PR_URL}"
    exit 0
    ;;

  report_current)
    report_file="$(mktemp /tmp/claude-review-report-XXXXXX.md)"
    generate_report "${report_file}"
    echo "CLAUDE_LOOP_STATUS=detected"
    echo "CLAUDE_LOOP_ROUND=${LOOP_ROUNDS_COMPLETED}"
    echo "CLAUDE_LOOP_MAX_ROUNDS=${LOOP_MAX_ROUNDS}"
    echo "CLAUDE_LOOP_REPORT_FILE=${report_file}"
    echo "CLAUDE_LOOP_LATEST_AT=${LOOP_LATEST_FEEDBACK_AT}"
    echo "CLAUDE_LOOP_PR_URL=${LOOP_PR_URL}"
    exit 10
    ;;

  request_and_wait)
    post_rereview_request "${LOOP_HEAD_SHA}"
    ;& # fall through to wait

  wait)
    set +e
    poll_output="$(poll_for_feedback "${LOOP_WAIT_SINCE}")"
    poll_rc=$?
    set -e

    if (( poll_rc == 10 )); then
      # Feedback detected — generate report
      report_file="$(mktemp /tmp/claude-review-report-XXXXXX.md)"
      generate_report "${report_file}"

      # Re-run analysis to get updated round count
      top_json="$(fetch_top_json)"
      inline_json="$(fetch_inline_json)"
      set +e
      updated_analysis="$(analyze_state "${top_json}" "${inline_json}")"
      set -e
      eval "${updated_analysis}"

      echo "CLAUDE_LOOP_STATUS=detected"
      echo "CLAUDE_LOOP_ROUND=${LOOP_ROUNDS_COMPLETED}"
      echo "CLAUDE_LOOP_MAX_ROUNDS=${LOOP_MAX_ROUNDS}"
      echo "CLAUDE_LOOP_REPORT_FILE=${report_file}"
      echo "CLAUDE_LOOP_LATEST_AT=${LOOP_LATEST_FEEDBACK_AT}"
      echo "CLAUDE_LOOP_PR_URL=${LOOP_PR_URL}"
      exit 10
    fi

    if (( poll_rc == 2 )); then
      echo "CLAUDE_LOOP_STATUS=error"
      exit 2
    fi

    echo "CLAUDE_LOOP_STATUS=quiet"
    echo "CLAUDE_LOOP_ROUND=${LOOP_ROUND}"
    echo "CLAUDE_LOOP_MAX_ROUNDS=${LOOP_MAX_ROUNDS}"
    echo "CLAUDE_LOOP_LATEST_AT=${LOOP_LATEST_FEEDBACK_AT}"
    echo "CLAUDE_LOOP_PR_URL=${LOOP_PR_URL}"
    exit 0
    ;;

  error)
    echo "CLAUDE_LOOP_STATUS=error"
    echo "CLAUDE_LOOP_ERROR=${LOOP_ERROR:-unknown}"
    exit 2
    ;;

  *)
    echo "CLAUDE_LOOP_STATUS=error"
    echo "CLAUDE_LOOP_ERROR=unknown_action:${LOOP_ACTION}"
    exit 2
    ;;
esac
