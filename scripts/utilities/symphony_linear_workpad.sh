#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
# shellcheck source=../lib/symphony_linear_common.sh
source "${REPO_ROOT}/scripts/lib/symphony_linear_common.sh"

CONTEXT_HELPER="${REPO_ROOT}/scripts/utilities/symphony_linear_issue_context.sh"

usage() {
  cat <<'EOF'
Usage:
  symphony_linear_workpad.sh <subcommand> [options]

Subcommands:
  show            Resolve the current Symphony-managed workpad comment.
  upsert          Create or update the persistent workpad comment.
  append-section  Replace or append one markdown section inside the workpad.
  latest-human    Materialize the latest non-workpad comment.

Purpose:
  Manage the single persistent Symphony workpad comment on a Linear issue.

Why prefer this helper over raw GraphQL:
  - It uses a stable explicit workpad marker instead of body-text heuristics.
  - It deterministically chooses an update target when duplicates exist.
  - It reuses the canonical issue-context classifier for workpad vs non-workpad.
  - It keeps routine create/update/show flows out of WORKFLOW.md and lane scripts.

Options:
  --issue-identifier VALUE    Linear issue identifier such as ALL-123.
  --issue-id VALUE            Linear issue id; accepted as an alternative lookup key.
  --comment-id VALUE          Explicit comment id to target for `show` or `upsert`.
  --body-file PATH            Markdown body to use for `upsert`.
  --section-title VALUE       Section title for `append-section`.
  --section-file PATH         File containing the section body for `append-section`.
  --section-stdin             Read the section body for `append-section` from stdin.
  --comments-first N          Number of comments to inspect. Default: 50.
  --linear-api-key VALUE      Linear API key. Default: LINEAR_API_KEY or ~/.linear/api_key.txt.
  --context-json-file PATH    Testing/debug override: use a normalized context JSON file.
  --linear-json-file PATH     Testing/debug override forwarded to the context helper.
  --output-file PATH          Materialize the selected comment body to this path.
  --json-output-file PATH     Write a JSON summary of the operation to this path.
  --format VALUE              One of: env, json, pretty. Default: env.
  --help                      Show this help.

Supported `--format` values:
  - env
  - json
  - pretty

Supported subcommands:
  - show
  - upsert
  - append-section
  - latest-human

Output contract:
  `show`:
    WORKPAD_STATUS=found|missing|error
    WORKPAD_COMMENT_ID=...
    WORKPAD_ISSUE_ID=...
    WORKPAD_ISSUE_IDENTIFIER=...
    WORKPAD_BODY_FILE=...
    WORKPAD_DUPLICATE_COUNT=...
    WORKPAD_WARNING=...

  `upsert` and `append-section`:
    WORKPAD_STATUS=created|updated|unchanged|error
    WORKPAD_COMMENT_ID=...
    WORKPAD_ISSUE_ID=...
    WORKPAD_ISSUE_IDENTIFIER=...
    WORKPAD_ACTION=create|update|noop
    WORKPAD_DUPLICATE_COUNT=...
    WORKPAD_WARNING=...
    WORKPAD_ERROR=...

  `latest-human`:
    WORKPAD_STATUS=found|missing|error
    LATEST_NON_WORKPAD_COMMENT_ID=...
    LATEST_NON_WORKPAD_COMMENT_FILE=...
    LATEST_NON_WORKPAD_COMMENT_AUTHOR=...
    LATEST_NON_WORKPAD_COMMENT_UPDATED_AT=...

Examples:
  bash scripts/utilities/symphony_linear_workpad.sh \
    show --issue-identifier ALL-123

  bash scripts/utilities/symphony_linear_workpad.sh \
    upsert --issue-identifier ALL-123 --body-file /tmp/workpad.md

  bash scripts/utilities/symphony_linear_workpad.sh \
    append-section \
    --issue-identifier ALL-123 \
    --section-title "Claude Feedback Disposition" \
    --section-file /tmp/disposition.md

  bash scripts/utilities/symphony_linear_workpad.sh \
    append-section \
    --issue-identifier ALL-123 \
    --section-title "Review Handoff" \
    --section-stdin <<'SYMPHONY_SECTION'
- Outcome: Preserved literal markdown like `code` and $(commands).
- Validation: shell-safe stdin path.
SYMPHONY_SECTION

  bash scripts/utilities/symphony_linear_workpad.sh \
    latest-human --issue-identifier ALL-123 --output-file /tmp/latest-human.md

Notes:
  - The managed workpad marker is inserted automatically when missing.
  - Duplicate workpad comments are never deleted automatically. The helper picks
    the most recently updated valid workpad comment and reports the duplicate count.
  - If you are unsure about behavior, run this helper with `--help` first.

Related helpers:
  - `symphony_linear_issue_context.sh` for canonical context and classification.
  - `symphony_linear_issue_state.sh` for routine state transitions.

Exit codes:
  0  Success.
  2  Invalid arguments.
  3  Linear request or response failure.
EOF
}

