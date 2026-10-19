#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

assert_contains() {
  local pattern="$1"
  local file="$2"
  if ! rg -n --fixed-strings -- "$pattern" "$file" >/dev/null 2>&1; then
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
printf 'project=%s\n' "${COMPOSE_PROJECT_NAME:-unset}" >> "${log_file}"
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

test_build_command_boundary() {
  local temp_root
  temp_root="$(mktemp -d)"
  make_stub_docker "${temp_root}/bin"
  (
    export PATH="${temp_root}/bin:${PATH}"
    export DOCKER_STUB_LOG="${temp_root}/docker.log"
    "${REPO_ROOT}/scripts/testing/docker-test-compose.sh" -p run run --rm backend-unit-tests echo up
    "${REPO_ROOT}/scripts/testing/docker-test-compose.sh" --env-file up up -d backend-unit-tests
    "${REPO_ROOT}/scripts/testing/docker-test-compose.sh" config --services
    "${REPO_ROOT}/scripts/testing/docker-test-compose.sh" down -v
  )
  assert_contains '-p run run --build --rm backend-unit-tests echo up' "${temp_root}/docker.log"
  assert_contains '--env-file up up --build -d backend-unit-tests' "${temp_root}/docker.log"
  assert_contains 'config --services' "${temp_root}/docker.log"
  assert_contains 'down -v' "${temp_root}/docker.log"
  [[ "$(rg -c -- '--build' "${temp_root}/docker.log")" == 2 ]]
  rm -rf "${temp_root}"
}

test_workspace_project_identity() {
  local temp_root workspace project_a project_b
  temp_root="$(mktemp -d)"
  make_stub_docker "${temp_root}/bin"
  for workspace in a b; do
    mkdir -p "${temp_root}/${workspace}/scripts/"{testing,lib}
    cp "${REPO_ROOT}/scripts/testing/docker-test-compose.sh" "${temp_root}/${workspace}/scripts/testing/"
    cp "${REPO_ROOT}/scripts/lib/rerank_provider_common.sh" "${temp_root}/${workspace}/scripts/lib/"
    env -u COMPOSE_PROJECT_NAME PATH="${temp_root}/bin:${PATH}" DOCKER_STUB_LOG="${temp_root}/${workspace}.log" \
      bash "${temp_root}/${workspace}/scripts/testing/docker-test-compose.sh" ps
  done
  project_a="$(rg '^project=' "${temp_root}/a.log")"
  project_b="$(rg '^project=' "${temp_root}/b.log")"
  [[ "${project_a}" != "${project_b}" ]]
  env COMPOSE_PROJECT_NAME=explicit PATH="${temp_root}/bin:${PATH}" DOCKER_STUB_LOG="${temp_root}/explicit.log" \
    bash "${temp_root}/a/scripts/testing/docker-test-compose.sh" ps
  assert_contains 'project=explicit' "${temp_root}/explicit.log"
  rm -rf "${temp_root}"
}

test_build_command_boundary
test_workspace_project_identity
test_docker_test_compose_can_force_rootful
test_docker_test_compose_defaults_to_rootless
test_docker_test_compose_enables_local_reranker_profile_for_local_provider

echo "docker_test_compose tests passed"
