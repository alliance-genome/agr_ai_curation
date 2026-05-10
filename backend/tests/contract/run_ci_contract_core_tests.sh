#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SUITE="core"
VALIDATE_ONLY=0

usage() {
  cat <<'USAGE'
Usage: run_ci_contract_core_tests.sh [--validate-only] [--suite SUITE]

Suites:
  core                  Stable contract-core suite.
  alliance-domain-pack  Alliance domain-pack/LinkML contract suite, excluding live DB.
  alliance-live-db      Explicit opt-in live curation DB projection contract suite.
USAGE
}

while (($#)); do
  case "$1" in
    --validate-only)
      VALIDATE_ONLY=1
      shift
      ;;
    --suite)
      SUITE="${2:?--suite requires a value}"
      shift 2
      ;;
    --suite=*)
      SUITE="${1#--suite=}"
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

case "${SUITE}" in
  core)
    TEST_PATH_FILE="${SCRIPT_DIR}/.core-test-paths"
    ;;
  alliance-domain-pack)
    TEST_PATH_FILE="${SCRIPT_DIR}/.alliance-domain-pack-test-paths"
    ;;
  alliance-live-db)
    TEST_PATH_FILE="${SCRIPT_DIR}/.alliance-live-db-test-paths"
    ;;
  *)
    echo "Unknown contract test suite: ${SUITE}" >&2
    usage >&2
    exit 2
    ;;
esac

if [[ ! -f "${TEST_PATH_FILE}" ]]; then
  echo "Missing ${SUITE} path file: ${TEST_PATH_FILE}" >&2
  exit 1
fi

test_args=()
while IFS= read -r path; do
  [[ -z "${path}" || "${path}" =~ ^# ]] && continue
  if [[ ! -e "${BACKEND_DIR}/${path}" ]]; then
    echo "Missing ${SUITE} test path: ${path}" >&2
    exit 1
  fi
  test_args+=("${path}")
done < "${TEST_PATH_FILE}"

if [[ "${#test_args[@]}" -eq 0 ]]; then
  echo "${SUITE} path file contains no test paths: ${TEST_PATH_FILE}" >&2
  exit 1
fi

if [[ "${VALIDATE_ONLY}" == "1" ]]; then
  if [[ "${SUITE}" == "core" ]]; then
    echo "contract-core-path-check: ok"
  else
    echo "${SUITE}-path-check: ok"
  fi
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

if [[ "${SUITE}" == "alliance-live-db" ]] && ! truthy "${ALLIANCE_LIVE_DB_CONTRACT_TESTS:-0}"; then
  echo "alliance-live-db suite requires ALLIANCE_LIVE_DB_CONTRACT_TESTS=1" >&2
  exit 1
fi

cd "${BACKEND_DIR}"
python -m pytest \
  -q \
  --tb=short \
  --strict-markers \
  "${test_args[@]}"
