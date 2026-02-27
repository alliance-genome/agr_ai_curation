#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TEST_PATH_FILE="${SCRIPT_DIR}/.core-test-paths"

if [[ ! -f "${TEST_PATH_FILE}" ]]; then
  echo "Missing contract-core path file: ${TEST_PATH_FILE}" >&2
  exit 1
fi

test_args=()
while IFS= read -r path; do
  [[ -z "${path}" || "${path}" =~ ^# ]] && continue
  if [[ ! -e "${BACKEND_DIR}/${path}" ]]; then
    echo "Missing contract-core test path: ${path}" >&2
    exit 1
  fi
  test_args+=("${path}")
done < "${TEST_PATH_FILE}"

if [[ "${1:-}" == "--validate-only" ]]; then
  echo "contract-core-path-check: ok"
  exit 0
fi

cd "${BACKEND_DIR}"
python -m pytest \
  -q \
  --tb=short \
  --strict-markers \
  "${test_args[@]}"