subcommand="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "${subcommand}" in
  show|upsert|append-section|latest-human)
    ;;
  -h|--help|"")
    usage
    exit 0
    ;;
  *)
    echo "Unknown subcommand: ${subcommand}" >&2
    usage >&2
    exit 2
    ;;
esac

issue_identifier=""
issue_id=""
comment_id=""
body_file=""
section_title=""
section_file=""
section_stdin=0
comments_first=50
linear_api_key=""
context_json_file=""
linear_json_file=""
output_file=""
json_output_file=""
format="env"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue-identifier)
      issue_identifier="${2:-}"
      shift 2
      ;;
    --issue-id)
      issue_id="${2:-}"
      shift 2
      ;;
    --comment-id)
      comment_id="${2:-}"
      shift 2
      ;;
    --body-file)
      body_file="${2:-}"
      shift 2
      ;;
    --section-title)
      section_title="${2:-}"
      shift 2
      ;;
    --section-file)
      section_file="${2:-}"
      shift 2
      ;;
    --section-stdin)
      section_stdin=1
      shift
      ;;
    --comments-first)
      comments_first="${2:-}"
      shift 2
      ;;
    --linear-api-key)
      linear_api_key="${2:-}"
      shift 2
      ;;
    --context-json-file)
      context_json_file="${2:-}"
      shift 2
      ;;
    --linear-json-file)
      linear_json_file="${2:-}"
      shift 2
      ;;
    --output-file)
      output_file="${2:-}"
      shift 2
      ;;
    --json-output-file)
      json_output_file="${2:-}"
      shift 2
      ;;
    --format)
      format="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "${format}" in
  env|json|pretty)
    ;;
  *)
    echo "--format must be one of: env, json, pretty" >&2
    exit 2
    ;;
esac

if ! [[ "${comments_first}" =~ ^[0-9]+$ ]]; then
  echo "--comments-first must be a non-negative integer." >&2
  exit 2
fi

if [[ -z "${context_json_file}" && -z "${issue_identifier}" && -z "${issue_id}" ]]; then
  echo "Either --context-json-file or one of --issue-identifier/--issue-id is required." >&2
  exit 2
fi

if [[ "${subcommand}" == "upsert" && -z "${body_file}" ]]; then
  echo "--body-file is required for upsert." >&2
  exit 2
fi

if [[ "${subcommand}" == "append-section" ]]; then
  if [[ -z "${section_title}" ]]; then
    echo "--section-title is required for append-section." >&2
    exit 2
  fi
  if [[ -z "${section_file}" && "${section_stdin}" -eq 0 ]]; then
    echo "Either --section-file or --section-stdin is required for append-section." >&2
    exit 2
  fi
  if [[ -n "${section_file}" && "${section_stdin}" -eq 1 ]]; then
    echo "--section-file and --section-stdin are mutually exclusive." >&2
    exit 2
  fi
fi

emit_result() {
  local payload="$1"
  if [[ -n "${json_output_file}" ]]; then
    printf '%s\n' "${payload}" > "${json_output_file}"
  fi

  case "${format}" in
    json)
      printf '%s\n' "${payload}"
      ;;
    pretty)
      jq -r '
        [
          "Symphony workpad operation",
          "",
          "Status: \(.status // "unknown")",
          "Action: \(.action // "n/a")",
          "Issue: \(.issue_identifier // "")",
          "Comment id: \(.comment_id // "none")",
          "Duplicate workpads: \(.duplicate_workpad_count // 0)",
          "Body file: \(.body_file // .latest_non_workpad_comment_file // "none")",
          "Warning: \(.warning // "none")"
        ] | join("\n")
      ' <<< "${payload}"
      ;;
    env)
      jq -r '
        to_entries
        | map("\(.key|ascii_upcase)=\(.value // "")")
        | .[]
      ' <<< "${payload}"
      ;;
  esac
}

