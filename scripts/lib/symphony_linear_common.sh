#!/usr/bin/env bash

# Shared internal helpers for Symphony's Linear shell scripts.
# This is an implementation detail, not a user-facing CLI contract.

set -euo pipefail

readonly SYMPHONY_LINEAR_WORKPAD_MARKER_PREFIX='<!-- symphony-workpad:'
readonly SYMPHONY_LINEAR_WORKPAD_MARKER_VERSION='v1'

symphony_linear_default_api_key_file() {
  printf '%s/.linear/api_key.txt' "${HOME}"
}

symphony_linear_trim() {
  local value="${1:-}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "${value}"
}

symphony_linear_sanitize_env_value() {
  local value="${1-}"
  value="${value//$'\r'/ }"
  value="${value//$'\n'/ }"
  printf '%s' "${value}"
}

symphony_linear_emit_env() {
  local key="$1"
  local value="${2-}"
  printf '%s=%s\n' "${key}" "$(symphony_linear_sanitize_env_value "${value}")"
}

symphony_linear_read_api_key() {
  local explicit_key="${1-}"
  if [[ -n "${explicit_key}" ]]; then
    symphony_linear_trim "${explicit_key}"
    return 0
  fi

  local env_key="${LINEAR_API_KEY:-}"
  env_key="$(symphony_linear_trim "${env_key}")"
  if [[ -n "${env_key}" ]]; then
    printf '%s' "${env_key}"
    return 0
  fi

  local key_file
  key_file="$(symphony_linear_default_api_key_file)"
  if [[ -r "${key_file}" ]]; then
    tr -d '[:space:]' < "${key_file}"
    return 0
  fi

  return 1
}

symphony_linear_workpad_marker() {
  local issue_identifier="${1-}"
  if [[ -n "${issue_identifier}" ]]; then
    printf '<!-- symphony-workpad:%s issue:%s -->' \
      "${SYMPHONY_LINEAR_WORKPAD_MARKER_VERSION}" \
      "${issue_identifier}"
    return 0
  fi

  printf '<!-- symphony-workpad:%s -->' "${SYMPHONY_LINEAR_WORKPAD_MARKER_VERSION}"
}

symphony_linear_ensure_workpad_marker() {
  local body="$1"
  local issue_identifier="${2-}"
  local marker
  marker="$(symphony_linear_workpad_marker "${issue_identifier}")"

  if [[ "${body}" == *"${SYMPHONY_LINEAR_WORKPAD_MARKER_PREFIX}"* ]]; then
    printf '%s' "${body}"
    return 0
  fi

  if [[ -n "${body}" ]]; then
    printf '%s\n\n%s' "${marker}" "${body}"
    return 0
  fi

  printf '%s\n\n# Symphony Workpad\n' "${marker}"
}

symphony_linear_graphql() {
  local api_key="$1"
  local query="$2"
  local variables_json="${3:-\{\}}"
  local payload response

  payload="$(jq -cn \
    --arg query "${query}" \
    --argjson variables "${variables_json}" \
    '{query: $query, variables: $variables}')"

  response="$(curl -fsS https://api.linear.app/graphql \
    -H "Authorization: ${api_key}" \
    -H "Content-Type: application/json" \
    --data "${payload}")"

  printf '%s' "${response}"
}

symphony_linear_response_error() {
  local response_json="$1"
  jq -r '
    if (.errors // []) | length > 0 then
      [.errors[]?.message // "Unknown Linear GraphQL error"] | join("; ")
    elif (.data // null) == null then
      "Linear GraphQL response did not include data."
    else
      empty
    end
  ' <<< "${response_json}"
}

symphony_linear_emit_result() {
  local format="$1"
  local payload="$2"
  local output_file="${3-}"

  if [[ -n "${output_file}" ]]; then
    printf '%s\n' "${payload}" > "${output_file}"
  fi

  printf '%s\n' "${payload}"
}

symphony_linear_normalize_context() {
  local response_json="$1"
  local marker_prefix="$2"
  jq -c --arg marker_prefix "${marker_prefix}" '
    def normalize_comment:
      {
        id: (.id // ""),
        body: (.body // ""),
        created_at: (.createdAt // ""),
        updated_at: (.updatedAt // .createdAt // ""),
        user_id: (.user.id // ""),
        user_name: (.user.name // .user.displayName // "Unknown"),
        is_workpad: ((.body // "") | contains($marker_prefix))
      };

    def normalize_state:
      if . == null then null else {
        id: (.id // ""),
        name: (.name // ""),
        type: (.type // ""),
        position: (.position // null)
      } end;

    .data.issue as $issue
    | ($issue.comments.nodes // [] | map(normalize_comment) | sort_by(.created_at, .updated_at)) as $comments
    | ($comments | map(select(.is_workpad))) as $workpad_comments
    | ($workpad_comments | sort_by(.updated_at, .created_at) | last // null) as $workpad_comment
    | ($comments | map(select(.is_workpad | not)) | sort_by(.updated_at, .created_at) | last // null) as $latest_non_workpad
    | {
        status: "ok",
        issue: {
          id: ($issue.id // ""),
          identifier: ($issue.identifier // ""),
          title: ($issue.title // ""),
          description: ($issue.description // ""),
          url: ($issue.url // ""),
          created_at: ($issue.createdAt // ""),
          updated_at: ($issue.updatedAt // ""),
          priority: ($issue.priority // null),
          state: ($issue.state | normalize_state)
        },
        labels: ($issue.labels.nodes // [] | map({
          id: (.id // ""),
          name: (.name // ""),
          color: (.color // "")
        })),
        team: (
          if ($issue.team // null) == null then null else {
            id: ($issue.team.id // ""),
            name: ($issue.team.name // ""),
            key: ($issue.team.key // ""),
            states: ($issue.team.states.nodes // [] | map(normalize_state))
          } end
        ),
        comments: $comments,
        comments_count: ($comments | length),
        workpad_comment: $workpad_comment,
        workpad_comments: $workpad_comments,
        workpad_comments_count: ($workpad_comments | length),
        duplicate_workpad_count: (
          ($workpad_comments | length) as $count
          | if $count > 1 then ($count - 1) else 0 end
        ),
        latest_non_workpad_comment: $latest_non_workpad,
        history: (
          $issue.history.nodes // []
          | map({
              id: (.id // ""),
              created_at: (.createdAt // ""),
              from_state: (.fromState | normalize_state),
              to_state: (.toState | normalize_state)
            })
          | sort_by(.created_at)
        )
      }
  ' <<< "${response_json}"
}

symphony_linear_pretty_context() {
  local normalized_json="$1"
  jq -r '
    [
      "Symphony Linear issue context",
      "",
      "Issue: \(.issue.identifier) - \(.issue.title)",
      "State: \(.issue.state.name // "unknown")",
      "URL: \(.issue.url)",
      "Comments: \(.comments_count)",
      "Workpad comment: \(.workpad_comment.id // "none")",
      "Latest non-workpad comment: \(.latest_non_workpad_comment.id // "none")",
      "",
      "Labels: " + (
        if (.labels | length) == 0 then "none" else (.labels | map(.name) | join(", ")) end
      )
    ] | join("\n")
  ' <<< "${normalized_json}"
}

symphony_linear_json_field() {
  local json_file="$1"
  local jq_filter="$2"
  jq -r "${jq_filter}" "${json_file}"
}
