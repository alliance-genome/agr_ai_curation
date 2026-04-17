#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

assert_contains_line() {
  local expected="$1"
  local text="$2"

  if ! grep -Fx "${expected}" <<<"${text}" >/dev/null 2>&1; then
    echo "Expected to find line '${expected}'." >&2
    printf '%s\n' "${text}" >&2
    exit 1
  fi
}

assert_not_contains_line() {
  local unexpected="$1"
  local text="$2"

  if grep -Fx "${unexpected}" <<<"${text}" >/dev/null 2>&1; then
    echo "Did not expect to find line '${unexpected}'." >&2
    printf '%s\n' "${text}" >&2
    exit 1
  fi
}

make_compose_env_file() {
  local env_file="$1"
  local db_scheme db_auth db_host db_name db_url
  db_scheme="postgresql://"
  db_auth="postgres:placeholder"
  db_host="postgres:5432"
  db_name="postgres"
  db_url="${db_scheme}${db_auth}@${db_host}/${db_name}"

  cat > "${env_file}" <<'EOF'
POSTGRES_PASSWORD=placeholder
REDIS_AUTH=placeholder
OPENAI_API_KEY=placeholder
LANGFUSE_LOCAL_SALT=placeholder
LANGFUSE_LOCAL_ENCRYPTION_KEY=placeholder
LANGFUSE_LOCAL_NEXTAUTH_SECRET=placeholder
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=placeholder
LANGFUSE_INIT_PROJECT_SECRET_KEY=placeholder
EOF

  {
    printf 'LANGFUSE_LOCAL_DATABASE_URL=%s\n' "${db_url}"
    printf 'DATABASE_URL=%s\n' "${db_url}"
  } >> "${env_file}"
}

test_compose_topology_skips_local_reranker_without_profile() {
  local env_file dev_services prod_services test_services
  env_file="$(mktemp)"
  trap 'rm -f "${env_file}"' RETURN
  make_compose_env_file "${env_file}"

  dev_services="$(docker compose --env-file "${env_file}" -f "${REPO_ROOT}/docker-compose.yml" config --services)"
  test_services="$(docker compose --env-file "${env_file}" -f "${REPO_ROOT}/docker-compose.test.yml" config --services)"
  prod_services="$(docker compose --env-file "${env_file}" -f "${REPO_ROOT}/docker-compose.production.yml" config --services)"

  assert_not_contains_line "reranker-transformers" "${dev_services}"
  assert_not_contains_line "reranker-transformers-test" "${test_services}"
  assert_not_contains_line "reranker-transformers" "${prod_services}"
}

test_compose_topology_includes_local_reranker_with_profile() {
  local env_file dev_services prod_services test_services
  env_file="$(mktemp)"
  trap 'rm -f "${env_file}"' RETURN
  make_compose_env_file "${env_file}"

  dev_services="$(COMPOSE_PROFILES=local-reranker docker compose --env-file "${env_file}" -f "${REPO_ROOT}/docker-compose.yml" config --services)"
  test_services="$(COMPOSE_PROFILES=local-reranker docker compose --env-file "${env_file}" -f "${REPO_ROOT}/docker-compose.test.yml" config --services)"
  prod_services="$(COMPOSE_PROFILES=local-reranker docker compose --env-file "${env_file}" -f "${REPO_ROOT}/docker-compose.production.yml" config --services)"

  assert_contains_line "reranker-transformers" "${dev_services}"
  assert_contains_line "reranker-transformers-test" "${test_services}"
  assert_contains_line "reranker-transformers" "${prod_services}"
}

test_compose_topology_skips_local_reranker_without_profile
test_compose_topology_includes_local_reranker_with_profile

echo "reranker_compose_profiles tests passed"