build_env_payload() {
  local status="$1"
  local action="$2"
  local issue_identifier_value="$3"
  local issue_id_value="$4"
  local comment_id_value="$5"
  local body_path_value="${6-}"
  local duplicate_count_value="${7:-0}"
  local warning_value="${8-}"
  local extra_json="${9:-{}}"

  jq -cn \
    --arg status "${status}" \
    --arg action "${action}" \
    --arg issue_identifier "${issue_identifier_value}" \
    --arg issue_id "${issue_id_value}" \
    --arg comment_id "${comment_id_value}" \
    --arg body_file "${body_path_value}" \
    --argjson duplicate_workpad_count "${duplicate_count_value}" \
    --arg warning "${warning_value}" \
    --argjson extra "${extra_json}" '
    {
      workpad_status: $status,
      workpad_action: $action,
      workpad_issue_identifier: $issue_identifier,
      workpad_issue_id: $issue_id,
      workpad_comment_id: $comment_id,
      workpad_body_file: $body_file,
      workpad_duplicate_count: $duplicate_workpad_count,
      workpad_warning: $warning
    } + $extra
  '
}

resolve_context_json_file() {
  if [[ -n "${context_json_file}" ]]; then
    printf '%s' "${context_json_file}"
    return 0
  fi

  local context_output temp_json
  local -a cmd
  temp_json="$(mktemp /tmp/symphony-workpad-context-XXXXXX.json)"
  cmd=(
    bash "${CONTEXT_HELPER}"
    --comments-first "${comments_first}"
    --json-output-file "${temp_json}"
  )
  if [[ -n "${issue_identifier}" ]]; then
    cmd+=(--issue-identifier "${issue_identifier}")
  fi
  if [[ -n "${issue_id}" ]]; then
    cmd+=(--issue-id "${issue_id}")
  fi
  if [[ -n "${linear_json_file}" ]]; then
    cmd+=(--linear-json-file "${linear_json_file}")
  fi
  if [[ -n "${linear_api_key}" ]]; then
    cmd+=(--linear-api-key "${linear_api_key}")
  fi

  set +e
  context_output="$("${cmd[@]}" 2>&1)"
  local rc=$?
  set -e

  if [[ "${rc}" -ne 0 ]]; then
    rm -f "${temp_json}"
    echo "${context_output}" >&2
    return 1
  fi

  printf '%s' "${temp_json}"
}

create_comment() {
  local issue_id_value="$1"
  local body_value="$2"
  local query response error

  query='
mutation SymphonyCreateWorkpadComment($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) {
    success
    comment {
      id
      body
      updatedAt
    }
  }
}'

  response="$(symphony_linear_graphql \
    "${linear_api_key}" \
    "${query}" \
    "$(jq -cn --arg issueId "${issue_id_value}" --arg body "${body_value}" '{issueId: $issueId, body: $body}')")"
  error="$(symphony_linear_response_error "${response}")"
  if [[ -n "${error}" ]]; then
    echo "${error}" >&2
    return 1
  fi
  printf '%s' "${response}"
}

update_comment() {
  local comment_id_value="$1"
  local body_value="$2"
  local query response error

  query='
mutation SymphonyUpdateWorkpadComment($commentId: String!, $body: String!) {
  commentUpdate(id: $commentId, input: {body: $body}) {
    success
    comment {
      id
      body
      updatedAt
    }
  }
}'

  response="$(symphony_linear_graphql \
    "${linear_api_key}" \
    "${query}" \
    "$(jq -cn --arg commentId "${comment_id_value}" --arg body "${body_value}" '{commentId: $commentId, body: $body}')")"
  error="$(symphony_linear_response_error "${response}")"
  if [[ -n "${error}" ]]; then
    echo "${error}" >&2
    return 1
  fi
  printf '%s' "${response}"
}

