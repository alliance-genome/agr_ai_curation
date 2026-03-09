#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

install_home_dir="${INSTALL_HOME_DIR:-${HOME}/.agr_ai_curation}"
auth_state_path="${INSTALL_AUTH_STATE_PATH:-${install_home_dir}/.install_auth.env}"
groups_template_path="${repo_root}/scripts/install/lib/templates/groups.standalone.yaml"
groups_output_path="${INSTALL_GROUPS_OUTPUT_PATH:-${repo_root}/config/groups.yaml}"

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s\n' "$value"
}

escape_sed_replacement() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//&/\\&}"
  value="${value//|/\\|}"
  printf '%s\n' "$value"
}

yaml_escape_double_quoted() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s\n' "$value"
}

validate_group_id() {
  local group_id="$1"
  if [[ ! "$group_id" =~ ^[A-Za-z0-9_]+$ ]]; then
    log_error "Group ID must match [A-Za-z0-9_]+"
    return 1
  fi
}

prompt_group_mode() {
  local response=""

  while true; do
    read -r -p "Group setup [1=all Alliance groups, 2=single Alliance group, 3=custom group] (default 1): " response
    response="${response:-1}"
    case "$response" in
      1|2|3)
        printf '%s\n' "$response"
        return 0
        ;;
      *)
        log_warn "Please choose 1, 2, or 3." >&2
        ;;
    esac
  done
}

list_template_group_ids() {
  awk '
    /^groups:/ { in_groups = 1; next }
    in_groups && /^  [A-Za-z0-9_]+:/ {
      key = $1
      sub(/:$/, "", key)
      print key
    }
  ' "$groups_template_path"
}

extract_template_group_block() {
  local target_group_id="$1"

  awk -v target="$target_group_id" '
    /^groups:/ { in_groups = 1; next }
    !in_groups { next }

    /^  [A-Za-z0-9_]+:/ {
      key = $1
      sub(/:$/, "", key)

      if (capture && key != target) {
        exit
      }

      if (key == target) {
        capture = 1
      }
    }

    capture { print }
  ' "$groups_template_path"
}

write_identity_provider_header() {
  local file_path="$1"
  local provider_type="$2"
  local group_claim="$3"

  cat >"$file_path" <<HEADER
identity_provider:
  type: "${provider_type}"
  group_claim: "${group_claim}"

HEADER
}

append_custom_group_block() {
  local file_path="$1"
  local group_id="$2"
  local display_name="$3"
  local description="$4"
  local species="$5"
  local taxon="$6"
  local provider_groups_csv="$7"
  local escaped_display_name
  local escaped_description
  local escaped_species
  local escaped_taxon

  escaped_display_name="$(yaml_escape_double_quoted "$display_name")"
  escaped_description="$(yaml_escape_double_quoted "$description")"
  escaped_species="$(yaml_escape_double_quoted "$species")"
  escaped_taxon="$(yaml_escape_double_quoted "$taxon")"

  printf 'groups:\n' >>"$file_path"
  printf '  %s:\n' "$group_id" >>"$file_path"
  printf '    name: "%s"\n' "$escaped_display_name" >>"$file_path"
  if [[ -n "$description" ]]; then
    printf '    description: "%s"\n' "$escaped_description" >>"$file_path"
  fi
  if [[ -n "$species" ]]; then
    printf '    species: "%s"\n' "$escaped_species" >>"$file_path"
  fi
  if [[ -n "$taxon" ]]; then
    printf '    taxon: "%s"\n' "$escaped_taxon" >>"$file_path"
  fi
  printf '    provider_groups:\n' >>"$file_path"

  local token
  local rendered_any=0
  local escaped_token
  IFS=',' read -r -a tokens <<<"$provider_groups_csv"
  for token in "${tokens[@]}"; do
    token="$(trim "$token")"
    if [[ -n "$token" ]]; then
      rendered_any=1
      escaped_token="$(yaml_escape_double_quoted "$token")"
      printf '      - "%s"\n' "$escaped_token" >>"$file_path"
    fi
  done

  if (( rendered_any == 0 )); then
    log_error "At least one provider group name is required for custom group setup."
    return 1
  fi
}

