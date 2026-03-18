#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

install_home_dir="${INSTALL_HOME_DIR:-${HOME}/.agr_ai_curation}"
env_template_path="${repo_root}/scripts/install/lib/templates/env.standalone"
env_output_path="${INSTALL_ENV_PATH:-${install_home_dir}/.env}"
package_profile_state_path="${INSTALL_PACKAGE_PROFILE_STATE_PATH:-${install_home_dir}/.install_package_profile.env}"
image_tag_override="${INSTALL_IMAGE_TAG:-}"
package_profile_override="${INSTALL_PACKAGE_PROFILE:-}"
resolved_image_tag=""
image_tag_note=""

mapfile -t deployment_config_filenames < <(install_deployment_config_filenames)

prompt_optional_value() {
  local prompt_text="$1"
  local response=""
  read -r -p "${prompt_text} (press Enter to skip): " response
  printf '%s\n' "$response"
}

generate_hex_secret() {
  local bytes="$1"
  openssl rand -hex "$bytes"
}

generate_prefixed_langfuse_key() {
  local prefix="$1"
  printf '%s%s\n' "$prefix" "$(generate_hex_secret 16)"
}

seed_runtime_layout() {
  local runtime_config_dir="$1"
  local runtime_packages_dir="$2"
  local runtime_state_dir="$3"
  local pdf_storage_dir="$4"
  local file_outputs_dir="$5"
  local weaviate_data_dir="$6"
  local config_source_dir="$7"

  require_directory_exists "$config_source_dir"

  mkdir -p \
    "$runtime_config_dir" \
    "$runtime_packages_dir" \
    "$runtime_state_dir" \
    "$pdf_storage_dir" \
    "$file_outputs_dir" \
    "$weaviate_data_dir"

  local filename=""
  for filename in "${deployment_config_filenames[@]}"; do
    require_file_exists "${config_source_dir}/${filename}"
    cp "${config_source_dir}/${filename}" "${runtime_config_dir}/${filename}"
    chmod 0644 "${runtime_config_dir}/${filename}"
  done

  chmod 0755 "$runtime_config_dir"

  # Published runtime containers run as non-root users, so the mutable
  # host-mounted directories need write access regardless of the installer
  # user's UID/GID on the host.
  chmod 0777 \
    "$runtime_state_dir" \
    "$pdf_storage_dir" \
    "$file_outputs_dir" \
    "$weaviate_data_dir"
}

seed_runtime_package_dir() {
  local package_source_dir="$1"
  local package_target_dir="$2"

  require_directory_exists "$package_source_dir"
  require_non_empty "package_target_dir" "$package_target_dir"

  rm -rf "$package_target_dir"
  cp -a "$package_source_dir" "$package_target_dir"
}

seed_runtime_packages() {
  local runtime_packages_dir="$1"
  local core_package_source_dir="$2"
  local alliance_package_source_dir="$3"
  local package_profile="$4"
  local core_package_target_dir="${runtime_packages_dir}/core"
  local alliance_package_target_dir="${runtime_packages_dir}/alliance"

  seed_runtime_package_dir "$core_package_source_dir" "$core_package_target_dir"

  if install_package_profile_includes_alliance "$package_profile"; then
    seed_runtime_package_dir "$alliance_package_source_dir" "$alliance_package_target_dir"
  else
    rm -rf "$alliance_package_target_dir"
  fi
}

load_existing_package_profile() {
  local existing_profile=""

  if [[ ! -f "$package_profile_state_path" ]]; then
    printf 'core-only\n'
    return 0
  fi

  existing_profile="$(awk -F= '/^INSTALL_PACKAGE_PROFILE=/{print $2; exit}' "$package_profile_state_path")"
  if existing_profile="$(normalize_install_package_profile "$existing_profile" 2>/dev/null)"; then
    printf '%s\n' "$existing_profile"
    return 0
  fi

  printf 'core-only\n'
}