replace_or_append_section() {
  local current_body="$1"
  local title="$2"
  local section_body="$3"

  CURRENT_BODY="${current_body}" SECTION_BODY="${section_body}" python3 - "$title" <<'PY'
import re
import sys
import os

title = sys.argv[1]
current_body = os.environ.get("CURRENT_BODY", "")
section_body = os.environ.get("SECTION_BODY", "")
heading = f"## {title}"
replacement = f"{heading}\n\n{section_body.strip()}\n"
pattern = re.compile(rf"(?ms)^## {re.escape(title)}\n.*?(?=^## |\Z)")

if pattern.search(current_body):
    updated = pattern.sub(replacement, current_body, count=1)
else:
    trimmed = current_body.rstrip()
    if trimmed:
        updated = f"{trimmed}\n\n{replacement}"
    else:
        updated = replacement

sys.stdout.write(updated.rstrip() + "\n")
PY
}

context_json_path="$(resolve_context_json_file)" || exit 3
context_json="$(cat "${context_json_path}")"

resolved_issue_id="$(jq -r '.issue.id // ""' <<< "${context_json}")"
resolved_issue_identifier="$(jq -r '.issue.identifier // ""' <<< "${context_json}")"
resolved_duplicate_count="$(jq -r '.duplicate_workpad_count // 0' <<< "${context_json}")"
resolved_warning=""
if [[ "${resolved_duplicate_count}" -gt 0 ]]; then
  resolved_warning="Multiple workpad comments found; targeting the most recently updated one."
fi

selected_comment_json="$(jq -c \
  --arg comment_id "${comment_id}" '
  if $comment_id != "" then
    (.comments[] | select(.id == $comment_id)) // empty
  else
    .workpad_comment // empty
  end
' <<< "${context_json}")"

