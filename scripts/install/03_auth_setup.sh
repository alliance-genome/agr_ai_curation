#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

install_home_dir="${INSTALL_HOME_DIR:-${HOME}/.agr_ai_curation}"
env_output_path="${INSTALL_ENV_PATH:-${install_home_dir}/.env}"
auth_state_path="${INSTALL_AUTH_STATE_PATH:-${install_home_dir}/.install_auth.env}"

prompt_auth_mode() {
  local response=""

  while true; do
    read -r -p "Authentication mode [1=dev, 2=oidc] (default 1): " response
    response="${response:-1}"
    case "$response" in
      1)
        printf 'dev\n'
        return 0
        ;;
      2)
        printf 'oidc\n'
        return 0
        ;;
      *)
        log_warn "Please choose 1 (dev) or 2 (oidc)." >&2
        ;;
    esac
  done
}

write_auth_state() {
  local auth_type="$1"
  local group_claim="$2"

  cat >"$auth_state_path" <<STATE
INSTALL_AUTH_TYPE=${auth_type}
INSTALL_GROUP_CLAIM=${group_claim}
STATE
  chmod 600 "$auth_state_path"
}

main() {
  require_file_exists "$env_output_path"

  backup_file_with_timestamp "$env_output_path"

  echo
  log_info "=== Stage 3 of 6: Authentication Setup ==="
  echo
  echo "  Choose how users will log in to AI Curation."
  echo
  echo "    Option 1: Dev mode (default)"
  echo "      No login required. Everyone shares a single \"Dev User\" identity."
  echo "      Publications, work items, and agent sessions are NOT isolated --"
  echo "      all activity is visible to all users. Good for quick testing, but"
  echo "      you need OIDC for proper per-user isolation."
  echo
  echo "    Option 2: OIDC (OpenID Connect)"
  echo "      Production-grade single sign-on. Works with Keycloak, Auth0, Okta,"
  echo "      or any OIDC-compliant provider. You'll need your provider's issuer"
  echo "      URL, client ID, client secret, and redirect URI."
  echo

  local auth_mode
  auth_mode="$(prompt_auth_mode)"

  local group_claim="groups"

  if [[ "$auth_mode" == "dev" ]]; then
    upsert_env_var "$env_output_path" "DEV_MODE" "true"
    upsert_env_var "$env_output_path" "AUTH_PROVIDER" "dev"
    upsert_env_var "$env_output_path" "OIDC_ISSUER_URL" ""
    upsert_env_var "$env_output_path" "OIDC_CLIENT_ID" ""
    upsert_env_var "$env_output_path" "OIDC_CLIENT_SECRET" ""
    upsert_env_var "$env_output_path" "OIDC_REDIRECT_URI" ""
    upsert_env_var "$env_output_path" "OIDC_GROUP_CLAIM" "$group_claim"
  else
    local issuer_url
    local client_id
    local client_secret
    local redirect_uri
    local claim_input

    log_info "OIDC issuer examples:"
    log_info "  - Keycloak: https://auth.example.org/realms/<realm>"
    log_info "  - Auth0: https://<tenant>.us.auth0.com"
    log_info "  - Okta: https://<tenant>.okta.com/oauth2/default"

    issuer_url="$(prompt_required_value "OIDC issuer URL")"
    client_id="$(prompt_required_value "OIDC client ID")"
    client_secret="$(prompt_required_value "OIDC client secret")"
    redirect_uri="$(prompt_required_value "OIDC redirect URI")"

    read -r -p "OIDC group claim path (default groups): " claim_input
    if [[ -n "$claim_input" ]]; then
      group_claim="$claim_input"
    fi

    upsert_env_var "$env_output_path" "DEV_MODE" "false"
    upsert_env_var "$env_output_path" "AUTH_PROVIDER" "oidc"
    upsert_env_var "$env_output_path" "OIDC_ISSUER_URL" "$issuer_url"
    upsert_env_var "$env_output_path" "OIDC_CLIENT_ID" "$client_id"
    upsert_env_var "$env_output_path" "OIDC_CLIENT_SECRET" "$client_secret"
    upsert_env_var "$env_output_path" "OIDC_REDIRECT_URI" "$redirect_uri"
    upsert_env_var "$env_output_path" "OIDC_GROUP_CLAIM" "$group_claim"
  fi

  chmod 600 "$env_output_path"
  write_auth_state "$auth_mode" "$group_claim"

  log_success "Authentication configuration saved to ${env_output_path}"
  log_success "Auth state saved to ${auth_state_path}"
}

main "$@"