prompt_package_profile() {
  local default_profile="$1"
  local response=""
  local default_choice="1"

  if [[ "$default_profile" == "core-plus-alliance" ]]; then
    default_choice="2"
  fi

  while true; do
    read -r -p "Package profile [1 = core only, 2 = core + alliance] (default ${default_choice}): " response
    response="${response:-$default_choice}"

    case "$response" in
      1)
        printf 'core-only\n'
        return 0
        ;;
      2)
        printf 'core-plus-alliance\n'
        return 0
        ;;
      *)
        log_warn "Please choose 1 (core only) or 2 (core + alliance)." >&2
        ;;
    esac
  done
}

resolve_package_profile() {
  local default_profile="$1"
  local normalized_profile=""

  if [[ -n "$package_profile_override" ]]; then
    if ! normalized_profile="$(normalize_install_package_profile "$package_profile_override")"; then
      log_error "Unsupported package profile override: ${package_profile_override}"
      return 1
    fi
    printf '%s\n' "$normalized_profile"
    return 0
  fi

  prompt_package_profile "$default_profile"
}

write_package_profile_state() {
  local package_profile="$1"
  local package_ids="agr.core"

  if install_package_profile_includes_alliance "$package_profile"; then
    package_ids="${package_ids},agr.alliance"
  fi

  cat >"$package_profile_state_path" <<STATE
INSTALL_PACKAGE_PROFILE=${package_profile}
INSTALL_PACKAGE_IDS=${package_ids}
STATE
  chmod 600 "$package_profile_state_path"
}

resolve_image_tag_defaults() {
  if [[ -n "$image_tag_override" ]]; then
    resolved_image_tag="$image_tag_override"
    image_tag_note="override"
    return 0
  fi

  resolved_image_tag="$(resolve_checkout_image_tag "$repo_root" || true)"
  if [[ -n "$resolved_image_tag" ]]; then
    image_tag_note="resolved from checkout"
    return 0
  fi

  resolved_image_tag=""
  image_tag_note="template default"
}

print_stage_intro() {
  local runtime_root_dir="$1"
  local data_root_dir="$2"

  echo
  log_info "=== Stage 2 of 6: Core Configuration ==="
  echo
  echo "  This stage creates the main environment file that holds API keys,"
  echo "  database passwords, encryption secrets, and the installed runtime layout."
  echo
  echo "  What you'll be asked:"
  echo
  echo "    1. Package profile     (default core only; optionally add the alliance package)"
  local red='\033[0;31m'
  local reset='\033[0m'
  if ! supports_color; then red="" reset=""; fi
  printf "    2. OpenAI API key      (${red}REQUIRED${reset} - used for embeddings and default models)\n"
  echo "    3. Groq API key        (optional - adds Groq as an LLM provider)"
  echo "    4. Anthropic API key   (recommended - powers the in-app Claude help agent)"
  echo "    5. Gemini API key      (optional - adds Google Gemini models)"
  echo
  echo "  Everything else (database passwords, encryption keys, Langfuse tokens)"
  echo "  is generated automatically. You don't need to prepare anything for those."
  echo "  You can re-run Stage 2 later to change the package profile and reseed bundled packages."
  echo
  echo "  Config location: ${install_home_dir}/.env"
  echo "  Runtime directory: ${runtime_root_dir}"
  echo "  Data directory: ${data_root_dir}"
  if [[ -n "$resolved_image_tag" ]]; then
    echo "  Published image tag: ${resolved_image_tag} (${image_tag_note})"
  else
    echo "  Published image tag: template defaults (${image_tag_note})"
  fi
  echo
}

