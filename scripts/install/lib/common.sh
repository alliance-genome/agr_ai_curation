#!/usr/bin/env bash
set -euo pipefail

# Guard against accidental double-source in installer stages.
if [[ "${INSTALL_COMMON_SH_LOADED:-0}" == "1" ]]; then
  return 0 2>/dev/null || exit 0
fi
INSTALL_COMMON_SH_LOADED=1

COLOR_RED='\033[0;31m'
COLOR_GREEN='\033[0;32m'
COLOR_YELLOW='\033[1;33m'
COLOR_BLUE='\033[0;34m'
COLOR_RESET='\033[0m'

supports_color() {
  [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]
}

_colorize() {
  local color="$1"
  local text="$2"

  if supports_color; then
    printf '%b%s%b\n' "$color" "$text" "$COLOR_RESET"
  else
    printf '%s\n' "$text"
  fi
}

log_info() {
  _colorize "$COLOR_BLUE" "[INFO] $*"
}

log_success() {
  _colorize "$COLOR_GREEN" "[OK] $*"
}

log_warn() {
  _colorize "$COLOR_YELLOW" "[WARN] $*"
}

log_error() {
  _colorize "$COLOR_RED" "[ERROR] $*" >&2
}

prompt_yes_no() {
  local question="$1"
  local default_choice="${2:-yes}"
  local default_hint="[Y/n]"
  local response=""

  if [[ "$default_choice" == "no" ]]; then
    default_hint="[y/N]"
  fi

  read -r -p "${question} ${default_hint} " response
  response="${response,,}"

  if [[ -z "$response" ]]; then
    response="$default_choice"
  fi

  [[ "$response" == "y" || "$response" == "yes" ]]
}

prompt_required_value() {
  local prompt_text="$1"
  local response=""

  while true; do
    read -r -p "${prompt_text}: " response
    if [[ -n "$response" ]]; then
      printf '%s\n' "$response"
      return 0
    fi
    log_warn "Value is required."
  done
}

require_non_empty() {
  local label="$1"
  local value="$2"

  if [[ -z "$value" ]]; then
    log_error "${label} must not be empty."
    return 1
  fi
  return 0
}

require_file_exists() {
  local file_path="$1"

  if [[ ! -f "$file_path" ]]; then
    log_error "Required file not found: $file_path"
    return 1
  fi
  return 0
}

require_command() {
  local command_name="$1"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    log_error "Required command not available: $command_name"
    return 1
  fi
  return 0
}

require_hex_64() {
  local label="$1"
  local value="$2"

  if [[ ! "$value" =~ ^[0-9a-fA-F]{64}$ ]]; then
    log_error "${label} must be a 64-character hex string."
    return 1
  fi
  return 0
}
