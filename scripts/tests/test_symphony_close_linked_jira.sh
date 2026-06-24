#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_close_linked_jira.sh"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/assertions.sh"

# Write a normalized Linear context JSON like symphony_linear_issue_context.sh emits.
write_context_json() {
  local path="$1"
  local attachment_url="$2"
  local description="$3"
  local title="${4:-Implement a thing}"

  local attachments='[]'
  if [[ -n "${attachment_url}" ]]; then
    attachments="$(jq -cn --arg url "${attachment_url}" '[{id:"att-1", title:"Jira", url:$url}]')"
  fi

  jq -n \
    --argjson attachments "${attachments}" \
    --arg description "${description}" \
    --arg title "${title}" '
    {
      status: "ok",
      issue: {
        id: "issue-all-123",
        identifier: "ALL-123",
        title: $title,
        description: $description,
        url: "https://linear.example/ALL-123",
        state: {name: "Done"}
      },
      labels: [],
      attachments: $attachments,
      attachments_count: ($attachments | length),
      team: {states: [{id: "state-done", name: "Done"}]},
      comments: []
    }' > "${path}"
}

# A stub Jira transport invoked as: STUB METHOD URLPATH [BODY].
# Behavior is driven by env vars so each test can shape responses.
write_jira_transport() {
  local path="$1"
  cat > "${path}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
method="$1"
url_path="$2"
body="${3:-}"

if [[ -n "${SYMPHONY_TEST_JIRA_LOG:-}" ]]; then
  printf '%s %s\n' "${method}" "${url_path}" >> "${SYMPHONY_TEST_JIRA_LOG}"
fi

category="${SYMPHONY_TEST_JIRA_CATEGORY:-indeterminate}"
token_present="${SYMPHONY_TEST_JIRA_TOKEN_PRESENT:-0}"

case "${method} ${url_path}" in
  "GET "*"/transitions")
    echo '{"transitions":[{"id":"11","name":"In Progress","to":{"statusCategory":{"key":"indeterminate"}}},{"id":"31","name":"Done","to":{"statusCategory":{"key":"done"}}}]}'
    ;;
  "GET "*"/comment"*)
    if [[ "${token_present}" == "1" ]]; then
      echo '{"comments":[{"body":"... symphony-finalize-jira-close:PROJ-101:ALL-123 ..."}]}'
    else
      echo '{"comments":[]}'
    fi
    ;;
  "GET "*"/issue/"*)
    printf '{"fields":{"status":{"statusCategory":{"key":"%s"}}}}\n' "${category}"
    ;;
  "POST "*)
    echo '{}'
    ;;
  *)
    echo '{}'
    ;;
esac
EOF
  chmod +x "${path}"
}

# Always isolate inherited Jira env so tests are deterministic. Using `env`
# (an external command) lets SYMPHONY_TEST_* assignments placed after it export
# cleanly into the script's child process.
JIRA_ENV_RESET=(env -u JIRA_EMAIL -u JIRA_API_KEY -u JIRA_URL)

test_resolve_key_from_attachment() {
  local tmp ctx out
  tmp="$(mktemp -d)"; ctx="${tmp}/ctx.json"; out="${tmp}/out.txt"
  write_context_json "${ctx}" "https://jira.example.com/browse/PROJ-101" "No jira here."
  "${JIRA_ENV_RESET[@]}" bash "${SCRIPT_PATH}" --context-json-file "${ctx}" --resolve-only > "${out}"
  assert_contains "CLOSE_LINKED_JIRA_STATUS=resolved" "${out}"
  assert_contains "CLOSE_LINKED_JIRA_KEY=PROJ-101" "${out}"
  assert_contains "CLOSE_LINKED_JIRA_LINK_SOURCE=attachment" "${out}"
  rm -rf "${tmp}"
}

test_resolve_key_from_description() {
  local tmp ctx out
  tmp="$(mktemp -d)"; ctx="${tmp}/ctx.json"; out="${tmp}/out.txt"
  write_context_json "${ctx}" "" "## Jira Metadata\n* Jira Key: [PROJ-102](https://jira.example.com/browse/PROJ-102)"
  "${JIRA_ENV_RESET[@]}" bash "${SCRIPT_PATH}" --context-json-file "${ctx}" --resolve-only > "${out}"
  assert_contains "CLOSE_LINKED_JIRA_KEY=PROJ-102" "${out}"
  assert_contains "CLOSE_LINKED_JIRA_LINK_SOURCE=description" "${out}"
  rm -rf "${tmp}"
}