case "${subcommand}" in
  show)
    if [[ -z "${selected_comment_json}" ]]; then
      payload="$(build_env_payload \
        "missing" \
        "noop" \
        "${resolved_issue_identifier}" \
        "${resolved_issue_id}" \
        "" \
        "" \
        "${resolved_duplicate_count}" \
        "${resolved_warning}")"
      emit_result "${payload}"
      exit 0
    fi

    if [[ -z "${output_file}" ]]; then
      output_file="$(mktemp /tmp/symphony-workpad-body-XXXXXX.md)"
    fi
    jq -r '.body // ""' <<< "${selected_comment_json}" > "${output_file}"

    payload="$(build_env_payload \
      "found" \
      "noop" \
      "${resolved_issue_identifier}" \
      "${resolved_issue_id}" \
      "$(jq -r '.id // ""' <<< "${selected_comment_json}")" \
      "${output_file}" \
      "${resolved_duplicate_count}" \
      "${resolved_warning}")"
    emit_result "${payload}"
    ;;
  latest-human)
    latest_comment_json="$(jq -c '.latest_non_workpad_comment // empty' <<< "${context_json}")"
    if [[ -z "${latest_comment_json}" ]]; then
      payload="$(jq -cn \
        --arg status "missing" \
        '{workpad_status: $status}')"
      emit_result "${payload}"
      exit 0
    fi

    if [[ -z "${output_file}" ]]; then
      output_file="$(mktemp /tmp/symphony-latest-human-XXXXXX.md)"
    fi
    jq -r '.body // ""' <<< "${latest_comment_json}" > "${output_file}"

    payload="$(jq -cn \
      --arg status "found" \
      --arg comment_id "$(jq -r '.id // ""' <<< "${latest_comment_json}")" \
      --arg body_file "${output_file}" \
      --arg author "$(jq -r '.user_name // ""' <<< "${latest_comment_json}")" \
      --arg updated_at "$(jq -r '.updated_at // ""' <<< "${latest_comment_json}")" '
      {
        workpad_status: $status,
        latest_non_workpad_comment_id: $comment_id,
        latest_non_workpad_comment_file: $body_file,
        latest_non_workpad_comment_author: $author,
        latest_non_workpad_comment_updated_at: $updated_at
      }')"
    emit_result "${payload}"
    ;;
  upsert|append-section)
    if ! linear_api_key="$(symphony_linear_read_api_key "${linear_api_key}")"; then
      payload="$(jq -cn \
        --arg status "error" \
        --arg error "No Linear API key found. Set --linear-api-key, export LINEAR_API_KEY, or run bash scripts/utilities/symphony_materialize_linear_auth.sh." '
        {workpad_status: $status, workpad_error: $error}')"
      emit_result "${payload}"
      exit 3
    fi

    if [[ "${subcommand}" == "upsert" ]]; then
      requested_body="$(cat "${body_file}")"
    else
      if [[ "${section_stdin}" -eq 1 ]]; then
        section_body="$(cat)"
      else
        section_body="$(cat "${section_file}")"
      fi
      if [[ -n "${selected_comment_json}" ]]; then
        current_body="$(jq -r '.body // ""' <<< "${selected_comment_json}")"
      else
        current_body=""
      fi
      requested_body="$(replace_or_append_section "${current_body}" "${section_title}" "${section_body}")"
    fi

    requested_body="$(symphony_linear_ensure_workpad_marker "${requested_body}" "${resolved_issue_identifier}")"

    target_comment_id="${comment_id:-$(jq -r '.workpad_comment.id // ""' <<< "${context_json}")}"
    existing_body=""
    if [[ -n "${target_comment_id}" ]]; then
      existing_body="$(jq -r --arg target_comment_id "${target_comment_id}" '
        (.comments[] | select(.id == $target_comment_id) | .body) // ""
      ' <<< "${context_json}")"
    fi

    if [[ -n "${target_comment_id}" && "${existing_body}" == "${requested_body}" ]]; then
      payload="$(build_env_payload \
        "unchanged" \
        "noop" \
        "${resolved_issue_identifier}" \
        "${resolved_issue_id}" \
        "${target_comment_id}" \
        "" \
        "${resolved_duplicate_count}" \
        "${resolved_warning}")"
      emit_result "${payload}"
      exit 0
    fi

    if [[ -n "${target_comment_id}" ]]; then
      if ! mutation_json="$(update_comment "${target_comment_id}" "${requested_body}")"; then
        payload="$(jq -cn \
          --arg status "error" \
          --arg error "Failed to update workpad comment." '
          {workpad_status: $status, workpad_error: $error}')"
        emit_result "${payload}"
        exit 3
      fi
      mutation_success="$(jq -r '.data.commentUpdate.success // false' <<< "${mutation_json}")"
      if [[ "${mutation_success}" != "true" ]]; then
        payload="$(jq -cn \
          --arg status "error" \
          --arg error "Linear commentUpdate did not succeed." '
          {workpad_status: $status, workpad_error: $error}')"
        emit_result "${payload}"
        exit 3
      fi
        payload="$(build_env_payload \
        "updated" \
        "update" \
        "${resolved_issue_identifier}" \
        "${resolved_issue_id}" \
        "$(jq -r --arg fallback "${target_comment_id}" '.data.commentUpdate.comment.id // $fallback' <<< "${mutation_json}")" \
        "" \
        "${resolved_duplicate_count}" \
        "${resolved_warning}")"
      emit_result "${payload}"
      exit 0
    fi

    if ! mutation_json="$(create_comment "${resolved_issue_id}" "${requested_body}")"; then
      payload="$(jq -cn \
        --arg status "error" \
        --arg error "Failed to create workpad comment." '
        {workpad_status: $status, workpad_error: $error}')"
      emit_result "${payload}"
      exit 3
    fi
    mutation_success="$(jq -r '.data.commentCreate.success // false' <<< "${mutation_json}")"
    if [[ "${mutation_success}" != "true" ]]; then
      payload="$(jq -cn \
        --arg status "error" \
        --arg error "Linear commentCreate did not succeed." '
        {workpad_status: $status, workpad_error: $error}')"
      emit_result "${payload}"
      exit 3
    fi
    payload="$(build_env_payload \
      "created" \
      "create" \
      "${resolved_issue_identifier}" \
      "${resolved_issue_id}" \
      "$(jq -r '.data.commentCreate.comment.id // ""' <<< "${mutation_json}")" \
      "" \
      "${resolved_duplicate_count}" \
      "${resolved_warning}")"
    emit_result "${payload}"
    ;;
esac
