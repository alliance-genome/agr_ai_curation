#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
# shellcheck source=../lib/symphony_linear_common.sh
source "${REPO_ROOT}/scripts/lib/symphony_linear_common.sh"

usage() {
  cat <<'EOF'
Usage:
  symphony_linear_issue_context.sh --issue-identifier ISSUE [options]
  symphony_linear_issue_context.sh --issue-id ISSUE_ID [options]

Purpose:
  Fetch the canonical Symphony Linear issue context for one issue and normalize
  it into a stable machine-readable shape.

Why prefer this helper over raw GraphQL:
  - It centralizes the repo's workpad detection logic.
  - It returns a stable normalized payload for downstream helpers and lanes.
  - It avoids re-authoring routine Linear comment/state queries in each script.
  - It can also materialize a JSON artifact for reuse without re-querying Linear.

Options:
  --issue-identifier VALUE    Linear issue identifier such as ALL-123.
  --issue-id VALUE            Linear issue id; accepted as an alternative lookup key.
  --comments-first N          Number of comments to fetch. Default: 50.
  --history-first N           Number of history events to fetch. Default: 50.
  --include-history           Include issue history in the response and JSON artifact.
  --include-team-states       Include the issue team and available workflow states.
  --linear-api-key VALUE      Linear API key. Default: ~/.linear/api_key.txt.
  --output-file PATH          Write the selected stdout format to this file too.
  --json-output-file PATH     Write the normalized context JSON to this path.
  --format VALUE              One of: env, json, pretty. Default: env.
  --linear-json-file PATH     Testing/debug override: read a raw Linear GraphQL
                              issue response from this file instead of calling Linear.
  --help                      Show this help.

Defaults:
  - `--format env`
  - `--comments-first 50`
  - `--history-first 50`
  - `--include-history` is off
  - `--include-team-states` is off

Output contract:
  In `env` mode this helper emits stable summary lines including:
    LINEAR_CONTEXT_STATUS=ok|error
    LINEAR_CONTEXT_ISSUE_ID=...
    LINEAR_CONTEXT_ISSUE_IDENTIFIER=...
    LINEAR_CONTEXT_TITLE=...
    LINEAR_CONTEXT_STATE=...
    LINEAR_CONTEXT_URL=...
    LINEAR_CONTEXT_COMMENTS_COUNT=...
    LINEAR_CONTEXT_WORKPAD_COMMENT_ID=...
    LINEAR_CONTEXT_LATEST_NON_WORKPAD_COMMENT_ID=...
    LINEAR_CONTEXT_JSON_FILE=...
    LINEAR_CONTEXT_ERROR=...

  The normalized JSON artifact contains:
    - issue metadata
    - labels
    - comments with workpad classification
    - latest workpad comment
    - latest non-workpad comment
    - optional history
    - optional team states

Examples:
  bash scripts/utilities/symphony_linear_issue_context.sh \
    --issue-identifier ALL-123

  bash scripts/utilities/symphony_linear_issue_context.sh \
    --issue-identifier ALL-123 \
    --include-history \
    --include-team-states \
    --json-output-file /tmp/all-123-context.json

  bash scripts/utilities/symphony_linear_issue_context.sh \
    --issue-id 7f4d... \
    --format pretty

  bash scripts/utilities/symphony_linear_issue_context.sh \
    --issue-identifier ALL-123 \
    --linear-json-file /tmp/issue-response.json \
    --format json

Notes:
  - Workpad comments are identified by the stable marker prefix:
      <!-- symphony-workpad:
  - The latest non-workpad comment is derived here so lane helpers do not need
    to duplicate comment classification logic.
  - Use `linear_graphql` only for unusual diagnostics or one-off reads that are
    outside this helper's scope.

Exit codes:
  0  Success.
  2  Invalid arguments or missing required input.
  3  Linear request or response failure.
EOF
}

issue_identifier=""
issue_id=""
comments_first=50
history_first=50
include_history=0
include_team_states=0
linear_api_key=""
output_file=""
json_output_file=""
format="env"
linear_json_file=""

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
    --comments-first)
      comments_first="${2:-}"
      shift 2
      ;;
    --history-first)
      history_first="${2:-}"
      shift 2
      ;;
    --include-history)
      include_history=1
      shift
      ;;
    --include-team-states)
      include_team_states=1
      shift
      ;;
    --linear-api-key)
      linear_api_key="${2:-}"
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
    --linear-json-file)
      linear_json_file="${2:-}"
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

if [[ -z "${issue_identifier}" && -z "${issue_id}" ]]; then
  echo "Either --issue-identifier or --issue-id is required." >&2
  usage >&2
  exit 2
fi

if ! [[ "${comments_first}" =~ ^[0-9]+$ ]]; then
  echo "--comments-first must be a non-negative integer." >&2
  exit 2
fi

if ! [[ "${history_first}" =~ ^[0-9]+$ ]]; then
  echo "--history-first must be a non-negative integer." >&2
  exit 2
fi

case "${format}" in
  env|json|pretty)
    ;;
  *)
    echo "--format must be one of: env, json, pretty" >&2
    exit 2
    ;;
esac

issue_lookup="${issue_id:-${issue_identifier}}"
include_history_json="false"
include_team_states_json="false"
if [[ "${include_history}" -eq 1 ]]; then
  include_history_json="true"
fi
if [[ "${include_team_states}" -eq 1 ]]; then
  include_team_states_json="true"
fi

if [[ -z "${linear_json_file}" ]]; then
  if ! linear_api_key="$(symphony_linear_read_api_key "${linear_api_key}")"; then
    symphony_linear_emit_env "LINEAR_CONTEXT_STATUS" "error"
    symphony_linear_emit_env "LINEAR_CONTEXT_ERROR" \
      "No Linear API key found. Set --linear-api-key or create ~/.linear/api_key.txt"
    exit 3
  fi
