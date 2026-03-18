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
    log_warn "Value is required." >&2
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

require_directory_exists() {
  local dir_path="$1"

  if [[ ! -d "$dir_path" ]]; then
    log_error "Required directory not found: $dir_path"
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

backup_file_with_timestamp() {
  local file_path="$1"

  if [[ ! -f "$file_path" ]]; then
    return 0
  fi

  local backup_path
  backup_path="${file_path}.bak.$(date -u +%Y%m%d%H%M%S).$$"
  cp "$file_path" "$backup_path"
  log_info "Backed up $(basename "$file_path") -> $(basename "$backup_path")"
}

upsert_env_var() {
  local env_file="$1"
  local key="$2"
  local value="$3"

  require_file_exists "$env_file"

  local tmp_file
  tmp_file="$(mktemp)"

  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    $0 ~ ("^" key "=") {
      print key "=" value
      found = 1
      next
    }
    { print }
    END {
      if (!found) {
        print key "=" value
      }
    }
  ' "$env_file" >"$tmp_file"

  mv "$tmp_file" "$env_file"
}

remove_env_var() {
  local env_file="$1"
  local key="$2"

  require_file_exists "$env_file"

  local tmp_file
  tmp_file="$(mktemp)"

  awk -v key="$key" '
    $0 !~ ("^" key "=") { print }
  ' "$env_file" >"$tmp_file"

  mv "$tmp_file" "$env_file"
}

install_runtime_root_dir() {
  local install_home_dir="$1"
  printf '%s/runtime\n' "$install_home_dir"
}

install_deployment_config_filenames() {
  cat <<'EOF'
groups.yaml
connections.yaml
providers.yaml
models.yaml
tool_policy_defaults.yaml
maintenance_message.txt
EOF
}

install_shipped_package_names() {
  cat <<'EOF'
core
alliance
EOF
}

install_runtime_config_dir() {
  local install_home_dir="$1"
  printf '%s/config\n' "$(install_runtime_root_dir "$install_home_dir")"
}

install_runtime_packages_dir() {
  local install_home_dir="$1"
  printf '%s/packages\n' "$(install_runtime_root_dir "$install_home_dir")"
}

install_runtime_state_dir() {
  local install_home_dir="$1"
  printf '%s/state\n' "$(install_runtime_root_dir "$install_home_dir")"
}

install_data_root_dir() {
  local install_home_dir="$1"
  printf '%s/data\n' "$install_home_dir"
}

install_pdf_storage_dir() {
  local install_home_dir="$1"
  printf '%s/pdf_storage\n' "$(install_data_root_dir "$install_home_dir")"
}

install_file_outputs_dir() {
  local install_home_dir="$1"
  printf '%s/file_outputs\n' "$(install_data_root_dir "$install_home_dir")"
}

install_weaviate_data_dir() {
  local install_home_dir="$1"
  printf '%s/weaviate\n' "$(install_data_root_dir "$install_home_dir")"
}

has_port_probe_command() {
  local lsof_cmd="${INSTALL_LSOF_CMD:-lsof}"
  local ss_cmd="${INSTALL_SS_CMD:-ss}"

  command -v "$lsof_cmd" >/dev/null 2>&1 || command -v "$ss_cmd" >/dev/null 2>&1
}

find_listening_port_owner() {
  local port="$1"
  local lsof_cmd="${INSTALL_LSOF_CMD:-lsof}"
  local ss_cmd="${INSTALL_SS_CMD:-ss}"
  local owner=""

  if command -v "$lsof_cmd" >/dev/null 2>&1; then
    owner="$("$lsof_cmd" -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 { print $1 "/" $2; exit }')"
    if [[ -n "$owner" ]]; then
      printf '%s\n' "$owner"
      return 0
    fi
  fi

  if command -v "$ss_cmd" >/dev/null 2>&1; then
    local ss_line
    ss_line="$("$ss_cmd" -ltnp "( sport = :${port} )" 2>/dev/null | awk 'NR>1 && $1=="LISTEN" { print; exit }')"
    if [[ -n "$ss_line" ]]; then
      owner="$(printf '%s\n' "$ss_line" | sed -n 's/.*users:(("\([^"]*\)".*pid=\([0-9]*\).*/\1\/\2/p')"
      if [[ -z "$owner" ]]; then
        owner="unknown process"
      fi
      printf '%s\n' "$owner"
      return 0
    fi
  fi

  return 1
}
