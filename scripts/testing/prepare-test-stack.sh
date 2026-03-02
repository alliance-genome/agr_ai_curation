#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/docker-compose.test.yml"

# Load optional local secrets and runtime vars from home env files.
# This exports values into the current process only.
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/load-home-test-env.sh"

# Defaults for isolated test infrastructure.
export TEST_DB_USER="${TEST_DB_USER:-postgres}"
export TEST_DB_PASSWORD="${TEST_DB_PASSWORD:-postgres}"
export TEST_DB_NAME="${TEST_DB_NAME:-ai_curation}"
export TEST_DB_PORT_HOST="${TEST_DB_PORT_HOST:-15434}"
export TEST_WEAVIATE_PORT_HOST="${TEST_WEAVIATE_PORT_HOST:-18080}"
export TEST_WEAVIATE_GRPC_PORT_HOST="${TEST_WEAVIATE_GRPC_PORT_HOST:-15051}"
export TEST_REDIS_PASSWORD="${TEST_REDIS_PASSWORD:-CHANGE_ME_TEST_REDIS_PASSWORD}"

export TEST_DB_HOST="${TEST_DB_HOST:-postgres-test}"
export TEST_DB_PORT="${TEST_DB_PORT:-5432}"

TEST_DATABASE_URL_IN_STACK="postgresql://${TEST_DB_USER}:${TEST_DB_PASSWORD}@${TEST_DB_HOST}:${TEST_DB_PORT}/${TEST_DB_NAME}"
TEST_DATABASE_URL_HOST="postgresql://${TEST_DB_USER}:${TEST_DB_PASSWORD}@127.0.0.1:${TEST_DB_PORT_HOST}/${TEST_DB_NAME}"

echo "Starting isolated test infrastructure (postgres-test + weaviate-test)..."
# Ensure deterministic infra state for schema-sensitive integration tests.
# Recreate test containers so stale Weaviate schemas (for example legacy
# non-vectorized DocumentChunk classes) do not persist across runs.
docker compose -f "${COMPOSE_FILE}" rm -sf \
  postgres-test \
  reranker-transformers-test \
  redis-test \
  weaviate-test >/dev/null 2>&1 || true

docker compose -f "${COMPOSE_FILE}" up -d --wait \
  postgres-test \
  reranker-transformers-test \
  redis-test \
  weaviate-test

if [[ "${SKIP_TEST_DB_MIGRATIONS:-0}" == "1" ]]; then
  echo "Skipping Alembic migrations because SKIP_TEST_DB_MIGRATIONS=1."
else
  echo "Applying Alembic migrations to test database..."
  docker compose -f "${COMPOSE_FILE}" run --rm \
    -e DATABASE_URL="${TEST_DATABASE_URL_IN_STACK}" \
    backend-unit-tests \
    bash -lc "cd /app/backend && alembic upgrade head"
fi

echo "Test infrastructure is ready."
echo "Postgres host port: ${TEST_DB_PORT_HOST}"
echo "Weaviate host port: ${TEST_WEAVIATE_PORT_HOST}"
echo "Weaviate gRPC host port: ${TEST_WEAVIATE_GRPC_PORT_HOST}"
echo "Host DATABASE_URL example: ${TEST_DATABASE_URL_HOST}"