fi

graphql_query='
query SymphonyIssueContext(
  $issueId: String!,
  $commentsFirst: Int!,
  $historyFirst: Int!,
  $includeHistory: Boolean!,
  $includeTeamStates: Boolean!
) {
  issue(id: $issueId) {
    id
    identifier
    title
    description
    url
    createdAt
    updatedAt
    priority
    state {
      id
      name
      type
    }
    labels {
      nodes {
        id
        name
        color
      }
    }
    comments(first: $commentsFirst) {
      nodes {
        id
        body
        createdAt
        updatedAt
        user {
          id
          name
          displayName
        }
      }
    }
    history(first: $historyFirst) @include(if: $includeHistory) {
      nodes {
        id
        createdAt
        fromState {
          id
          name
          type
        }
        toState {
          id
          name
          type
        }
      }
    }
    team @include(if: $includeTeamStates) {
      id
      name
      key
      states(first: 100) {
        nodes {
          id
          name
          type
          position
        }
      }
    }
  }
}'

if [[ -n "${linear_json_file}" ]]; then
  response_json="$(cat "${linear_json_file}")"
else
  variables_json="$(jq -cn \
    --arg issueId "${issue_lookup}" \
    --argjson commentsFirst "${comments_first}" \
    --argjson historyFirst "${history_first}" \
    --argjson includeHistory "${include_history_json}" \
    --argjson includeTeamStates "${include_team_states_json}" \
    '{
      issueId: $issueId,
      commentsFirst: $commentsFirst,
      historyFirst: $historyFirst,
      includeHistory: $includeHistory,
      includeTeamStates: $includeTeamStates
    }')"

  if ! response_json="$(symphony_linear_graphql "${linear_api_key}" "${graphql_query}" "${variables_json}")"; then
    symphony_linear_emit_env "LINEAR_CONTEXT_STATUS" "error"
    symphony_linear_emit_env "LINEAR_CONTEXT_ERROR" "Linear request failed."
    exit 3
  fi
fi

response_error="$(symphony_linear_response_error "${response_json}")"
if [[ -n "${response_error}" ]]; then
  symphony_linear_emit_env "LINEAR_CONTEXT_STATUS" "error"
  symphony_linear_emit_env "LINEAR_CONTEXT_ERROR" "${response_error}"
  exit 3
fi

if [[ "$(jq -r '(.data.issue.id // .data.issue.identifier // empty)' <<< "${response_json}")" == "" ]]; then
  symphony_linear_emit_env "LINEAR_CONTEXT_STATUS" "error"
  symphony_linear_emit_env "LINEAR_CONTEXT_ERROR" \
    "Could not resolve Linear issue ${issue_lookup}."
  exit 3
fi

normalized_json="$(symphony_linear_normalize_context \
  "${response_json}" \
  "${SYMPHONY_LINEAR_WORKPAD_MARKER_PREFIX}")"

if [[ -z "${json_output_file}" ]]; then
  json_output_file="$(mktemp /tmp/symphony-linear-context-XXXXXX.json)"
fi
printf '%s\n' "${normalized_json}" > "${json_output_file}"

if [[ "${format}" == "json" ]]; then
  symphony_linear_emit_result "json" "${normalized_json}" "${output_file}"
  exit 0
fi

if [[ "${format}" == "pretty" ]]; then
  pretty_output="$(symphony_linear_pretty_context "${normalized_json}")"
  symphony_linear_emit_result "pretty" "${pretty_output}" "${output_file}"
  exit 0
fi

env_output="$(
  {
    symphony_linear_emit_env "LINEAR_CONTEXT_STATUS" "ok"
    symphony_linear_emit_env "LINEAR_CONTEXT_ISSUE_ID" \
      "$(jq -r '.issue.id // ""' <<< "${normalized_json}")"
    symphony_linear_emit_env "LINEAR_CONTEXT_ISSUE_IDENTIFIER" \
      "$(jq -r '.issue.identifier // ""' <<< "${normalized_json}")"
    symphony_linear_emit_env "LINEAR_CONTEXT_TITLE" \
      "$(jq -r '.issue.title // ""' <<< "${normalized_json}")"
    symphony_linear_emit_env "LINEAR_CONTEXT_STATE" \
      "$(jq -r '.issue.state.name // ""' <<< "${normalized_json}")"
    symphony_linear_emit_env "LINEAR_CONTEXT_URL" \
      "$(jq -r '.issue.url // ""' <<< "${normalized_json}")"
    symphony_linear_emit_env "LINEAR_CONTEXT_COMMENTS_COUNT" \
      "$(jq -r '.comments_count // 0' <<< "${normalized_json}")"
    symphony_linear_emit_env "LINEAR_CONTEXT_WORKPAD_COMMENT_ID" \
      "$(jq -r '.workpad_comment.id // ""' <<< "${normalized_json}")"
    symphony_linear_emit_env "LINEAR_CONTEXT_LATEST_NON_WORKPAD_COMMENT_ID" \
      "$(jq -r '.latest_non_workpad_comment.id // ""' <<< "${normalized_json}")"
    symphony_linear_emit_env "LINEAR_CONTEXT_WORKPAD_DUPLICATE_COUNT" \
      "$(jq -r '.duplicate_workpad_count // 0' <<< "${normalized_json}")"
    symphony_linear_emit_env "LINEAR_CONTEXT_JSON_FILE" "${json_output_file}"
  }
)"

symphony_linear_emit_result "env" "${env_output}" "${output_file}"
