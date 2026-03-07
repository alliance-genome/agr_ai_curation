#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
core_config_script="${repo_root}/scripts/install/02_core_config.sh"
auth_setup_script="${repo_root}/scripts/install/03_auth_setup.sh"
group_setup_script="${repo_root}/scripts/install/04_group_setup.sh"
orchestrator_script="${repo_root}/scripts/install/install.sh"

export NO_COLOR=1

assert_contains() {
  local needle="$1"
  local file_path="$2"
  if ! grep -q "$needle" "$file_path"; then
    echo "Expected to find '$needle' in $file_path" >&2
    cat "$file_path" >&2
    exit 1
  fi
}

assert_not_contains() {
  local needle="$1"
  local file_path="$2"
  if grep -q "$needle" "$file_path"; then
    echo "Did not expect to find '$needle' in $file_path" >&2
    cat "$file_path" >&2
    exit 1
  fi
}

assert_regex() {
  local regex="$1"
  local file_path="$2"
  if ! grep -Eq "$regex" "$file_path"; then
    echo "Expected regex '$regex' in $file_path" >&2
    cat "$file_path" >&2
    exit 1
  fi
}

assert_glob_exists() {
  local glob_pattern="$1"
  if ! compgen -G "$glob_pattern" >/dev/null; then
    echo "Expected at least one file matching: $glob_pattern" >&2
    exit 1
  fi
}

run_core_config() {
  local home_dir="$1"
  local input_text="$2"

  HOME="$home_dir" bash "$core_config_script" <<<"$input_text"
}

run_auth_setup() {
  local home_dir="$1"
  local input_text="$2"

  HOME="$home_dir" bash "$auth_setup_script" <<<"$input_text"
}

run_group_setup() {
  local home_dir="$1"
  local groups_output_path="$2"
  local input_text="$3"

  HOME="$home_dir" INSTALL_GROUPS_OUTPUT_PATH="$groups_output_path" bash "$group_setup_script" <<<"$input_text"
}

run_group_setup_expect_fail() {
  local home_dir="$1"
  local groups_output_path="$2"
  local input_text="$3"
  local output_path="$4"
  local rc=0

  set +e
  HOME="$home_dir" INSTALL_GROUPS_OUTPUT_PATH="$groups_output_path" bash "$group_setup_script" <<<"$input_text" >"$output_path" 2>&1
  rc=$?
  set -e

  if [[ "$rc" -eq 0 ]]; then
    echo "Expected group setup to fail but it succeeded" >&2
    cat "$output_path" >&2
    exit 1
  fi
}

test_core_config_generates_env_and_backups() {
  local temp_home
  temp_home="$(mktemp -d)"
  trap 'rm -rf "$temp_home"' RETURN

  local env_file="${temp_home}/.agr_ai_curation/.env"

  run_core_config "$temp_home" $'sk-openai-first\n\n\n\n'

  [[ -f "$env_file" ]] || {
    echo "Expected env file not found: $env_file" >&2
    exit 1
  }

  assert_contains '^OPENAI_API_KEY=sk-openai-first$' "$env_file"
  assert_contains '^GROQ_API_KEY=$' "$env_file"
  assert_contains '^ANTHROPIC_API_KEY=$' "$env_file"
  assert_contains '^GEMINI_API_KEY=$' "$env_file"
  assert_regex '^NEXTAUTH_SECRET=[0-9a-f]{64}$' "$env_file"
  assert_regex '^SALT=[0-9a-f]{64}$' "$env_file"
  assert_regex '^ENCRYPTION_KEY=[0-9a-f]{64}$' "$env_file"
  assert_regex '^LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-[0-9a-f]{32}$' "$env_file"
  assert_regex '^LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-[0-9a-f]{32}$' "$env_file"
  assert_contains '^LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=${MINIO_ROOT_PASSWORD}$' "$env_file"
  assert_contains '^LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY=${MINIO_ROOT_PASSWORD}$' "$env_file"
  assert_contains '^LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY=${MINIO_ROOT_PASSWORD}$' "$env_file"
  assert_contains '^LLM_PROVIDER_STRICT_MODE=false$' "$env_file"

  local init_public_key
  local public_key
  init_public_key="$(grep '^LANGFUSE_INIT_PROJECT_PUBLIC_KEY=' "$env_file" | cut -d= -f2-)"
  public_key="$(grep '^LANGFUSE_PUBLIC_KEY=' "$env_file" | cut -d= -f2-)"
  if [[ "$init_public_key" != "$public_key" ]]; then
    echo "LANGFUSE_PUBLIC_KEY should match init public key" >&2
    exit 1
  fi

  run_core_config "$temp_home" $'sk-openai-second\n\n\n\n'

  assert_contains '^OPENAI_API_KEY=sk-openai-second$' "$env_file"
  assert_glob_exists "${env_file}.bak.*"
}

