#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TEST_PATH_FILE_INPUT="tests/contract/.core-test-paths"
SUITE_LABEL="contract-core"
VALIDATE_ONLY=0
REQUIRED_TRUTHY_ENVS=()

usage() {
  cat <<'USAGE'
Usage: run_ci_contract_core_tests.sh [--validate-only] [--path-file PATH] [--suite-label LABEL] [--require-truthy-env NAME]

Options:
  --path-file PATH           Test path manifest, relative to backend/ unless absolute.
                             Defaults to tests/contract/.core-test-paths.
  --suite-label LABEL        Display label for validation and error output.
                             Defaults to contract-core.
  --require-truthy-env NAME  Require NAME to be truthy before running tests.
                             Truthy values: 1, true, TRUE, yes, YES, on, ON.
USAGE
}

while (($#)); do
  case "$1" in
    --validate-only)
      VALIDATE_ONLY=1
      shift
      ;;
    --path-file)
      TEST_PATH_FILE_INPUT="${2:?--path-file requires a value}"
      shift 2
      ;;
    --path-file=*)
      TEST_PATH_FILE_INPUT="${1#--path-file=}"
      shift
      ;;
    --suite-label)
      SUITE_LABEL="${2:?--suite-label requires a value}"
      shift 2
      ;;
    --suite-label=*)
      SUITE_LABEL="${1#--suite-label=}"
      shift
      ;;
    --require-truthy-env)
      REQUIRED_TRUTHY_ENVS+=("${2:?--require-truthy-env requires a value}")
      shift 2
      ;;
    --require-truthy-env=*)
      REQUIRED_TRUTHY_ENVS+=("${1#--require-truthy-env=}")
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${TEST_PATH_FILE_INPUT}" ]]; then
  echo "--path-file cannot be empty" >&2
  exit 2
fi

if [[ -z "${SUITE_LABEL}" ]]; then
  echo "--suite-label cannot be empty" >&2
  exit 2
fi

if [[ "${TEST_PATH_FILE_INPUT}" == /* ]]; then
  TEST_PATH_FILE="${TEST_PATH_FILE_INPUT}"
else
  TEST_PATH_FILE="${BACKEND_DIR}/${TEST_PATH_FILE_INPUT}"
fi

if [[ ! -f "${TEST_PATH_FILE}" ]]; then
  echo "Missing ${SUITE_LABEL} path file: ${TEST_PATH_FILE}" >&2
  exit 1
fi

test_args=()
while IFS= read -r path; do
  [[ -z "${path}" || "${path}" =~ ^# ]] && continue
  if [[ ! -e "${BACKEND_DIR}/${path}" ]]; then
    echo "Missing ${SUITE_LABEL} test path: ${path}" >&2
    exit 1
  fi
  test_args+=("${path}")
done < "${TEST_PATH_FILE}"

if [[ "${#test_args[@]}" -eq 0 ]]; then
  echo "${SUITE_LABEL} path file contains no test paths: ${TEST_PATH_FILE}" >&2
  exit 1
fi

if [[ "${VALIDATE_ONLY}" == "1" ]]; then
  echo "${SUITE_LABEL}-path-check: ok"
  exit 0
fi

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

for required_env in "${REQUIRED_TRUTHY_ENVS[@]}"; do
  if [[ ! "${required_env}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "Invalid required env var name: ${required_env}" >&2
    exit 2
  fi

  if ! truthy "${!required_env:-}"; then
    echo "${SUITE_LABEL} suite requires ${required_env}=1" >&2
    exit 1
  fi
done

cd "${BACKEND_DIR}"
python -m pytest \
  -q \
  --tb=short \
  --strict-markers \
  "${test_args[@]}"
