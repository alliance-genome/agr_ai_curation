#!/usr/bin/env bash

set -euo pipefail

# Close the Jira ticket linked to a finished Linear issue.
#
# Symphony's Finalizing lane calls this helper (best-effort) after a Linear
# issue reaches Done. The Jira link is discovered the same way Linear records
# it when importing from Jira: a Linear attachment whose URL points at
# <jira-base-url>/browse/<ISSUE-KEY>. A browse link in the issue description is
# the only fallback. The Jira base URL is read from configuration (JIRA_URL /
# credentials file), not hardcoded, so the helper is not tied to any one
# organization's Jira.
#
# This helper never blocks finalization. When there is no link, no Jira
# credentials, or the Jira request fails, it reports a status and exits 0 so the
# caller can record the outcome and still complete the issue.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
# shellcheck source=../lib/symphony_linear_common.sh
source "${REPO_ROOT}/scripts/lib/symphony_linear_common.sh"

CONTEXT_HELPER="${SYMPHONY_CLOSE_LINKED_JIRA_CONTEXT_HELPER:-${SCRIPT_DIR}/symphony_linear_issue_context.sh}"

usage() {
  cat <<'EOF'
Usage:
  symphony_close_linked_jira.sh --issue-identifier ISSUE [options]

Purpose:
  Transition the Jira ticket linked to a finished Linear issue to Done, with an
  idempotent, AI-attributed Jira comment. Designed to run from the Symphony
  Finalizing lane as a best-effort step that never blocks finalization.

Link discovery precedence (Jira issue keys such as PROJ-123):
  1. Linear attachment URL matching .../browse/<KEY> (most reliable).
  2. A browse/<KEY> link inside the Linear issue description.

Options:
  --issue-identifier VALUE    Linear issue identifier such as ALL-123.
  --issue-id VALUE            Linear issue id; alternative lookup key.
  --linear-api-key VALUE      Linear API key. Default: LINEAR_API_KEY or ~/.linear/api_key.txt.
  --context-json-file PATH    Use this normalized Linear context JSON instead of fetching.
  --linear-json-file PATH     Testing/debug override forwarded to the context helper.
  --context-helper PATH       Override the Linear context helper path.
  --agent-name VALUE          Attribution name for the Jira comment. Default: Symphony.
  --jira-base-url VALUE       Jira base URL. Default: JIRA_URL from env or the credentials file.
  --jira-email VALUE          Jira account email. Default: env or credentials file.
  --jira-api-key VALUE        Jira API token. Default: env or credentials file.
  --jira-creds-file PATH      Jira credentials file. Default: ~/.alliance/jira/.env.
  --jira-transport PATH       Testing seam: a script invoked as `PATH METHOD URLPATH [BODY]`
                              that prints the Jira JSON response; replaces curl.
  --resolve-only              Resolve the linked Jira key and exit without touching Jira.
  --dry-run                   Resolve the key and Jira status, but do not comment or transition.
  --json-output-file PATH     Write the JSON result to this path.
  --format VALUE              One of: env, json, pretty. Default: env.
  --help                      Show this help.

Output contract:
  CLOSE_LINKED_JIRA_STATUS=closed|already_done|no_link|no_jira_creds|dry_run|resolved|error
  CLOSE_LINKED_JIRA_KEY=PROJ-123 or empty
  CLOSE_LINKED_JIRA_LINEAR=ALL-123
  CLOSE_LINKED_JIRA_LINK_SOURCE=attachment|description|none
  CLOSE_LINKED_JIRA_TRANSITION=<transition name or empty>
  CLOSE_LINKED_JIRA_COMMENTED=true|false|skipped
  CLOSE_LINKED_JIRA_REASON=<short reason>

Exit codes:
  0  Any best-effort outcome (closed, already_done, no_link, no_jira_creds, dry_run, resolved).
  2  Invalid arguments.
  3  Could not resolve Linear context.
EOF
}

issue_identifier=""
issue_id=""
linear_api_key=""
context_json_file=""
linear_json_file=""
agent_name="Symphony"
jira_base_url=""
jira_email=""
jira_api_key=""
jira_creds_file="${HOME}/.alliance/jira/.env"
jira_transport=""
resolve_only=0
dry_run=0
json_output_file=""
format="env"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue-identifier) issue_identifier="${2:-}"; shift 2 ;;
    --issue-id) issue_id="${2:-}"; shift 2 ;;
    --linear-api-key) linear_api_key="${2:-}"; shift 2 ;;
    --context-json-file) context_json_file="${2:-}"; shift 2 ;;
    --linear-json-file) linear_json_file="${2:-}"; shift 2 ;;
    --context-helper) CONTEXT_HELPER="${2:-}"; shift 2 ;;
    --agent-name) agent_name="${2:-}"; shift 2 ;;
    --jira-base-url) jira_base_url="${2:-}"; shift 2 ;;
    --jira-email) jira_email="${2:-}"; shift 2 ;;
    --jira-api-key) jira_api_key="${2:-}"; shift 2 ;;
    --jira-creds-file) jira_creds_file="${2:-}"; shift 2 ;;
    --jira-transport) jira_transport="${2:-}"; shift 2 ;;
    --resolve-only) resolve_only=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    --json-output-file) json_output_file="${2:-}"; shift 2 ;;
    --format) format="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "${format}" in
  env|json|pretty) ;;
  *)
    echo "--format must be one of: env, json, pretty" >&2
    exit 2
    ;;
