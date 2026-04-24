#!/usr/bin/env bash

# Source this file from ~/.bash_aliases to get the Symphony VM Codex helpers.
# User-local overrides can live in:
#   ~/.agr_ai_curation/shell/codex_shortcuts.env

if [[ -n "${SYMPHONY_VM_SHELL_SHORTCUTS_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
SYMPHONY_VM_SHELL_SHORTCUTS_LOADED=1

_symphony_codex_shortcuts_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
_symphony_codex_shortcuts_repo_root_default="$(cd "${_symphony_codex_shortcuts_script_dir}/../.." && pwd -P)"
_symphony_codex_shortcuts_override_file="${SYMPHONY_CODEX_SHELL_ENV_FILE:-${HOME}/.agr_ai_curation/shell/codex_shortcuts.env}"

if [[ -f "${_symphony_codex_shortcuts_override_file}" ]]; then
  # shellcheck disable=SC1090
  source "${_symphony_codex_shortcuts_override_file}"
fi

: "${SYMPHONY_CODEX_DEFAULT_MODEL:=gpt-5.5}"
: "${SYMPHONY_CODEX_DEFAULT_REASONING_HIGH:=high}"
: "${SYMPHONY_CODEX_DEFAULT_REASONING_XHIGH:=xhigh}"
: "${SYMPHONY_CODEX_USE_YOLO:=1}"
: "${SYMPHONY_CODEX_USE_NO_ALT_SCREEN:=0}"
: "${SYMPHONY_CODEX_MAIN_SANDBOX_DIR:=${HOME}/.symphony/sandboxes/agr_ai_curation/main}"

_symphony_codex_repo_root() {
  if [[ -n "${AGR_AI_CURATION_REPO_ROOT:-}" ]]; then
    printf '%s\n' "${AGR_AI_CURATION_REPO_ROOT}"
    return 0
  fi

  printf '%s\n' "${_symphony_codex_shortcuts_repo_root_default}"
}

_symphony_codex_launcher() {
  local repo_root=""

  repo_root="$(_symphony_codex_repo_root)"
  printf '%s/scripts/utilities/codex_with_repo_pat.sh\n' "${repo_root}"
}

_symphony_codex_run() {
  local reasoning="$1"
  shift

  local target_dir="${PWD}"
  local launcher=""
  local -a cmd=()

  if [[ "${1:-}" == "--main-sandbox" ]]; then
    target_dir="${SYMPHONY_CODEX_MAIN_SANDBOX_DIR}"
    shift
  fi

  if [[ ! -d "${target_dir}" ]]; then
    echo "Codex target directory is missing: ${target_dir}" >&2
    return 1
  fi

  launcher="$(_symphony_codex_launcher)"
  if [[ ! -x "${launcher}" ]]; then
    echo "Missing Codex launcher: ${launcher}" >&2
    return 1
  fi

  cmd=(
    "${launcher}"
    -C "${target_dir}"
    -m "${SYMPHONY_CODEX_DEFAULT_MODEL}"
    -c "model_reasoning_effort=\"${reasoning}\""
  )

  if [[ "${SYMPHONY_CODEX_USE_YOLO}" == "1" ]]; then
    cmd+=(--yolo)
  fi

  if [[ "${SYMPHONY_CODEX_USE_NO_ALT_SCREEN}" == "1" ]]; then
    cmd+=(--no-alt-screen)
  fi

  cmd+=("$@")
  "${cmd[@]}"
}

symphony_codex_high() {
  _symphony_codex_run "${SYMPHONY_CODEX_DEFAULT_REASONING_HIGH}" "$@"
}

symphony_codex_xhigh() {
  _symphony_codex_run "${SYMPHONY_CODEX_DEFAULT_REASONING_XHIGH}" "$@"
}

co() {
  symphony_codex_xhigh "$@"
}

CO() {
  symphony_codex_xhigh "$@"
}

comain() {
  symphony_codex_xhigh --main-sandbox "$@"
}

COMAIN() {
  symphony_codex_xhigh --main-sandbox "$@"
}

cor() {
  local launcher=""

  launcher="$(_symphony_codex_launcher)"
  if [[ ! -x "${launcher}" ]]; then
    echo "Missing Codex launcher: ${launcher}" >&2
    return 1
  fi

  "${launcher}" resume "$@"
}

COR() {
  cor "$@"
}

if [[ $- == *i* ]]; then
  alias codex-high='symphony_codex_high'
  alias codex-xhigh='symphony_codex_xhigh'
fi
