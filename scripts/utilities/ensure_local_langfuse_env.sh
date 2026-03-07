#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

env_file="${1:-${AGR_AI_CURATION_ENV_FILE:-${HOME}/.agr_ai_curation/.env}}"
canonical_langfuse_database_url='postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/postgres'
backed_up=0
changed=0

generate_hex_secret() {
  local bytes="$1"
  openssl rand -hex "$bytes"
}

generate_prefixed_langfuse_key() {
  local prefix="$1"
  printf '%s%s\n' "$prefix" "$(generate_hex_secret 16)"
}

get_env_var() {
  local key="$1"

  awk -v key="$key" '
    index($0, key "=") == 1 {
      print substr($0, length(key) + 2)
      exit
    }
  ' "$env_file"
}

ensure_backup_once() {
  if (( backed_up == 0 )); then
    backup_file_with_timestamp "$env_file"
    backed_up=1
  fi
}

set_env_var() {
  local key="$1"
  local value="$2"
  local reason="$3"

  ensure_backup_once
  upsert_env_var "$env_file" "$key" "$value"
  changed=1
  log_info "${reason}"
}

is_hex_64() {
  [[ "${1:-}" =~ ^[0-9a-fA-F]{64}$ ]]
}

is_langfuse_public_key() {
  [[ "${1:-}" =~ ^pk-lf-[0-9a-fA-F]{32}$ ]]
}

is_langfuse_secret_key() {
  [[ "${1:-}" =~ ^sk-lf-[0-9a-fA-F]{32}$ ]]
}

is_placeholder_value() {
  local value="${1:-}"

  [[ -z "$value" ]] || [[ "$value" == CHANGE_ME* ]] || [[ "$value" == your_* ]] || [[ "$value" == "\${"* ]]
}

ensure_hex_secret() {
  local key="$1"
  local current_value
  current_value="$(get_env_var "$key")"

  if ! is_hex_64 "$current_value"; then
    set_env_var "$key" "$(generate_hex_secret 32)" "Normalized ${key} to a valid 64-character hex secret."
  fi
}

ensure_local_hex_secret() {
  local local_key="$1"
  local legacy_key="$2"
  local current_value
  local legacy_value

  current_value="$(get_env_var "$local_key")"
  if is_hex_64 "$current_value"; then
    return 0
  fi

  legacy_value="$(get_env_var "$legacy_key")"
  if is_hex_64 "$legacy_value"; then
    set_env_var "$local_key" "$legacy_value" "Aligned ${local_key} with existing ${legacy_key}."
    return 0
  fi

  set_env_var "$local_key" "$(generate_hex_secret 32)" "Generated ${local_key} for the local Langfuse stack."
}