test_auth_setup_dev_and_oidc() {
  local temp_home_dev
  local temp_home_oidc=""
  temp_home_dev="$(mktemp -d)"
  trap 'rm -rf "$temp_home_dev" "$temp_home_oidc"' RETURN

  run_core_config "$temp_home_dev" $'sk-openai\n\n\n\n'
  run_auth_setup "$temp_home_dev" $'1\n'

  local env_dev="${temp_home_dev}/.agr_ai_curation/.env"
  local state_dev="${temp_home_dev}/.agr_ai_curation/.install_auth.env"

  assert_contains '^DEV_MODE=true$' "$env_dev"
  assert_contains '^AUTH_PROVIDER=dev$' "$env_dev"
  assert_contains '^OIDC_GROUP_CLAIM=groups$' "$env_dev"
  assert_contains '^INSTALL_AUTH_TYPE=dev$' "$state_dev"
  assert_contains '^INSTALL_GROUP_CLAIM=groups$' "$state_dev"

  temp_home_oidc="$(mktemp -d)"

  run_core_config "$temp_home_oidc" $'sk-openai\n\n\n\n'
  run_auth_setup "$temp_home_oidc" $'2\nhttps://issuer.example.org/realms/alliance\nalliance-web\nsecret-value\nhttps://app.example.org/auth/callback\nrealm_access.roles\n'

  local env_oidc="${temp_home_oidc}/.agr_ai_curation/.env"
  local state_oidc="${temp_home_oidc}/.agr_ai_curation/.install_auth.env"

  assert_contains '^DEV_MODE=false$' "$env_oidc"
  assert_contains '^AUTH_PROVIDER=oidc$' "$env_oidc"
  assert_contains '^OIDC_ISSUER_URL=https://issuer.example.org/realms/alliance$' "$env_oidc"
  assert_contains '^OIDC_CLIENT_ID=alliance-web$' "$env_oidc"
  assert_contains '^OIDC_CLIENT_SECRET=secret-value$' "$env_oidc"
  assert_contains '^OIDC_REDIRECT_URI=https://app.example.org/auth/callback$' "$env_oidc"
  assert_contains '^OIDC_GROUP_CLAIM=realm_access.roles$' "$env_oidc"
  assert_contains '^INSTALL_AUTH_TYPE=oidc$' "$state_oidc"
  assert_contains '^INSTALL_GROUP_CLAIM=realm_access.roles$' "$state_oidc"
}

test_group_setup_modes_and_backup() {
  local temp_home
  temp_home="$(mktemp -d)"
  trap 'rm -rf "$temp_home"' RETURN

  local groups_output_path="${temp_home}/generated-groups.yaml"

  run_core_config "$temp_home" $'sk-openai\n\n\n\n'
  run_auth_setup "$temp_home" $'1\n'
  run_group_setup "$temp_home" "$groups_output_path" $'1\n'

  assert_contains '^  type: "dev"$' "$groups_output_path"
  assert_contains '^  group_claim: "groups"$' "$groups_output_path"
  assert_contains '^  FB:$' "$groups_output_path"
  assert_contains '^  HGNC:$' "$groups_output_path"

  run_group_setup "$temp_home" "$groups_output_path" $'2\nFB\n'

  assert_glob_exists "${groups_output_path}.bak.*"
  assert_contains '^  FB:$' "$groups_output_path"
  assert_not_contains '^  WB:$' "$groups_output_path"

  run_auth_setup "$temp_home" $'2\nhttps://issuer.example.org\nclient-id\nclient-secret\nhttps://app.example.org/auth/callback\nrealm_access.roles\n'
  run_group_setup "$temp_home" "$groups_output_path" $'3\nMYORG\nMy Organization\nCustom deployment group\nHomo sapiens\nNCBITaxon:9606\nmyorg-curators,myorg-admins\n'

  assert_contains '^  type: "oidc"$' "$groups_output_path"
  assert_contains '^  group_claim: "realm_access.roles"$' "$groups_output_path"
  assert_contains '^  MYORG:$' "$groups_output_path"
  assert_contains '      - "myorg-curators"' "$groups_output_path"
  assert_contains '      - "myorg-admins"' "$groups_output_path"
}