main() {
  require_file_exists "$env_template_path"
  require_command "openssl"

  local runtime_root_dir
  local runtime_config_dir
  local runtime_packages_dir
  local runtime_state_dir
  local data_root_dir
  local pdf_storage_dir
  local file_outputs_dir
  local weaviate_data_dir
  local package_profile
  local package_profile_label
  local default_package_profile
  local core_package_source_dir
  local alliance_package_source_dir
  local config_source_dir

  runtime_root_dir="$(install_runtime_root_dir "$install_home_dir")"
  runtime_config_dir="$(install_runtime_config_dir "$install_home_dir")"
  runtime_packages_dir="$(install_runtime_packages_dir "$install_home_dir")"
  runtime_state_dir="$(install_runtime_state_dir "$install_home_dir")"
  data_root_dir="$(install_data_root_dir "$install_home_dir")"
  pdf_storage_dir="$(install_pdf_storage_dir "$install_home_dir")"
  file_outputs_dir="$(install_file_outputs_dir "$install_home_dir")"
  weaviate_data_dir="$(install_weaviate_data_dir "$install_home_dir")"
  core_package_source_dir="${repo_root}/packages/core"
  alliance_package_source_dir="${repo_root}/packages/alliance"
  config_source_dir="${repo_root}/config"

  mkdir -p "$install_home_dir"
  resolve_image_tag_defaults
  print_stage_intro "$runtime_root_dir" "$data_root_dir"
  default_package_profile="$(load_existing_package_profile)"
  package_profile="$(resolve_package_profile "$default_package_profile")"
  package_profile_label="$(install_package_profile_label "$package_profile")"
  seed_runtime_layout \
    "$runtime_config_dir" \
    "$runtime_packages_dir" \
    "$runtime_state_dir" \
    "$pdf_storage_dir" \
    "$file_outputs_dir" \
    "$weaviate_data_dir" \
    "$config_source_dir"
  seed_runtime_packages \
    "$runtime_packages_dir" \
    "$core_package_source_dir" \
    "$alliance_package_source_dir" \
    "$package_profile"

  if [[ -f "$env_output_path" ]]; then
    backup_file_with_timestamp "$env_output_path"
  fi

  cp "$env_template_path" "$env_output_path"
  chmod 600 "$env_output_path"

  local openai_api_key
  local groq_api_key
  local anthropic_api_key
  local gemini_api_key

  openai_api_key="$(prompt_required_value "OpenAI API key (required)")"
  groq_api_key="$(prompt_optional_value "Groq API key")"
  anthropic_api_key="$(prompt_optional_value "Anthropic API key")"
  gemini_api_key="$(prompt_optional_value "Gemini API key")"

  local postgres_password
  local redis_auth
  local nextauth_secret
  local salt
  local encryption_key
  local clickhouse_password
  local minio_root_password
  local langfuse_init_public_key
  local langfuse_init_secret_key
  local langfuse_init_user_password

  postgres_password="$(generate_hex_secret 16)"
  redis_auth="$(generate_hex_secret 16)"
  nextauth_secret="$(generate_hex_secret 32)"
  salt="$(generate_hex_secret 32)"
  encryption_key="$(generate_hex_secret 32)"
  clickhouse_password="$(generate_hex_secret 16)"
  minio_root_password="$(generate_hex_secret 16)"
  langfuse_init_public_key="$(generate_prefixed_langfuse_key "pk-lf-")"
  langfuse_init_secret_key="$(generate_prefixed_langfuse_key "sk-lf-")"
  langfuse_init_user_password="$(generate_hex_secret 16)"

  require_hex_64 "NEXTAUTH_SECRET" "$nextauth_secret"
  require_hex_64 "SALT" "$salt"
  require_hex_64 "ENCRYPTION_KEY" "$encryption_key"

  upsert_env_var "$env_output_path" "OPENAI_API_KEY" "$openai_api_key"
  upsert_env_var "$env_output_path" "GROQ_API_KEY" "$groq_api_key"
  upsert_env_var "$env_output_path" "ANTHROPIC_API_KEY" "$anthropic_api_key"
  upsert_env_var "$env_output_path" "GEMINI_API_KEY" "$gemini_api_key"

  upsert_env_var "$env_output_path" "POSTGRES_PASSWORD" "$postgres_password"
  upsert_env_var "$env_output_path" "REDIS_AUTH" "$redis_auth"
  upsert_env_var "$env_output_path" "NEXTAUTH_SECRET" "$nextauth_secret"
  upsert_env_var "$env_output_path" "SALT" "$salt"
  upsert_env_var "$env_output_path" "ENCRYPTION_KEY" "$encryption_key"
  upsert_env_var "$env_output_path" "LANGFUSE_LOCAL_NEXTAUTH_SECRET" "$nextauth_secret"
  upsert_env_var "$env_output_path" "LANGFUSE_LOCAL_SALT" "$salt"
  upsert_env_var "$env_output_path" "LANGFUSE_LOCAL_ENCRYPTION_KEY" "$encryption_key"
  upsert_env_var "$env_output_path" "CLICKHOUSE_PASSWORD" "$clickhouse_password"
  upsert_env_var "$env_output_path" "MINIO_ROOT_PASSWORD" "$minio_root_password"

  upsert_env_var "$env_output_path" "LANGFUSE_INIT_PROJECT_PUBLIC_KEY" "$langfuse_init_public_key"
  upsert_env_var "$env_output_path" "LANGFUSE_INIT_PROJECT_SECRET_KEY" "$langfuse_init_secret_key"
  upsert_env_var "$env_output_path" "LANGFUSE_INIT_USER_PASSWORD" "$langfuse_init_user_password"

  upsert_env_var "$env_output_path" "LANGFUSE_PUBLIC_KEY" "$langfuse_init_public_key"
  upsert_env_var "$env_output_path" "LANGFUSE_SECRET_KEY" "$langfuse_init_secret_key"
  upsert_env_var "$env_output_path" "LANGFUSE_LOCAL_PUBLIC_KEY" "$langfuse_init_public_key"
  upsert_env_var "$env_output_path" "LANGFUSE_LOCAL_SECRET_KEY" "$langfuse_init_secret_key"

  upsert_env_var "$env_output_path" "LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY" '${MINIO_ROOT_PASSWORD}'
  upsert_env_var "$env_output_path" "LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY" '${MINIO_ROOT_PASSWORD}'
  upsert_env_var "$env_output_path" "LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY" '${MINIO_ROOT_PASSWORD}'
  upsert_env_var "$env_output_path" "LLM_PROVIDER_STRICT_MODE" "false"

  # Ensure compose interpolation keeps working if .env values are consumed directly.
  upsert_env_var "$env_output_path" "DATABASE_URL" 'postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/ai_curation'
  upsert_env_var "$env_output_path" "LANGFUSE_DATABASE_URL" 'postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/postgres'
  upsert_env_var "$env_output_path" "LANGFUSE_LOCAL_DATABASE_URL" 'postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/postgres'
  upsert_env_var "$env_output_path" "AGR_RUNTIME_CONFIG_HOST_DIR" "$runtime_config_dir"
  upsert_env_var "$env_output_path" "AGR_REPO_CONFIG_HOST_DIR" "$config_source_dir"
  upsert_env_var "$env_output_path" "AGR_RUNTIME_PACKAGES_HOST_DIR" "$runtime_packages_dir"
  upsert_env_var "$env_output_path" "AGR_RUNTIME_STATE_HOST_DIR" "$runtime_state_dir"
  upsert_env_var "$env_output_path" "PDF_STORAGE_HOST_DIR" "$pdf_storage_dir"
  upsert_env_var "$env_output_path" "FILE_OUTPUT_STORAGE_HOST_DIR" "$file_outputs_dir"
  upsert_env_var "$env_output_path" "WEAVIATE_DATA_HOST_DIR" "$weaviate_data_dir"

  if [[ -n "$resolved_image_tag" ]]; then
    upsert_env_var "$env_output_path" "BACKEND_IMAGE_TAG" "$resolved_image_tag"
    upsert_env_var "$env_output_path" "FRONTEND_IMAGE_TAG" "$resolved_image_tag"
    upsert_env_var "$env_output_path" "TRACE_REVIEW_BACKEND_IMAGE_TAG" "$resolved_image_tag"
  fi

  chmod 600 "$env_output_path"
  write_package_profile_state "$package_profile"
  log_success "Generated core config at ${env_output_path}"
  log_success "Seeded runtime config into ${runtime_config_dir}"
  log_success "Selected package profile: ${package_profile_label}"
  log_success "Package profile state saved to ${package_profile_state_path}"
  log_success "Seeded bundled core package into ${runtime_packages_dir}/core"
  if install_package_profile_includes_alliance "$package_profile"; then
    log_success "Seeded bundled alliance package into ${runtime_packages_dir}/alliance"
  else
    log_info "Alliance package not selected; runtime packages remain core only"
  fi
}

main "$@"
