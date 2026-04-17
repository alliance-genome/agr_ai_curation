#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

assert_contains() {
  local pattern="$1"
  local file="$2"
  if ! rg -n --fixed-strings "$pattern" "$file" >/dev/null 2>&1; then
    echo "Expected to find '$pattern' in $file" >&2
    exit 1
  fi
}

make_stub_docker() {
  local stub_dir="$1"
  mkdir -p "${stub_dir}"

  cat > "${stub_dir}/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
log_file="${DOCKER_STUB_LOG:?}"
printf 'args=%s\n' "$*" >> "${log_file}"
printf 'docker_host=%s\n' "${DOCKER_HOST:-unset}" >> "${log_file}"
EOF

  chmod +x "${stub_dir}/docker"
}

test_docker_test_compose_defaults_to_rootless() {
  local temp_root stub_dir docker_log
  temp_root="$(mktemp -d)"
  stub_dir="${temp_root}/stubbin"
  docker_log="${temp_root}/docker.log"

  make_stub_docker "${stub_dir}"

  (
    cd "${REPO_ROOT}"
    export PATH="${stub_dir}:${PATH}"
    export DOCKER_STUB_LOG="${docker_log}"
    ./scripts/testing/docker-test-compose.sh ps >/dev/null 2>&1
  )

  assert_contains "args=compose -f ${REPO_ROOT}/docker-compose.test.yml ps" "${docker_log}"
  assert_contains "docker_host=unix:///run/user/$(id -u)/docker.sock" "${docker_log}"
}

test_docker_test_compose_can_force_rootful() {
  local temp_root stub_dir docker_log
  temp_root="$(mktemp -d)"
  stub_dir="${temp_root}/stubbin"
  docker_log="${temp_root}/docker.log"

  make_stub_docker "${stub_dir}"

  (
    cd "${REPO_ROOT}"
    export PATH="${stub_dir}:${PATH}"
    export DOCKER_STUB_LOG="${docker_log}"
    ./scripts/testing/docker-test-compose.sh --rootful ps >/dev/null 2>&1
  )

  assert_contains "args=compose -f ${REPO_ROOT}/docker-compose.test.yml ps" "${docker_log}"
  assert_contains "docker_host=unset" "${docker_log}"
}

test_docker_test_compose_enables_local_reranker_profile_for_local_provider() {
  local temp_root stub_dir docker_log
  temp_root="$(mktemp -d)"
  stub_dir="${temp_root}/stubbin"
  docker_log="${temp_root}/docker.log"

  make_stub_docker "${stub_dir}"

  (
    cd "${REPO_ROOT}"
    export PATH="${stub_dir}:${PATH}"
    export DOCKER_STUB_LOG="${docker_log}"
    export RERANK_PROVIDER=local_transformers
    ./scripts/testing/docker-test-compose.sh ps >/dev/null 2>&1
  )

  assert_contains "args=compose -f ${REPO_ROOT}/docker-compose.test.yml --profile local-reranker ps" "${docker_log}"
}

test_docker_test_compose_can_force_rootful
test_docker_test_compose_defaults_to_rootless
test_docker_test_compose_enables_local_reranker_profile_for_local_provider

echo "docker_test_compose tests passed"