test_group_setup_mode_one_handles_slash_group_claim() {
  local temp_home
  temp_home="$(mktemp -d)"
  trap 'rm -rf "$temp_home"' RETURN

  local groups_output_path="${temp_home}/groups-with-slash-claim.yaml"

  run_core_config "$temp_home" $'sk-openai\n\n\n\n'
  run_auth_setup "$temp_home" $'2\nhttps://issuer.example.org\nclient-id\nclient-secret\nhttps://app.example.org/auth/callback\nrealm_access/roles\n'
  run_group_setup "$temp_home" "$groups_output_path" $'1\n'

  assert_contains '^  group_claim: "realm_access/roles"$' "$groups_output_path"
}

test_group_setup_rejects_invalid_custom_group_id() {
  local temp_home
  temp_home="$(mktemp -d)"
  trap 'rm -rf "$temp_home"' RETURN

  local groups_output_path="${temp_home}/groups-invalid-custom-id.yaml"
  local output_path="${temp_home}/group-setup-invalid.log"

  run_core_config "$temp_home" $'sk-openai\n\n\n\n'
  run_auth_setup "$temp_home" $'1\n'
  run_group_setup_expect_fail "$temp_home" "$groups_output_path" $'3\nMY:ORG\nName\nDescription\nHomo sapiens\nNCBITaxon:9606\ngroup-one\n' "$output_path"

  assert_contains 'Group ID must match \[A-Za-z0-9_]\+' "$output_path"
}

test_group_setup_escapes_yaml_double_quotes() {
  local temp_home
  temp_home="$(mktemp -d)"
  trap 'rm -rf "$temp_home"' RETURN

  local groups_output_path="${temp_home}/groups-escaped-values.yaml"

  run_core_config "$temp_home" $'sk-openai\n\n\n\n'
  run_auth_setup "$temp_home" $'2\nhttps://issuer.example.org\nclient-id\nclient-secret\nhttps://app.example.org/auth/callback\nrealm_access.roles\n'
  run_group_setup "$temp_home" "$groups_output_path" $'3\nMYORG\nMy "Org"\nDesc with "quotes"\nHomo "sapiens"\nNCBITaxon:9606\ngroup "one",group-two\n'

  assert_contains '    name: "My \\"Org\\""' "$groups_output_path"
  assert_contains '    description: "Desc with \\"quotes\\""' "$groups_output_path"
  assert_contains '    species: "Homo \\"sapiens\\""' "$groups_output_path"
  assert_contains '      - "group \\"one\\""' "$groups_output_path"
}

test_orchestrator_skip_flags() {
  local temp_home
  temp_home="$(mktemp -d)"
  trap 'rm -rf "$temp_home"' RETURN

  local groups_output_path="${temp_home}/orchestrator-groups.yaml"

  HOME="$temp_home" INSTALL_GROUPS_OUTPUT_PATH="$groups_output_path" bash "$orchestrator_script" \
    --skip-preflight \
    --skip-group-setup \
    --skip-pdfx-setup <<< $'sk-orchestrator\n\n\n\n1\n'

  [[ -f "${temp_home}/.agr_ai_curation/.env" ]] || {
    echo "Orchestrator did not create env file" >&2
    exit 1
  }

  if [[ -f "$groups_output_path" ]]; then
    echo "Orchestrator should skip group setup when --skip-group-setup is provided" >&2
    exit 1
  fi
}

test_core_config_generates_env_and_backups
test_auth_setup_dev_and_oidc
test_group_setup_modes_and_backup
test_group_setup_mode_one_handles_slash_group_claim
test_group_setup_rejects_invalid_custom_group_id
test_group_setup_escapes_yaml_double_quotes
test_orchestrator_skip_flags

echo "core/auth/group installer stage checks passed"
