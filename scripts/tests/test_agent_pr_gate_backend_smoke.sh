#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
temp_dir="$(mktemp -d)"
trap 'rm -rf "${temp_dir}"' EXIT
mkdir -p "${temp_dir}/bin"
cat > "${temp_dir}/bin/docker" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
printf '%s host=%s\n' "$*" "${DOCKER_HOST:-unset}" >> "${DOCKER_STUB_LOG}"
case "$*" in
  *'config --format json') printf '{"name":"fixture-project"}\n' ;;
  'image inspect '*) exit "${STUB_IMAGE_STATUS:-0}" ;;
  'run '*) exit "${STUB_RUN_STATUS:-0}" ;;
esac
STUB
chmod +x "${temp_dir}/bin/docker"
export PATH="${temp_dir}/bin:${PATH}"
export DOCKER_STUB_LOG="${temp_dir}/docker.log"
unset AGENT_GATE_UNIT_TEST_IMAGE AGENT_GATE_SKIP_TEST_SMOKE_IF_MISSING DOCKER_HOST
unset AI_CURATION_TEST_DOCKER_MODE AI_CURATION_ROOTLESS_DOCKER_HOST
export XDG_RUNTIME_DIR="${temp_dir}/runtime"
helper="${repo_root}/scripts/testing/agent_pr_gate_backend_smoke.sh"
bash "${helper}"
grep -Fq 'image inspect fixture-project-backend-unit-tests' "${DOCKER_STUB_LOG}"
grep -Fq 'build backend-unit-tests' "${DOCKER_STUB_LOG}"
grep -Fq "host=unix://${temp_dir}/runtime/docker.sock" "${DOCKER_STUB_LOG}"
grep -Fq 'fixture-project-backend-unit-tests python -m pytest tests/unit/test_exceptions.py -q' "${DOCKER_STUB_LOG}"

: > "${DOCKER_STUB_LOG}"
AGENT_GATE_UNIT_TEST_IMAGE=prebuilt-ci:latest bash "${helper}"
grep -Fq 'image inspect prebuilt-ci:latest host=unset' "${DOCKER_STUB_LOG}"
if grep -Fq 'compose' "${DOCKER_STUB_LOG}"; then
  echo 'Explicit prebuilt image must not invoke Compose' >&2
  exit 1
fi

expect_status() {
  local expected="$1"
  shift
  local actual=0
  "$@" >"${temp_dir}/output" 2>&1 || actual=$?
  [[ "${actual}" == "${expected}" ]]
}
: > "${DOCKER_STUB_LOG}"
expect_status 3 env STUB_IMAGE_STATUS=1 AGENT_GATE_SKIP_TEST_SMOKE_IF_MISSING=1 bash "${helper}"
expect_status 1 env STUB_IMAGE_STATUS=1 bash "${helper}"
if grep -Eq 'build backend-unit-tests|^run ' "${DOCKER_STUB_LOG}"; then
  echo 'Missing image must not build or run' >&2
  exit 1
fi
expect_status 1 env STUB_RUN_STATUS=1 AGENT_GATE_SKIP_TEST_SMOKE_IF_MISSING=1 bash "${helper}"
expect_status 1 env STUB_RUN_STATUS=3 AGENT_GATE_SKIP_TEST_SMOKE_IF_MISSING=1 bash "${helper}"
echo 'agent PR gate backend smoke tests passed'
