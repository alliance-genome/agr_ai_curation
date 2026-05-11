#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
IGNORE_FILE="${SCRIPT_DIR}/.ci-ignore-paths"
DOMAIN_ENVELOPE_RELEASE_PATH_FILE="${SCRIPT_DIR}/.domain-envelope-release-test-paths"
SUITE="all"
VALIDATE_ONLY=0

usage() {
  cat <<'USAGE'
Usage: run_ci_unit_tests.sh [--validate-only] [--suite SUITE]

Suites:
  all                       Full unit suite with CI ignore paths and coverage.
  domain-envelope-release   Offline 0.7.0 domain-envelope release-gate unit slice.
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

load_path_file() {
  local path_file="$1"
  local label="$2"
  local -n out_paths="$3"

  if [[ ! -f "${path_file}" ]]; then
    echo "Missing ${label} path file: ${path_file}" >&2
    exit 1
  fi

  out_paths=()
  while IFS= read -r path; do
    [[ -z "${path}" || "${path}" =~ ^# ]] && continue
    if [[ ! -e "${BACKEND_DIR}/${path}" ]]; then
      echo "Missing ${label} path: ${path}" >&2
      exit 1
    fi
    out_paths+=("${path}")
  done < "${path_file}"

  if [[ "${#out_paths[@]}" -eq 0 ]]; then
    echo "${label} path file contains no test paths: ${path_file}" >&2
    exit 1
  fi
}

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

case "${SUITE}" in
  all)
    if [[ "${VALIDATE_ONLY}" == "1" ]]; then
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
    ;;
  domain-envelope-release)
    domain_envelope_paths=()
    load_path_file "${DOMAIN_ENVELOPE_RELEASE_PATH_FILE}" \
      "domain-envelope release-gate" \
      domain_envelope_paths

    if [[ "${VALIDATE_ONLY}" == "1" ]]; then
      echo "domain-envelope-release-path-check: ok"
      exit 0
    fi

    cd "${BACKEND_DIR}"
    python -m pytest \
      -q \
      --tb=short \
      --strict-markers \
      "${domain_envelope_paths[@]}"
    ;;
  *)
    echo "Unknown unit test suite: ${SUITE}" >&2
    usage >&2
    exit 2
    ;;
esac