esac

if [[ -z "${context_json_file}" && -z "${issue_identifier}" && -z "${issue_id}" ]]; then
  echo "Either --context-json-file or one of --issue-identifier/--issue-id is required." >&2
  exit 2
fi

# ── Result emission ────────────────────────────────────────────────

emit_result() {
  local status="$1"
  local key="$2"
  local link_source="$3"
  local transition="$4"
  local commented="$5"
  local reason="$6"

  local payload
  payload="$(jq -cn \
    --arg status "${status}" \
    --arg key "${key}" \
    --arg linear "${issue_identifier}" \
    --arg link_source "${link_source}" \
    --arg transition "${transition}" \
    --arg commented "${commented}" \
    --arg reason "${reason}" '
    {
      close_linked_jira_status: $status,
      close_linked_jira_key: $key,
      close_linked_jira_linear: $linear,
      close_linked_jira_link_source: $link_source,
      close_linked_jira_transition: $transition,
      close_linked_jira_commented: $commented,
      close_linked_jira_reason: $reason
    }')"

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
          "Symphony close-linked-Jira result",
          "",
          "Status: \(.close_linked_jira_status // "unknown")",
          "Linear: \(.close_linked_jira_linear // "")",
          "Jira: \(.close_linked_jira_key // "none")",
          "Link source: \(.close_linked_jira_link_source // "none")",
          "Transition: \(.close_linked_jira_transition // "none")",
          "Commented: \(.close_linked_jira_commented // "false")",
          "Reason: \(.close_linked_jira_reason // "none")"
        ] | join("\n")
      ' <<< "${payload}"
      ;;
    env)
      jq -r 'to_entries | map("\(.key|ascii_upcase)=\(.value // "")") | .[]' <<< "${payload}"
      ;;
  esac
}

# ── Linear context ─────────────────────────────────────────────────

resolve_context_json() {
  if [[ -n "${context_json_file}" ]]; then
    cat "${context_json_file}"
    return 0
  fi

  if ! linear_api_key="$(symphony_linear_read_api_key "${linear_api_key}")"; then
    return 1
  fi

  local temp_json context_output rc
  temp_json="$(mktemp "${TMPDIR:-/tmp}/symphony-close-jira-context-XXXXXX.json")"
  local -a cmd=(bash "${CONTEXT_HELPER}" --json-output-file "${temp_json}")
  if [[ -n "${issue_identifier}" ]]; then cmd+=(--issue-identifier "${issue_identifier}"); fi
  if [[ -n "${issue_id}" ]]; then cmd+=(--issue-id "${issue_id}"); fi
  if [[ -n "${linear_api_key}" ]]; then cmd+=(--linear-api-key "${linear_api_key}"); fi
  if [[ -n "${linear_json_file}" ]]; then cmd+=(--linear-json-file "${linear_json_file}"); fi

  set +e
  context_output="$("${cmd[@]}" 2>&1)"
  rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    rm -f "${temp_json}"
    echo "${context_output}" >&2
    return 1
  fi

  cat "${temp_json}"
  rm -f "${temp_json}"
}

# ── Jira link extraction ───────────────────────────────────────────

# Pull the first Jira issue key (PROJECT-NUMBER) out of text, upper-cased.
first_jira_key() {
  grep -oiE '[A-Za-z][A-Za-z0-9]*-[0-9]+' | head -n 1 | tr '[:lower:]' '[:upper:]'
}