main() {
  require_file_exists "$groups_template_path"
  require_file_exists "$auth_state_path"

  # shellcheck source=/dev/null
  source "$auth_state_path"

  local auth_type="${INSTALL_AUTH_TYPE:-}"
  local group_claim="${INSTALL_GROUP_CLAIM:-}"

  if [[ -z "$auth_type" ]]; then
    log_error "Auth state is missing INSTALL_AUTH_TYPE. Run Stage 3 first."
    exit 1
  fi

  if [[ "$auth_type" == "dev" ]]; then
    group_claim="groups"
  elif [[ -z "$group_claim" ]]; then
    log_error "Auth state is missing INSTALL_GROUP_CLAIM for OIDC mode. Run Stage 3 first."
    exit 1
  fi

  echo
  log_info "=== Stage 4: Group Mapping ==="
  echo
  echo "  Groups control how users are organized and how the AI agents behave."
  echo "  Each group can define a species, taxon, and organization-specific rules"
  echo "  that customize agent prompts for curators in that group."
  echo
  echo "  The Alliance uses groups for its Model Organism Databases (MODs), but"
  echo "  groups can represent any organization, team, or community."
  echo
  echo "    Option 1: All Alliance groups (default)"
  echo "      Installs the standard Alliance groups (MGI, ZFIN, FlyBase, WormBase,"
  echo "      SGD, RGD, XenBase, and others). Choose this for Alliance deployments."
  echo
  echo "    Option 2: Single Alliance group"
  echo "      Pick just one Alliance group. Good if your instance serves a single"
  echo "      community within the Alliance."
  echo
  echo "    Option 3: Custom group"
  echo "      Define your own group with a custom name, species, taxon ID, and"
  echo "      identity provider group names. Use this for non-Alliance organizations."
  echo "      Species and taxon are optional -- if omitted, the group still works"
  echo "      but organism-scoped queries (gene/allele lookup by taxon) won't apply."
  echo

  mkdir -p "$(dirname "$groups_output_path")"
  if [[ -f "$groups_output_path" ]]; then
    backup_file_with_timestamp "$groups_output_path"
  fi

  local mode
  mode="$(prompt_group_mode)"

  local tmp_output
  tmp_output="$(mktemp)"

  case "$mode" in
    1)
      local escaped_auth_type
      local escaped_group_claim
      escaped_auth_type="$(escape_sed_replacement "$auth_type")"
      escaped_group_claim="$(escape_sed_replacement "$group_claim")"
      sed -e "s|__AUTH_TYPE__|${escaped_auth_type}|g" \
          -e "s|__GROUP_CLAIM__|${escaped_group_claim}|g" \
          "$groups_template_path" >"$tmp_output"
      ;;
    2)
      local available_ids
      available_ids="$(list_template_group_ids | tr '\n' ' ')"
      log_info "Available Alliance group IDs: ${available_ids}"

      local selected_id
      selected_id="$(prompt_required_value "Select Alliance group ID")"

      local selected_block
      selected_block="$(extract_template_group_block "$selected_id")"
      if [[ -z "$selected_block" ]]; then
        log_error "Group ID '${selected_id}' was not found in template."
        rm -f "$tmp_output"
        exit 1
      fi

      write_identity_provider_header "$tmp_output" "$auth_type" "$group_claim"
      printf 'groups:\n' >>"$tmp_output"
      printf '%s\n' "$selected_block" >>"$tmp_output"
      ;;
    3)
      local custom_group_id
      local custom_name
      local custom_description
      local custom_species
      local custom_taxon
      local custom_provider_groups

      custom_group_id="$(prompt_required_value "Custom group ID (example: MYORG)")"
      validate_group_id "$custom_group_id"
      custom_name="$(prompt_required_value "Display name")"
      read -r -p "Description (press Enter to skip): " custom_description
      read -r -p "Species (press Enter to skip): " custom_species
      read -r -p "Taxon ID (press Enter to skip): " custom_taxon
      custom_provider_groups="$(prompt_required_value "Provider group names (comma-separated)")"

      write_identity_provider_header "$tmp_output" "$auth_type" "$group_claim"
      append_custom_group_block \
        "$tmp_output" \
        "$custom_group_id" \
        "$custom_name" \
        "$custom_description" \
        "$custom_species" \
        "$custom_taxon" \
        "$custom_provider_groups"
      ;;
  esac

  mv "$tmp_output" "$groups_output_path"
  log_success "Generated group config at ${groups_output_path}"
}

main "$@"
