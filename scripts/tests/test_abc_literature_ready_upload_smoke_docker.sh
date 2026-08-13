#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WRAPPER="${REPO_ROOT}/scripts/testing/abc_literature_ready_upload_smoke_docker.sh"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

assert_not_contains() {
  local unexpected="$1"
  local actual="$2"
  if [[ "${actual}" == *"${unexpected}"* ]]; then
    echo "Expected output not to contain '${unexpected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

mkdir -p "${TMP_DIR}/bin" "${TMP_DIR}/empty-aws" "${TMP_DIR}/file_outputs"
cat > "${TMP_DIR}/bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${DOCKER_STUB_LOG:?}"
if [[ "$*" == *" ps --services --status running"* ]]; then
  printf 'backend\n'
fi
EOF
chmod +x "${TMP_DIR}/bin/docker"
printf '[profile unrelated]\nregion = us-east-1\n' > "${TMP_DIR}/empty-aws/config"

run_wrapper() {
  local env_file="$1"
  shift
  PATH="${TMP_DIR}/bin:${PATH}" \
  HOME="${TMP_DIR}/home" \
  DOCKER_STUB_LOG="${TMP_DIR}/docker.log" \
  ABC_LITERATURE_READY_UPLOAD_SMOKE_ENV_FILE="${env_file}" \
  ABC_LITERATURE_READY_UPLOAD_SMOKE_AWS_DIR="${TMP_DIR}/empty-aws" \
  ABC_LITERATURE_READY_UPLOAD_SMOKE_COMPOSE_FILE="docker-compose.yml" \
  ABC_LITERATURE_READY_UPLOAD_SMOKE_DOCKER_USER="1000:1000" \
    "${WRAPPER}" "$@"
}

test_rejects_curator_token_cli_without_echoing_secret() {
  local env_file output status token
  env_file="${TMP_DIR}/cli.env"
  token="secret-cli-token"
  : > "${env_file}"

  set +e
  output="$(run_wrapper "${env_file}" --curator-id-token "${token}" 2>&1)"
  status=$?
  set -e

  if [[ "${status}" != "2" ]]; then
    echo "Expected curator-token CLI rejection to exit 2, got ${status}" >&2
    exit 1
  fi
  assert_contains "does not accept --curator-id-token" "${output}"
  assert_contains "ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN" "${output}"
  assert_not_contains "${token}" "${output}"
}

test_add_literature_wrapper_names_its_canonical_token_env() {
  local env_file output status token
  env_file="${TMP_DIR}/add-cli.env"
  token="secret-add-cli-token"
  : > "${env_file}"

  set +e
  output="$(
    ABC_LITERATURE_READY_UPLOAD_SMOKE_SCRIPT_PATH=/app/scripts/testing/add_literature_upload_smoke.py \
      run_wrapper "${env_file}" --curator-id-token="${token}" 2>&1
  )"
  status=$?
  set -e

  if [[ "${status}" != "2" ]]; then
    echo "Expected Add Literature curator-token CLI rejection to exit 2, got ${status}" >&2
    exit 1
  fi
  assert_contains "ADD_LITERATURE_UPLOAD_SMOKE_CURATOR_ID_TOKEN" "${output}"
  assert_contains "ADD_LITERATURE_UPLOAD_SMOKE_ENV_FILE" "${output}"
  assert_not_contains "${token}" "${output}"
}

test_token_env_file_skips_aws_setup() {
  local env_file docker_log
  env_file="${TMP_DIR}/token.env"
  docker_log="${TMP_DIR}/docker.log"
  printf 'ABC_LITERATURE_READY_UPLOAD_SMOKE_CURATOR_ID_TOKEN=secret-env-token\n' > "${env_file}"
  : > "${docker_log}"

  run_wrapper "${env_file}" >/dev/null

  assert_contains " compose -f " " $(<"${docker_log}")"
  assert_not_contains "AWS_PROFILE=" "$(<"${docker_log}")"
  assert_not_contains "abc-ready-upload-smoke-aws" "$(<"${docker_log}")"
  assert_not_contains "secret-env-token" "$(<"${docker_log}")"
}

test_missing_token_and_profile_fails_clearly() {
  local env_file output status
  env_file="${TMP_DIR}/missing.env"
  : > "${env_file}"

  set +e
  output="$(run_wrapper "${env_file}" 2>&1)"
  status=$?
  set -e

  if [[ "${status}" != "2" ]]; then
    echo "Expected missing token/profile preflight to exit 2, got ${status}" >&2
    exit 1
  fi
  assert_contains "An AWS profile is required when no curator ID token is configured." "${output}"
}

test_rejects_curator_token_cli_without_echoing_secret
test_add_literature_wrapper_names_its_canonical_token_env
test_token_env_file_skips_aws_setup
test_missing_token_and_profile_fails_clearly

echo "abc_literature_ready_upload_smoke_docker tests passed"
