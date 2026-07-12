#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
AGENT_LSP_HELPER="${SYMPHONY_AGENT_LSP_HELPER:-${REPO_ROOT}/scripts/utilities/agent_lsp.py}"

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_lsp_warm.sh [options]

Options:
  --root PATH       Workspace root to warm (default: current git root or pwd)
  --timeout VALUE   Warm timeout in seconds (default: SYMPHONY_LSP_WARM_TIMEOUT or 20)
  -h, --help        Show this help

Always emits SYMPHONY_LSP_* machine-readable lines. Warm failures are reported
as SYMPHONY_LSP_STATUS=error but do not fail the script; lane helpers should
continue to generate their briefs.
EOF
}

workspace_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd -P)"
timeout="${SYMPHONY_LSP_WARM_TIMEOUT:-20}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) workspace_root="${2:-}"; shift 2 ;;
    --timeout) timeout="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

sanitize_line() {
  tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//'
}

emit_error() {
  local reason="$1"
  local detail="${2:-}"
  echo "SYMPHONY_LSP_STATUS=error"
  echo "SYMPHONY_LSP_REASON=$(printf '%s' "${reason}" | sanitize_line)"
  echo "SYMPHONY_LSP_WORKSPACE_ROOT=${workspace_root}"
  if [[ -n "${detail}" ]]; then
    echo "SYMPHONY_LSP_ERROR=$(printf '%s' "${detail}" | sanitize_line)"
  fi
}

if [[ -z "${workspace_root}" || ! -d "${workspace_root}" ]]; then
  emit_error "workspace_root_missing" "${workspace_root}"
  exit 0
fi

if [[ ! -f "${AGENT_LSP_HELPER}" && ! -x "${AGENT_LSP_HELPER}" ]]; then
  emit_error "agent_lsp_helper_missing" "${AGENT_LSP_HELPER}"
  exit 0
fi

cmd=("${AGENT_LSP_HELPER}")
if [[ "${AGENT_LSP_HELPER}" == *.py ]]; then
  cmd=(python3 "${AGENT_LSP_HELPER}")
fi

set +e
json_output="$("${cmd[@]}" --root "${workspace_root}" --timeout "${timeout}" --format json warm 2>&1)"
rc=$?
set -e

if [[ "${rc}" -ne 0 ]]; then
  emit_error "warm_failed" "${json_output}"
  exit 0
fi

if ! printf '%s' "${json_output}" | jq -e '.status' >/dev/null 2>&1; then
  emit_error "warm_output_invalid" "${json_output}"
  exit 0
fi

status="$(printf '%s' "${json_output}" | jq -r '.status // "unknown"')"
reason="$(printf '%s' "${json_output}" | jq -r '.reason // "unknown"')"
refreshed="$(printf '%s' "${json_output}" | jq -r '.refreshed // false')"
cache_dir="$(printf '%s' "${json_output}" | jq -r '.cache_dir // ""')"
state_root="$(printf '%s' "${json_output}" | jq -r '.workspace_root // ""')"
languages="$(printf '%s' "${json_output}" | jq -r '(.languages // []) | join(",")')"
typescript_status="$(printf '%s' "${json_output}" | jq -r '.language_status.typescript.status // "not_applicable"')"
typescript_reason="$(printf '%s' "${json_output}" | jq -r '.language_status.typescript.reason // "not_applicable"')"

echo "SYMPHONY_LSP_STATUS=${status}"
echo "SYMPHONY_LSP_REASON=${reason}"
echo "SYMPHONY_LSP_REFRESHED=${refreshed}"
echo "SYMPHONY_LSP_WORKSPACE_ROOT=${state_root:-${workspace_root}}"
echo "SYMPHONY_LSP_CACHE_DIR=${cache_dir}"
echo "SYMPHONY_LSP_LANGUAGES=${languages}"
echo "SYMPHONY_LSP_TYPESCRIPT_STATUS=${typescript_status}"
echo "SYMPHONY_LSP_TYPESCRIPT_REASON=${typescript_reason}"