# A `<KEY>:` title prefix is intentionally NOT used for discovery (it could
# match non-Jira tokens like "GPT-4: ..."). A title-only key must resolve to
# no_link.
test_title_prefix_is_not_used_for_discovery() {
  local tmp ctx out
  tmp="$(mktemp -d)"; ctx="${tmp}/ctx.json"; out="${tmp}/out.txt"
  write_context_json "${ctx}" "" "Plain description." "PROJ-103: a title-only key"
  "${JIRA_ENV_RESET[@]}" bash "${SCRIPT_PATH}" --context-json-file "${ctx}" > "${out}"
  assert_contains "CLOSE_LINKED_JIRA_STATUS=no_link" "${out}"
  rm -rf "${tmp}"
}

# A title that merely looks like a key (e.g. "GPT-4: ...") must not be closed.
test_non_jira_title_token_is_ignored() {
  local tmp ctx out
  tmp="$(mktemp -d)"; ctx="${tmp}/ctx.json"; out="${tmp}/out.txt"
  write_context_json "${ctx}" "" "No link here." "GPT-4: turbo upgrade notes"
  "${JIRA_ENV_RESET[@]}" bash "${SCRIPT_PATH}" --context-json-file "${ctx}" > "${out}"
  assert_contains "CLOSE_LINKED_JIRA_STATUS=no_link" "${out}"
  rm -rf "${tmp}"
}

test_no_link_reports_no_link() {
  local tmp ctx out
  tmp="$(mktemp -d)"; ctx="${tmp}/ctx.json"; out="${tmp}/out.txt"
  write_context_json "${ctx}" "" "Nothing linked here." "Just a title"
  "${JIRA_ENV_RESET[@]}" bash "${SCRIPT_PATH}" --context-json-file "${ctx}" > "${out}"
  assert_contains "CLOSE_LINKED_JIRA_STATUS=no_link" "${out}"
  assert_contains "CLOSE_LINKED_JIRA_LINK_SOURCE=none" "${out}"
  rm -rf "${tmp}"
}

test_missing_creds_reports_no_jira_creds() {
  local tmp ctx out
  tmp="$(mktemp -d)"; ctx="${tmp}/ctx.json"; out="${tmp}/out.txt"
  write_context_json "${ctx}" "https://jira.example.com/browse/PROJ-101" "x"
  "${JIRA_ENV_RESET[@]}" bash "${SCRIPT_PATH}" --context-json-file "${ctx}" \
    --jira-creds-file "${tmp}/does-not-exist.env" > "${out}"
  assert_contains "CLOSE_LINKED_JIRA_STATUS=no_jira_creds" "${out}"
  assert_contains "CLOSE_LINKED_JIRA_KEY=PROJ-101" "${out}"
  rm -rf "${tmp}"
}

test_already_done_skips() {
  local tmp ctx out transport log
  tmp="$(mktemp -d)"; ctx="${tmp}/ctx.json"; out="${tmp}/out.txt"
  transport="${tmp}/jira.sh"; log="${tmp}/jira.log"
  write_context_json "${ctx}" "https://jira.example.com/browse/PROJ-101" "x"
  write_jira_transport "${transport}"
  SYMPHONY_TEST_JIRA_LOG="${log}" SYMPHONY_TEST_JIRA_CATEGORY="done" \
    "${JIRA_ENV_RESET[@]}" bash "${SCRIPT_PATH}" --context-json-file "${ctx}" \
      --jira-email "t@example.com" --jira-api-key "tok" --jira-base-url "https://jira.example.com" \
      --jira-transport "${transport}" > "${out}"
  assert_contains "CLOSE_LINKED_JIRA_STATUS=already_done" "${out}"
  assert_contains "CLOSE_LINKED_JIRA_COMMENTED=skipped" "${out}"
  # No transition POST should have happened.
  assert_not_contains "POST /rest/api/3/issue/PROJ-101/transitions" "${log}"
  rm -rf "${tmp}"
}

