#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
IGNORE_FILE="${SCRIPT_DIR}/.ci-ignore-paths"

if [[ ! -f "${IGNORE_FILE}" ]]; then
  echo "Missing ignore file: ${IGNORE_FILE}" >&2
  exit 1
fi

ignore_args=()
while IFS= read -r path; do
  [[ -z "${path}" || "${path}" =~ ^# ]] && continue
  if [[ ! -e "${BACKEND_DIR}/${path}" ]]; then
    echo "Missing ignored path: ${path}" >&2
    exit 1
  fi
  ignore_args+=("--ignore=${path}")
done < "${IGNORE_FILE}"

if [[ "${1:-}" == "--validate-only" ]]; then
  echo "ignore-path-check: ok"
  exit 0
fi

cd "${BACKEND_DIR}"
python -m pytest \
  tests/unit/ \
  -v \
  --tb=short \
  --strict-markers \
  --cov=src \
  --cov-report=term-missing \
  --cov-report=html \
  --cov-report=xml:coverage.xml \
  --cov-fail-under=50 \
  "${ignore_args[@]}"
