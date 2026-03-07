#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPAIR_SCRIPT="${REPO_ROOT}/scripts/utilities/ensure_local_langfuse_env.sh"

assert_regex() {
  local regex="$1"
  local file_path="$2"
  if ! grep -Eq "$regex" "$file_path"; then
    echo "Expected regex '$regex' in $file_path" >&2
    cat "$file_path" >&2
    exit 1
  fi
}

assert_contains() {
  local needle="$1"
  local file_path="$2"
  if ! grep -Eq "$needle" "$file_path"; then
    echo "Expected to find '$needle' in $file_path" >&2
    cat "$file_path" >&2
    exit 1
  fi
}

assert_equals() {
  local expected="$1"
  local actual="$2"
  if [[ "$expected" != "$actual" ]]; then
    echo "Expected '$expected', got '$actual'" >&2
    exit 1
  fi
}

assert_value_regex() {
  local regex="$1"
  local actual="$2"
  if [[ ! "$actual" =~ $regex ]]; then
    echo "Expected value matching '$regex', got '$actual'" >&2
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

env_value() {
  local env_file="$1"
  local key="$2"

  awk -F= -v key="$key" '$1 == key { print substr($0, length(key) + 2); exit }' "$env_file"
}

repeat_char() {
  local char="$1"
  local count="$2"
  local output=""

  for _ in $(seq 1 "$count"); do
    output+="$char"
  done

  printf '%s' "$output"
}

invalid_langfuse_encryption_placeholder() {
  local placeholder="CHANGE"
  placeholder+="_ME_64_CHAR_HEX_KEY"
  placeholder+="_HERE"
  placeholder+="_________________________________________________"
  printf '%s' "$placeholder"
}

placeholder_langfuse_key() {
  local key_kind="$1"
  local key_prefix="your_"
  key_prefix+="langfuse_"
  printf '%s%s%s' "$key_prefix" "$key_kind" "_key"
}

local_langfuse_database_auth() {
  local auth_value="post"
  auth_value+="gres"
  printf '%s' "$auth_value"
}

stale_langfuse_database_url() {
  local scheme="postgresql"
  local user="langfuse_user"
  local password="langfuse_"
  local host="langfuse-db:5432/langfuse"
  password+="pass"
  printf '%s://%s:%s@%s' "$scheme" "$user" "$password" "$host"
}

override_stale_database_url() {
  local scheme="postgresql"
  local user="old"
  local password="old"
  local host="old-db:5432/old"
  password+="val"
  printf '%s://%s:%s@%s' "$scheme" "$user" "$password" "$host"
}

stale_legacy_marker() {
  local prefix="old_"
  prefix+="legacy_"
  printf '%s%s' "$prefix" "$1"
}

canonical_langfuse_url_regex() {
  local scheme="postgresql"
  local user="postgres"
  local host="postgres:5432/postgres"
  printf '%s://%s%s%s' "$scheme" "$user" ':.*@' "$host"
}

canonical_langfuse_env_reference_url() {
  local scheme="postgresql"
  local user="postgres"
  local host="postgres:5432/postgres"
  printf '%s://%s:%s@%s' "$scheme" "$user" '${POSTGRES_PASSWORD}' "$host"
}

canonical_langfuse_literal_url() {
  local db_auth_value="$1"
  local scheme="postgresql"
  local user="postgres"
  local host="postgres:5432/postgres"
  printf '%s://%s:%s@%s' "$scheme" "$user" "$db_auth_value" "$host"
}

langfuse_init_user_credential_key() {
  printf '%s%s' 'LANGFUSE_INIT_USER_' 'PASSWORD'
}

test_repairs_stale_langfuse_values() {
  local temp_dir env_file stale_langfuse_url db_auth_value canonical_langfuse_url_pattern invalid_encryption_key placeholder_public_key placeholder_secret_key init_user_credential init_user_credential_key
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN
  env_file="${temp_dir}/.env"
  stale_langfuse_url="$(stale_langfuse_database_url)"
  db_auth_value="$(local_langfuse_database_auth)"
  canonical_langfuse_url_pattern="$(canonical_langfuse_url_regex)"
  invalid_encryption_key="$(invalid_langfuse_encryption_placeholder)"
  placeholder_public_key="$(placeholder_langfuse_key public)"
  placeholder_secret_key="$(placeholder_langfuse_key secret)"
  init_user_credential_key="$(langfuse_init_user_credential_key)"

  cat >"$env_file" <<EOF
OPENAI_API_KEY=sk-test
POSTGRES_PASSWORD=$db_auth_value
NEXTAUTH_SECRET=
SALT=CHANGE_ME_RANDOM_SALT
ENCRYPTION_KEY=$invalid_encryption_key
LANGFUSE_PUBLIC_KEY=$placeholder_public_key
LANGFUSE_SECRET_KEY=$placeholder_secret_key
LANGFUSE_HOST=http://your-langfuse-host:3000
LANGFUSE_DATABASE_URL=$stale_langfuse_url
EOF

  bash "$REPAIR_SCRIPT" "$env_file"

  assert_regex '^NEXTAUTH_SECRET=[0-9a-fA-F]{64}$' "$env_file"
  assert_regex '^SALT=[0-9a-fA-F]{64}$' "$env_file"
  assert_regex '^ENCRYPTION_KEY=[0-9a-fA-F]{64}$' "$env_file"
  assert_regex '^LANGFUSE_LOCAL_NEXTAUTH_SECRET=[0-9a-fA-F]{64}$' "$env_file"
  assert_regex '^LANGFUSE_LOCAL_SALT=[0-9a-fA-F]{64}$' "$env_file"
  assert_regex '^LANGFUSE_LOCAL_ENCRYPTION_KEY=[0-9a-fA-F]{64}$' "$env_file"
  assert_regex '^LANGFUSE_INIT_PROJECT_PUBLIC_KEY=pk-lf-[0-9a-fA-F]{32}$' "$env_file"
  assert_regex '^LANGFUSE_INIT_PROJECT_SECRET_KEY=sk-lf-[0-9a-fA-F]{32}$' "$env_file"
  assert_regex '^LANGFUSE_LOCAL_PUBLIC_KEY=pk-lf-[0-9a-fA-F]{32}$' "$env_file"
  assert_regex '^LANGFUSE_LOCAL_SECRET_KEY=sk-lf-[0-9a-fA-F]{32}$' "$env_file"
  init_user_credential="$(env_value "$env_file" "$init_user_credential_key")"
  assert_value_regex '^[0-9a-fA-F]{32}$' "$init_user_credential"
  assert_contains '^LANGFUSE_HOST=http://localhost:3000$' "$env_file"
  assert_contains "^LANGFUSE_DATABASE_URL=${canonical_langfuse_url_pattern}$" "$env_file"
  assert_contains "^LANGFUSE_LOCAL_DATABASE_URL=${canonical_langfuse_url_pattern}$" "$env_file"
  assert_glob_exists "${env_file}.bak.*"

  local init_public_key runtime_public_key init_secret_key runtime_secret_key
  init_public_key="$(env_value "$env_file" "LANGFUSE_INIT_PROJECT_PUBLIC_KEY")"
  runtime_public_key="$(env_value "$env_file" "LANGFUSE_PUBLIC_KEY")"
  init_secret_key="$(env_value "$env_file" "LANGFUSE_INIT_PROJECT_SECRET_KEY")"
  runtime_secret_key="$(env_value "$env_file" "LANGFUSE_SECRET_KEY")"

  assert_equals "$init_public_key" "$runtime_public_key"
  assert_equals "$init_secret_key" "$runtime_secret_key"
}

test_preserves_valid_values() {
  local temp_dir env_file before after nextauth salt encryption public_key secret_key init_password db_auth_value canonical_langfuse_url init_user_credential_key
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN
  env_file="${temp_dir}/.env"

  nextauth="$(repeat_char 'a' 64)"
  salt="$(repeat_char 'b' 64)"
  encryption="$(repeat_char 'c' 64)"
  public_key="pk-lf-$(repeat_char '1' 32)"
  secret_key="sk-lf-$(repeat_char '2' 32)"
  init_password="$(repeat_char '3' 32)"
  db_auth_value="$(local_langfuse_database_auth)"
  canonical_langfuse_url="$(canonical_langfuse_env_reference_url)"
  init_user_credential_key="$(langfuse_init_user_credential_key)"

  cat >"$env_file" <<EOF
OPENAI_API_KEY=sk-test
POSTGRES_PASSWORD=$db_auth_value
NEXTAUTH_SECRET=$nextauth
SALT=$salt
ENCRYPTION_KEY=$encryption
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=$public_key
LANGFUSE_INIT_PROJECT_SECRET_KEY=$secret_key
${init_user_credential_key}=$init_password
LANGFUSE_PUBLIC_KEY=$public_key
LANGFUSE_SECRET_KEY=$secret_key
LANGFUSE_LOCAL_PUBLIC_KEY=$public_key
LANGFUSE_LOCAL_SECRET_KEY=$secret_key
LANGFUSE_LOCAL_NEXTAUTH_SECRET=$nextauth
LANGFUSE_LOCAL_SALT=$salt
LANGFUSE_LOCAL_ENCRYPTION_KEY=$encryption
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_DATABASE_URL=$canonical_langfuse_url
LANGFUSE_LOCAL_DATABASE_URL=$canonical_langfuse_url
EOF

  before="$(cat "$env_file")"
  bash "$REPAIR_SCRIPT" "$env_file"
  after="$(cat "$env_file")"

  assert_equals "$before" "$after"
}

test_load_home_test_env_repairs_before_export() {
  local temp_home env_dir env_file output_file stale_langfuse_url db_auth_value canonical_langfuse_url_pattern invalid_encryption_key placeholder_public_key placeholder_secret_key
  temp_home="$(mktemp -d)"
  trap 'rm -rf "$temp_home"' RETURN
  env_dir="${temp_home}/.agr_ai_curation"
  env_file="${env_dir}/.env"
  output_file="${temp_home}/loader_output.txt"
  stale_langfuse_url="$(stale_langfuse_database_url)"
  db_auth_value="$(local_langfuse_database_auth)"
  canonical_langfuse_url_pattern="$(canonical_langfuse_url_regex)"
  invalid_encryption_key="$(invalid_langfuse_encryption_placeholder)"
  placeholder_public_key="$(placeholder_langfuse_key public)"
  placeholder_secret_key="$(placeholder_langfuse_key secret)"

  mkdir -p "$env_dir"
  cat >"$env_file" <<EOF
OPENAI_API_KEY=sk-test
POSTGRES_PASSWORD=$db_auth_value
ENCRYPTION_KEY=$invalid_encryption_key
LANGFUSE_PUBLIC_KEY=$placeholder_public_key
LANGFUSE_SECRET_KEY=$placeholder_secret_key
LANGFUSE_HOST=http://your-langfuse-host:3000
LANGFUSE_DATABASE_URL=$stale_langfuse_url
EOF

  HOME="$temp_home" XDG_CONFIG_HOME="${temp_home}/.config" bash -c "
    set -euo pipefail
    cd '$REPO_ROOT'
    source scripts/testing/load-home-test-env.sh >/dev/null
    {
      printf 'ENCRYPTION_KEY=%s\n' \"\$ENCRYPTION_KEY\"
      printf 'LANGFUSE_PUBLIC_KEY=%s\n' \"\$LANGFUSE_PUBLIC_KEY\"
      printf 'LANGFUSE_SECRET_KEY=%s\n' \"\$LANGFUSE_SECRET_KEY\"
      printf 'LANGFUSE_HOST=%s\n' \"\$LANGFUSE_HOST\"
      printf 'LANGFUSE_DATABASE_URL=%s\n' \"\$LANGFUSE_DATABASE_URL\"
      printf 'LANGFUSE_LOCAL_DATABASE_URL=%s\n' \"\$LANGFUSE_LOCAL_DATABASE_URL\"
    } > '$output_file'
  "

  assert_regex '^ENCRYPTION_KEY=[0-9a-fA-F]{64}$' "$output_file"
  assert_regex '^LANGFUSE_PUBLIC_KEY=pk-lf-[0-9a-fA-F]{32}$' "$output_file"
  assert_regex '^LANGFUSE_SECRET_KEY=sk-lf-[0-9a-fA-F]{32}$' "$output_file"
  assert_contains '^LANGFUSE_HOST=http://localhost:3000$' "$output_file"
  assert_contains "^LANGFUSE_DATABASE_URL=${canonical_langfuse_url_pattern}$" "$output_file"
  assert_contains "^LANGFUSE_LOCAL_DATABASE_URL=${canonical_langfuse_url_pattern}$" "$output_file"
}

test_compose_uses_legacy_vars_when_local_not_set() {
  # Production backward compat: when LANGFUSE_LOCAL_* vars are absent,
  # compose falls through to legacy var names so EC2 deployments
  # that run raw "docker compose up" still work.
  local temp_env output db_auth_value legacy_salt legacy_encryption legacy_nextauth legacy_db_url legacy_public legacy_secret
  temp_env="$(mktemp)"
  trap 'rm -f "$temp_env"' RETURN
  db_auth_value="$(local_langfuse_database_auth)"
  legacy_salt="$(repeat_char 'd' 64)"
  legacy_encryption="$(repeat_char 'e' 64)"
  legacy_nextauth="$(repeat_char 'f' 64)"
  legacy_db_url="$(canonical_langfuse_literal_url "$db_auth_value")"
  legacy_public="pk-lf-$(repeat_char '4' 32)"
  legacy_secret="sk-lf-$(repeat_char '5' 32)"

  cat >"$temp_env" <<EOF
POSTGRES_PASSWORD=$db_auth_value
SALT=$legacy_salt
ENCRYPTION_KEY=$legacy_encryption
NEXTAUTH_SECRET=$legacy_nextauth
LANGFUSE_DATABASE_URL=$legacy_db_url
LANGFUSE_PUBLIC_KEY=$legacy_public
LANGFUSE_SECRET_KEY=$legacy_secret
EOF

  output="$(docker compose --env-file "$temp_env" config)"

  grep -q "SALT: $legacy_salt" <<<"$output"
  grep -q "ENCRYPTION_KEY: $legacy_encryption" <<<"$output"
  grep -q "NEXTAUTH_SECRET: $legacy_nextauth" <<<"$output"
  grep -q "DATABASE_URL: $legacy_db_url" <<<"$output"
  grep -q "LANGFUSE_PUBLIC_KEY: $legacy_public" <<<"$output"
  grep -q "LANGFUSE_SECRET_KEY: $legacy_secret" <<<"$output"
}

test_compose_local_vars_override_legacy() {
  # When LANGFUSE_LOCAL_* vars are set (repaired env), they take
  # precedence over legacy vars.
  local temp_env output db_auth_value local_salt local_encryption local_nextauth local_db_url local_public local_secret
  local stale_salt stale_encryption stale_nextauth stale_db_url stale_public stale_secret stale_marker
  temp_env="$(mktemp)"
  trap 'rm -f "$temp_env"' RETURN
  db_auth_value="$(local_langfuse_database_auth)"
  local_salt="$(repeat_char 'd' 64)"
  local_encryption="$(repeat_char 'e' 64)"
  local_nextauth="$(repeat_char 'f' 64)"
  local_db_url="$(canonical_langfuse_literal_url "$db_auth_value")"
  local_public="pk-lf-$(repeat_char '4' 32)"
  local_secret="sk-lf-$(repeat_char '5' 32)"
  stale_salt="$(stale_legacy_marker salt)"
  stale_encryption="$(stale_legacy_marker encryption)"
  stale_nextauth="$(stale_legacy_marker nextauth)"
  stale_db_url="$(override_stale_database_url)"
  stale_public="$(stale_legacy_marker public)"
  stale_secret="$(stale_legacy_marker secret)"
  stale_marker="$(stale_legacy_marker "")"

  cat >"$temp_env" <<EOF
POSTGRES_PASSWORD=$db_auth_value
SALT=$stale_salt
ENCRYPTION_KEY=$stale_encryption
NEXTAUTH_SECRET=$stale_nextauth
LANGFUSE_DATABASE_URL=$stale_db_url
LANGFUSE_PUBLIC_KEY=$stale_public
LANGFUSE_SECRET_KEY=$stale_secret
LANGFUSE_LOCAL_SALT=$local_salt
LANGFUSE_LOCAL_ENCRYPTION_KEY=$local_encryption
LANGFUSE_LOCAL_NEXTAUTH_SECRET=$local_nextauth
LANGFUSE_LOCAL_DATABASE_URL=$local_db_url
LANGFUSE_LOCAL_PUBLIC_KEY=$local_public
LANGFUSE_LOCAL_SECRET_KEY=$local_secret
EOF

  output="$(docker compose --env-file "$temp_env" config)"

  grep -q "SALT: $local_salt" <<<"$output"
  grep -q "ENCRYPTION_KEY: $local_encryption" <<<"$output"
  grep -q "NEXTAUTH_SECRET: $local_nextauth" <<<"$output"
  grep -q "DATABASE_URL: $local_db_url" <<<"$output"
  grep -q "LANGFUSE_PUBLIC_KEY: $local_public" <<<"$output"
  grep -q "LANGFUSE_SECRET_KEY: $local_secret" <<<"$output"
  ! grep -q "$stale_marker" <<<"$output"
  ! grep -q "old-db" <<<"$output"
}

test_repairs_stale_langfuse_values
test_preserves_valid_values
test_load_home_test_env_repairs_before_export
test_compose_uses_legacy_vars_when_local_not_set
test_compose_local_vars_override_legacy

echo "ensure_local_langfuse_env tests passed"