# Emits "<source>\t<key>". The source field is always non-empty so that the
# caller's `read` (which strips leading IFS whitespace) parses both fields even
# when the key is empty. Matching is host- and project-agnostic and requires an
# explicit Jira `/browse/<KEY>` link. The title is intentionally not used: both
# Linear creation paths (linear-to-jira sync and the linear skill) always
# attach the Jira browse URL, so the title adds no coverage but would risk
# matching non-Jira tokens like "GPT-4: ..." and closing the wrong ticket.
extract_jira_key() {
  local ctx="$1"
  local key=""

  # 1. Attachment browse URLs (normalized newest-first).
  key="$(jq -r '.attachments[]?.url // empty' <<< "${ctx}" \
    | grep -oiE 'browse/[A-Za-z][A-Za-z0-9]*-[0-9]+' \
    | first_jira_key || true)"
  if [[ -n "${key}" ]]; then
    printf '%s\t%s' "attachment" "${key}"
    return 0
  fi

  # 2. A browse/<KEY> link inside the description.
  key="$(jq -r '.issue.description // empty' <<< "${ctx}" \
    | grep -oiE 'browse/[A-Za-z][A-Za-z0-9]*-[0-9]+' \
    | first_jira_key || true)"
  if [[ -n "${key}" ]]; then
    printf '%s\t%s' "description" "${key}"
    return 0
  fi

  printf '%s\t' "none"
}

# ── Jira credentials ───────────────────────────────────────────────

load_jira_creds() {
  if [[ -z "${jira_email}" ]]; then jira_email="${JIRA_EMAIL:-}"; fi
  if [[ -z "${jira_api_key}" ]]; then jira_api_key="${JIRA_API_KEY:-}"; fi
  if [[ -z "${jira_base_url}" ]]; then jira_base_url="${JIRA_URL:-}"; fi

  if [[ ( -z "${jira_email}" || -z "${jira_api_key}" || -z "${jira_base_url}" ) && -f "${jira_creds_file}" ]]; then
    local line k v
    while IFS= read -r line || [[ -n "${line}" ]]; do
      line="${line#"${line%%[![:space:]]*}"}"
      [[ -z "${line}" || "${line}" == \#* ]] && continue
      [[ "${line}" != *=* ]] && continue
      k="${line%%=*}"
      v="${line#*=}"
      k="$(printf '%s' "${k}" | tr -d '[:space:]')"
      v="${v#"${v%%[![:space:]]*}"}"
      v="${v%"${v##*[![:space:]]}"}"
      v="${v#\"}"; v="${v%\"}"
      v="${v#\'}"; v="${v%\'}"
      case "${k}" in
        JIRA_EMAIL) [[ -z "${jira_email}" ]] && jira_email="${v}" ;;
        JIRA_API_KEY) [[ -z "${jira_api_key}" ]] && jira_api_key="${v}" ;;
        JIRA_URL) [[ -z "${jira_base_url}" ]] && jira_base_url="${v}" ;;
      esac
    done < "${jira_creds_file}"
  fi

  jira_base_url="${jira_base_url%/}"

  # Base URL is intentionally not defaulted to any organization's Jira; it must
  # come from JIRA_URL (env), the credentials file, or --jira-base-url.
  [[ -n "${jira_email}" && -n "${jira_api_key}" && -n "${jira_base_url}" ]]
}

# ── Jira transport ─────────────────────────────────────────────────

# jira_api METHOD URLPATH [BODY_JSON] -> prints response JSON, returns curl rc.
jira_api() {
  local method="$1"
  local url_path="$2"
  local body="${3:-}"

  if [[ -n "${jira_transport}" ]]; then
    "${jira_transport}" "${method}" "${url_path}" "${body}"
    return $?
  fi

  local auth
  auth="$(printf '%s:%s' "${jira_email}" "${jira_api_key}" | base64 | tr -d '\n')"

  local -a cmd=(curl -fsS -X "${method}"
    -H "Authorization: Basic ${auth}"
    -H "Accept: application/json")
  if [[ -n "${body}" ]]; then
    cmd+=(-H "Content-Type: application/json" --data "${body}")
  fi
  cmd+=("${jira_base_url}${url_path}")
  "${cmd[@]}"
}

# ── Main ───────────────────────────────────────────────────────────

context_json="$(resolve_context_json)" || {
  emit_result "error" "" "none" "" "false" "Could not resolve Linear context."
  exit 3
}

if [[ -z "${issue_identifier}" ]]; then
  issue_identifier="$(jq -r '.issue.identifier // ""' <<< "${context_json}")"
fi

IFS=$'\t' read -r link_source jira_key <<< "$(extract_jira_key "${context_json}")"

if [[ -z "${jira_key}" ]]; then
  emit_result "no_link" "" "none" "" "false" "No linked Jira ticket found on the Linear issue."
  exit 0
fi

if [[ "${resolve_only}" -eq 1 ]]; then
  emit_result "resolved" "${jira_key}" "${link_source}" "" "false" "Resolved linked Jira ticket; no Jira action requested."
  exit 0
fi

if ! load_jira_creds; then
  emit_result "no_jira_creds" "${jira_key}" "${link_source}" "" "false" \
    "Jira credentials/base URL unavailable (need JIRA_EMAIL, JIRA_API_KEY, JIRA_URL in env or ${jira_creds_file})."
  exit 0