ensure_langfuse_runtime_keys() {
  local public_key
  local secret_key
  local current_runtime_public
  local current_runtime_secret
  local current_local_public
  local current_local_secret

  public_key="$(get_env_var "LANGFUSE_INIT_PROJECT_PUBLIC_KEY")"
  current_runtime_public="$(get_env_var "LANGFUSE_PUBLIC_KEY")"
  current_local_public="$(get_env_var "LANGFUSE_LOCAL_PUBLIC_KEY")"
  if ! is_langfuse_public_key "$public_key"; then
    if is_langfuse_public_key "$current_local_public"; then
      public_key="$current_local_public"
    elif is_langfuse_public_key "$current_runtime_public"; then
      public_key="$current_runtime_public"
    else
      public_key="$(generate_prefixed_langfuse_key "pk-lf-")"
    fi
    set_env_var "LANGFUSE_INIT_PROJECT_PUBLIC_KEY" "$public_key" "Normalized LANGFUSE_INIT_PROJECT_PUBLIC_KEY to a valid local Langfuse project key."
  fi

  secret_key="$(get_env_var "LANGFUSE_INIT_PROJECT_SECRET_KEY")"
  current_runtime_secret="$(get_env_var "LANGFUSE_SECRET_KEY")"
  current_local_secret="$(get_env_var "LANGFUSE_LOCAL_SECRET_KEY")"
  if ! is_langfuse_secret_key "$secret_key"; then
    if is_langfuse_secret_key "$current_local_secret"; then
      secret_key="$current_local_secret"
    elif is_langfuse_secret_key "$current_runtime_secret"; then
      secret_key="$current_runtime_secret"
    else
      secret_key="$(generate_prefixed_langfuse_key "sk-lf-")"
    fi
    set_env_var "LANGFUSE_INIT_PROJECT_SECRET_KEY" "$secret_key" "Normalized LANGFUSE_INIT_PROJECT_SECRET_KEY to a valid local Langfuse project key."
  fi

  if [[ "$current_runtime_public" != "$public_key" ]]; then
    set_env_var "LANGFUSE_PUBLIC_KEY" "$public_key" "Aligned LANGFUSE_PUBLIC_KEY with LANGFUSE_INIT_PROJECT_PUBLIC_KEY."
  fi

  if [[ "$current_local_public" != "$public_key" ]]; then
    set_env_var "LANGFUSE_LOCAL_PUBLIC_KEY" "$public_key" "Aligned LANGFUSE_LOCAL_PUBLIC_KEY with LANGFUSE_INIT_PROJECT_PUBLIC_KEY."
  fi

  if [[ "$current_runtime_secret" != "$secret_key" ]]; then
    set_env_var "LANGFUSE_SECRET_KEY" "$secret_key" "Aligned LANGFUSE_SECRET_KEY with LANGFUSE_INIT_PROJECT_SECRET_KEY."
  fi

  if [[ "$current_local_secret" != "$secret_key" ]]; then
    set_env_var "LANGFUSE_LOCAL_SECRET_KEY" "$secret_key" "Aligned LANGFUSE_LOCAL_SECRET_KEY with LANGFUSE_INIT_PROJECT_SECRET_KEY."
  fi

  local init_user_password
  init_user_password="$(get_env_var "LANGFUSE_INIT_USER_PASSWORD")"
  if is_placeholder_value "$init_user_password"; then
    set_env_var "LANGFUSE_INIT_USER_PASSWORD" "$(generate_hex_secret 16)" "Generated LANGFUSE_INIT_USER_PASSWORD for local Langfuse bootstrap."
  fi
}

ensure_langfuse_host() {
  local current_host
  current_host="$(get_env_var "LANGFUSE_HOST")"

  if [[ -z "$current_host" || "$current_host" == "http://your-langfuse-host:3000" ]]; then
    set_env_var "LANGFUSE_HOST" "http://localhost:3000" "Normalized LANGFUSE_HOST to the local Langfuse endpoint."
  fi
}

ensure_langfuse_database_url() {
  local current_value
  local current_local_value
  current_value="$(get_env_var "LANGFUSE_DATABASE_URL")"
  current_local_value="$(get_env_var "LANGFUSE_LOCAL_DATABASE_URL")"

  if [[ -z "$current_value" || "$current_value" == *"@langfuse-db:"* || "$current_value" == *"@langfuse-db/"* ]]; then
    set_env_var "LANGFUSE_DATABASE_URL" "$canonical_langfuse_database_url" "Normalized LANGFUSE_DATABASE_URL to use the compose postgres service."
  fi

  if [[ -z "$current_local_value" || "$current_local_value" == *"@langfuse-db:"* || "$current_local_value" == *"@langfuse-db/"* ]]; then
    set_env_var "LANGFUSE_LOCAL_DATABASE_URL" "$canonical_langfuse_database_url" "Normalized LANGFUSE_LOCAL_DATABASE_URL to use the compose postgres service."
  fi
}

main() {
  require_command "openssl"
  require_file_exists "$env_file"

  ensure_hex_secret "NEXTAUTH_SECRET"
  ensure_hex_secret "SALT"
  ensure_hex_secret "ENCRYPTION_KEY"
  ensure_local_hex_secret "LANGFUSE_LOCAL_NEXTAUTH_SECRET" "NEXTAUTH_SECRET"
  ensure_local_hex_secret "LANGFUSE_LOCAL_SALT" "SALT"
  ensure_local_hex_secret "LANGFUSE_LOCAL_ENCRYPTION_KEY" "ENCRYPTION_KEY"
  ensure_langfuse_runtime_keys
  ensure_langfuse_host
  ensure_langfuse_database_url

  if (( changed == 0 )); then
    log_success "Local Langfuse env is already consistent: ${env_file}"
    return 0
  fi

  chmod 600 "$env_file"
  log_success "Repaired local Langfuse env: ${env_file}"
}

main "$@"
