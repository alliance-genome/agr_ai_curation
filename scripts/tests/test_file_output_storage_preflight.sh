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

if [[ "${DOCKER_STUB_MODE:-success}" == "fail" ]]; then
  cat <<'JSON'
{
  "status": "fail",
  "base_path": "/app/file_outputs",
  "errors": ["temp_processing: PermissionError: denied"]
}
JSON
  exit 1
fi

cat <<'JSON'
{
  "status": "pass",
  "base_path": "/app/file_outputs",
  "errors": [],
  "save_output": {
    "result": "pass"
  }
}
JSON
EOF

  chmod +x "${stub_dir}/docker"
}

test_preflight_writes_evidence_on_success() {
  local temp_root stub_dir docker_log output_dir output_log
  temp_root="$(mktemp -d)"
  stub_dir="${temp_root}/stubbin"
  docker_log="${temp_root}/docker.log"
  output_dir="${temp_root}/out"
  output_log="${temp_root}/output.log"

  make_stub_docker "${stub_dir}"

  (
    cd "${REPO_ROOT}"
    export PATH="${stub_dir}:${PATH}"
    export DOCKER_STUB_LOG="${docker_log}"
    export EXPORT_STORAGE_PREFLIGHT_OUT_DIR="${output_dir}"
    "${REPO_ROOT}/scripts/testing/file_output_storage_preflight.sh" > "${output_log}" 2>&1
  )

  assert_contains "args=compose exec -T backend python -" "${docker_log}"
  assert_contains "Evidence file:" "${output_log}"

  local evidence_file
  evidence_file="$(find "${output_dir}" -maxdepth 1 -name 'file_output_storage_preflight_*.json' | head -n 1)"
  if [[ -z "${evidence_file}" ]]; then
    echo "Expected evidence file to be created in ${output_dir}" >&2
    exit 1
  fi

  assert_contains "\"status\": \"pass\"" "${evidence_file}"
  assert_contains "\"base_path\": \"/app/file_outputs\"" "${evidence_file}"
}

test_preflight_preserves_evidence_on_failure() {
  local temp_root stub_dir docker_log output_dir output_log
  temp_root="$(mktemp -d)"
  stub_dir="${temp_root}/stubbin"
  docker_log="${temp_root}/docker.log"
  output_dir="${temp_root}/out"
  output_log="${temp_root}/output.log"

  make_stub_docker "${stub_dir}"

  if (
    cd "${REPO_ROOT}"
    export PATH="${stub_dir}:${PATH}"
    export DOCKER_STUB_LOG="${docker_log}"
    export DOCKER_STUB_MODE="fail"
    export EXPORT_STORAGE_PREFLIGHT_OUT_DIR="${output_dir}"
    "${REPO_ROOT}/scripts/testing/file_output_storage_preflight.sh" > "${output_log}" 2>&1
  ); then
    echo "Expected preflight script to fail when docker stub exits nonzero" >&2
    exit 1
  fi

  assert_contains "args=compose exec -T backend python -" "${docker_log}"

  local evidence_file
  evidence_file="$(find "${output_dir}" -maxdepth 1 -name 'file_output_storage_preflight_*.json' | head -n 1)"
  if [[ -z "${evidence_file}" ]]; then
    echo "Expected failure evidence file to be created in ${output_dir}" >&2
    exit 1
  fi

  assert_contains "\"status\": \"fail\"" "${evidence_file}"
  assert_contains "PermissionError: denied" "${evidence_file}"
}

test_preflight_writes_evidence_on_success
test_preflight_preserves_evidence_on_failure

echo "file_output_storage_preflight tests passed"
