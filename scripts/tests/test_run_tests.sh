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

if [[ "$*" == *" run --rm backend-unit-tests"* ]]; then
  echo "simulated unit test failure" >&2
  exit 3
fi
EOF

  chmod +x "${stub_dir}/docker"
}

test_run_tests_cleans_up_on_failure() {
  local temp_root stub_dir output docker_log
  temp_root="$(mktemp -d)"
  stub_dir="${temp_root}/stubbin"
  output="${temp_root}/output.txt"
  docker_log="${temp_root}/docker.log"

  make_stub_docker "${stub_dir}"
  printf 'stale\n' > "${temp_root}/.test-stack.env"

  if (
    cd "${temp_root}"
    export PATH="${stub_dir}:${PATH}"
    export DOCKER_STUB_LOG="${docker_log}"
    "${REPO_ROOT}/scripts/testing/run-tests.sh" unit > "${output}" 2>&1
  ); then
    echo "Expected run-tests.sh unit to fail when backend-unit-tests fails" >&2
    exit 1
  fi

  assert_contains "args=compose -f docker-compose.test.yml run --rm backend-unit-tests" "${docker_log}"
  assert_contains "args=compose -f docker-compose.test.yml down" "${docker_log}"
  if [[ -e "${temp_root}/.test-stack.env" ]]; then
    echo "Expected .test-stack.env to be removed during cleanup" >&2
    exit 1
  fi
}

test_run_tests_cleans_up_on_failure

echo "run_tests script tests passed"