test_dry_run_does_not_mutate() {
  local tmp ctx out transport log
  tmp="$(mktemp -d)"; ctx="${tmp}/ctx.json"; out="${tmp}/out.txt"
  transport="${tmp}/jira.sh"; log="${tmp}/jira.log"
  write_context_json "${ctx}" "https://jira.example.com/browse/PROJ-101" "x"
  write_jira_transport "${transport}"
  SYMPHONY_TEST_JIRA_LOG="${log}" SYMPHONY_TEST_JIRA_CATEGORY="indeterminate" \
    "${JIRA_ENV_RESET[@]}" bash "${SCRIPT_PATH}" --context-json-file "${ctx}" \
      --jira-email "t@example.com" --jira-api-key "tok" --jira-base-url "https://jira.example.com" \
      --jira-transport "${transport}" --dry-run > "${out}"
  assert_contains "CLOSE_LINKED_JIRA_STATUS=dry_run" "${out}"
  assert_not_contains "POST " "${log}"
  rm -rf "${tmp}"
}

test_close_comments_and_transitions() {
  local tmp ctx out transport log
  tmp="$(mktemp -d)"; ctx="${tmp}/ctx.json"; out="${tmp}/out.txt"
  transport="${tmp}/jira.sh"; log="${tmp}/jira.log"
  write_context_json "${ctx}" "https://jira.example.com/browse/PROJ-101" "x"
  write_jira_transport "${transport}"
  SYMPHONY_TEST_JIRA_LOG="${log}" SYMPHONY_TEST_JIRA_CATEGORY="indeterminate" SYMPHONY_TEST_JIRA_TOKEN_PRESENT="0" \
    "${JIRA_ENV_RESET[@]}" bash "${SCRIPT_PATH}" --context-json-file "${ctx}" \
      --jira-email "t@example.com" --jira-api-key "tok" --jira-base-url "https://jira.example.com" \
      --jira-transport "${transport}" > "${out}"
  assert_contains "CLOSE_LINKED_JIRA_STATUS=closed" "${out}"
  assert_contains "CLOSE_LINKED_JIRA_TRANSITION=Done" "${out}"
  assert_contains "CLOSE_LINKED_JIRA_COMMENTED=true" "${out}"
  assert_contains "POST /rest/api/3/issue/PROJ-101/comment" "${log}"
  assert_contains "POST /rest/api/3/issue/PROJ-101/transitions" "${log}"
  rm -rf "${tmp}"
}

test_close_is_idempotent_on_comment() {
  local tmp ctx out transport log
  tmp="$(mktemp -d)"; ctx="${tmp}/ctx.json"; out="${tmp}/out.txt"
  transport="${tmp}/jira.sh"; log="${tmp}/jira.log"
  write_context_json "${ctx}" "https://jira.example.com/browse/PROJ-101" "x"
  write_jira_transport "${transport}"
  SYMPHONY_TEST_JIRA_LOG="${log}" SYMPHONY_TEST_JIRA_CATEGORY="indeterminate" SYMPHONY_TEST_JIRA_TOKEN_PRESENT="1" \
    "${JIRA_ENV_RESET[@]}" bash "${SCRIPT_PATH}" --context-json-file "${ctx}" \
      --jira-email "t@example.com" --jira-api-key "tok" --jira-base-url "https://jira.example.com" \
      --jira-transport "${transport}" > "${out}"
  assert_contains "CLOSE_LINKED_JIRA_STATUS=closed" "${out}"
  assert_contains "CLOSE_LINKED_JIRA_COMMENTED=skipped" "${out}"
  assert_not_contains "POST /rest/api/3/issue/PROJ-101/comment" "${log}"
  assert_contains "POST /rest/api/3/issue/PROJ-101/transitions" "${log}"
  rm -rf "${tmp}"
}

test_resolve_key_from_attachment
test_resolve_key_from_description
test_title_prefix_is_not_used_for_discovery
test_non_jira_title_token_is_ignored
test_no_link_reports_no_link
test_missing_creds_reports_no_jira_creds
test_already_done_skips
test_dry_run_does_not_mutate
test_close_comments_and_transitions
test_close_is_idempotent_on_comment

echo "symphony_close_linked_jira tests passed"