fi

linear_url="$(jq -r '.issue.url // ""' <<< "${context_json}")"

# Current Jira status — skip when already in a Done category.
set +e
issue_json="$(jira_api GET "/rest/api/3/issue/${jira_key}?fields=status" 2>/dev/null)"
issue_rc=$?
set -e
if [[ "${issue_rc}" -ne 0 ]]; then
  emit_result "error" "${jira_key}" "${link_source}" "" "false" \
    "Could not read Jira issue ${jira_key} (HTTP error or not accessible)."
  exit 0
fi

status_category="$(jq -r '.fields.status.statusCategory.key // ""' <<< "${issue_json}")"
if [[ "${status_category}" == "done" ]]; then
  emit_result "already_done" "${jira_key}" "${link_source}" "" "skipped" \
    "Jira ${jira_key} is already in a Done status category."
  exit 0
fi

if [[ "${dry_run}" -eq 1 ]]; then
  emit_result "dry_run" "${jira_key}" "${link_source}" "" "false" \
    "Would comment on and transition Jira ${jira_key} to Done."
  exit 0
fi

# Find a transition into a Done-category status.
set +e
transitions_json="$(jira_api GET "/rest/api/3/issue/${jira_key}/transitions" 2>/dev/null)"
trans_rc=$?
set -e
if [[ "${trans_rc}" -ne 0 ]]; then
  emit_result "error" "${jira_key}" "${link_source}" "" "false" \
    "Could not read Jira transitions for ${jira_key}."
  exit 0
fi

transition_id="$(jq -r '
  [ .transitions[]?
    | select((.to.statusCategory.key // "") == "done"
             or ((.name // "") | ascii_downcase) == "done") ]
  | (.[0].id // "")
' <<< "${transitions_json}")"
transition_name="$(jq -r --arg id "${transition_id}" '
  (.transitions[]? | select(.id == $id) | .name) // ""
' <<< "${transitions_json}")"

if [[ -z "${transition_id}" ]]; then
  emit_result "error" "${jira_key}" "${link_source}" "" "false" \
    "No Done-category transition available for ${jira_key}."
  exit 0
fi

# Idempotent AI-attributed comment, keyed by a deterministic reconcile token.
token="symphony-finalize-jira-close:${jira_key}:${issue_identifier}"
commented="false"

set +e
comments_json="$(jira_api GET "/rest/api/3/issue/${jira_key}/comment?maxResults=100" 2>/dev/null)"
comments_rc=$?
set -e
token_present=0
if [[ "${comments_rc}" -eq 0 ]]; then
  if jq -e --arg token "${token}" '
    [.comments[]? | (.body | tostring) | contains($token)] | any
  ' <<< "${comments_json}" >/dev/null 2>&1; then
    token_present=1
  fi
fi

if [[ "${token_present}" -eq 1 ]]; then
  commented="skipped"
else
  para1="Closing automatically: the linked Linear issue ${issue_identifier} reached Done via Symphony finalization."
  para2="Linear: ${linear_url}"
  comment_body="$(jq -cn \
    --arg p1 "${para1}" \
    --arg p2 "${para2}" \
    --arg token "${token}" \
    --arg agent "${agent_name}" '
    {
      body: {
        type: "doc",
        version: 1,
        content: [
          {type: "paragraph", content: [{type: "text", text: $p1}]},
          {type: "paragraph", content: [{type: "text", text: $p2}]},
          {type: "paragraph", content: [{type: "text", text: $token}]},
          {type: "paragraph", content: [
            {type: "text", text: ("This comment was drafted by " + $agent + "."),
             marks: [{type: "subsup", attrs: {type: "sub"}}]}
          ]}
        ]
      }
    }')"
  set +e
  jira_api POST "/rest/api/3/issue/${jira_key}/comment" "${comment_body}" >/dev/null 2>&1
  comment_post_rc=$?
  set -e
  if [[ "${comment_post_rc}" -eq 0 ]]; then
    commented="true"
  fi
fi

# Transition the issue to Done.
transition_body="$(jq -cn --arg id "${transition_id}" '{transition: {id: $id}}')"
set +e
jira_api POST "/rest/api/3/issue/${jira_key}/transitions" "${transition_body}" >/dev/null 2>&1
transition_rc=$?
set -e
if [[ "${transition_rc}" -ne 0 ]]; then
  emit_result "error" "${jira_key}" "${link_source}" "${transition_name}" "${commented}" \
    "Comment step status=${commented}; Done transition request failed for ${jira_key}."
  exit 0
fi

emit_result "closed" "${jira_key}" "${link_source}" "${transition_name}" "${commented}" \
  "Transitioned ${jira_key} to ${transition_name}."
exit 0
