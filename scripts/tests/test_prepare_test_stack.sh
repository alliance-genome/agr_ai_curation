#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/assertions.sh"

make_stub_docker() {
  local stub_dir="$1"
  mkdir -p "${stub_dir}"

  cat > "${stub_dir}/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
log_file="${DOCKER_STUB_LOG:?}"
printf 'args=%s\n' "$*" >> "${log_file}"

if [[ -n "${STUB_DOCKER_FAIL_ONCE_MATCH:-}" && "$*" == *"${STUB_DOCKER_FAIL_ONCE_MATCH}"* ]]; then
  count_file="${STUB_DOCKER_FAIL_ONCE_COUNT_FILE:?}"
  count=0
  if [[ -f "${count_file}" ]]; then
    count="$(cat "${count_file}")"
  fi
  if [[ "${count}" == "0" ]]; then
    echo "1" > "${count_file}"
    echo "simulated docker failure for: $*" >&2
    exit 1
  fi
fi

if [[ "$*" == *" port postgres-test 5432"* ]]; then
  echo "127.0.0.1:15434"
  exit 0
fi
if [[ "$*" == *" port weaviate-test 8080"* ]]; then
  echo "127.0.0.1:18080"
  exit 0
fi
if [[ "$*" == *" port weaviate-test 50051"* ]]; then
  echo "127.0.0.1:15051"
  exit 0
fi
if [[ "$*" == *" ps"* ]]; then
  cat <<'PS'
NAME                            STATUS                SERVICE
prepare-postgres-test-1         running(healthy)      postgres-test
prepare-weaviate-test-1         running(unhealthy)    weaviate-test
PS
  exit 0
fi
if [[ "$*" == *" logs "* ]]; then
  echo "stub logs for $*"
  exit 0
fi

printf 'docker_host=%s\n' "${DOCKER_HOST:-unset}" >> "${log_file}"
EOF

  chmod +x "${stub_dir}/docker"
}

test_prepare_test_stack_skips_reranker_for_non_local_provider() {
  local temp_root stub_dir output docker_log home_env fail_once_count
  temp_root="$(mktemp -d)"
  stub_dir="${temp_root}/stubbin"
  output="${temp_root}/output.txt"
  docker_log="${temp_root}/docker.log"
  home_env="${temp_root}/test.env"
  fail_once_count="${temp_root}/docker-fail-once.count"

  : > "${home_env}"
  make_stub_docker "${stub_dir}"

  (
    cd "${REPO_ROOT}"
    export PATH="${stub_dir}:${PATH}"
    export DOCKER_STUB_LOG="${docker_log}"
    export TEST_SECRETS_ENV_FILE="${home_env}"
    export STUB_DOCKER_FAIL_ONCE_MATCH="up -d --wait postgres-test redis-test weaviate-test"
    export STUB_DOCKER_FAIL_ONCE_COUNT_FILE="${fail_once_count}"
    export TEST_STACK_START_RETRY_SLEEP_SECONDS=0
    ./scripts/testing/prepare-test-stack.sh > "${output}" 2>&1
  )

  assert_contains "rerank_provider=none" "${output}"
  assert_contains "reranker_dependency_required=0" "${output}"
  assert_contains "Test infrastructure startup attempt 1/2 failed." "${output}"
  assert_contains "Test stack diagnostics:" "${output}"
  assert_contains "Test infrastructure is ready." "${output}"
  assert_contains "Postgres host port: 15434" "${output}"
  assert_contains "Weaviate host port: 18080" "${output}"
  assert_contains "Weaviate gRPC host port: 15051" "${output}"
  assert_count "2" "args=compose -f ${REPO_ROOT}/docker-compose.test.yml up -d --wait postgres-test redis-test weaviate-test" "${docker_log}"
  assert_not_contains "args=compose -f ${REPO_ROOT}/docker-compose.test.yml up -d --wait postgres-test reranker-transformers-test redis-test weaviate-test" "${docker_log}"
  assert_contains "docker_host=unix:///run/user/$(id -u)/docker.sock" "${docker_log}"
  # Split the DATABASE_URL assertion to avoid TruffleHog false positive on test fixture credentials.
  local expected_db_url="postgresql://postgres:postgres"
  expected_db_url="${expected_db_url}@postgres-test:5432/ai_curation"
  assert_contains "args=compose -f ${REPO_ROOT}/docker-compose.test.yml run --rm -e DATABASE_URL=${expected_db_url} backend-unit-tests bash -lc cd /app/backend && alembic upgrade head" "${docker_log}"
  assert_contains "export TEST_DB_PORT_HOST=15434" "${REPO_ROOT}/.test-stack.env"
  assert_contains "export TEST_WEAVIATE_PORT_HOST=18080" "${REPO_ROOT}/.test-stack.env"
  assert_contains "export RERANK_PROVIDER=none" "${REPO_ROOT}/.test-stack.env"

  rm -f "${REPO_ROOT}/.test-stack.env"
}

test_prepare_test_stack_skips_reranker_for_non_local_provider

test_prepare_test_stack_includes_reranker_for_local_provider() {
  local temp_root stub_dir output docker_log home_env
  temp_root="$(mktemp -d)"
  stub_dir="${temp_root}/stubbin"
  output="${temp_root}/output.txt"
  docker_log="${temp_root}/docker.log"
  home_env="${temp_root}/test.env"

  : > "${home_env}"
  make_stub_docker "${stub_dir}"

  (
    cd "${REPO_ROOT}"
    export PATH="${stub_dir}:${PATH}"
    export DOCKER_STUB_LOG="${docker_log}"
    export TEST_SECRETS_ENV_FILE="${home_env}"
    export RERANK_PROVIDER=local_transformers
    export TEST_STACK_START_RETRY_SLEEP_SECONDS=0
    ./scripts/testing/prepare-test-stack.sh > "${output}" 2>&1
  )

  assert_contains "rerank_provider=local_transformers" "${output}"
  assert_contains "reranker_dependency_required=1" "${output}"
  assert_contains "args=compose -f ${REPO_ROOT}/docker-compose.test.yml --profile local-reranker up -d --wait postgres-test reranker-transformers-test redis-test weaviate-test" "${docker_log}"
  assert_contains "export RERANK_PROVIDER=local_transformers" "${REPO_ROOT}/.test-stack.env"

  rm -f "${REPO_ROOT}/.test-stack.env"
}

test_prepare_test_stack_includes_reranker_for_local_provider

test_prepare_test_stack_can_force_rootful_mode() {
  local temp_root stub_dir output docker_log home_env
  temp_root="$(mktemp -d)"
  stub_dir="${temp_root}/stubbin"
  output="${temp_root}/output.txt"
  docker_log="${temp_root}/docker.log"
  home_env="${temp_root}/test.env"

  : > "${home_env}"
  make_stub_docker "${stub_dir}"

  (
    cd "${REPO_ROOT}"
    export PATH="${stub_dir}:${PATH}"
    export DOCKER_STUB_LOG="${docker_log}"
    export TEST_SECRETS_ENV_FILE="${home_env}"
    export AI_CURATION_TEST_DOCKER_MODE=rootful
    export TEST_STACK_START_RETRY_SLEEP_SECONDS=0
    ./scripts/testing/prepare-test-stack.sh > "${output}" 2>&1
  )

  assert_contains "docker_host=unset" "${docker_log}"
  rm -f "${REPO_ROOT}/.test-stack.env"
}

test_prepare_test_stack_can_force_rootful_mode

echo "prepare_test_stack tests passed"
