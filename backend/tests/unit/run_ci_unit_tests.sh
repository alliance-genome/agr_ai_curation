#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_ROOT="$(cd "${BACKEND_DIR}/.." && pwd)"
IGNORE_FILE="${SCRIPT_DIR}/.ci-ignore-paths"
DOMAIN_ENVELOPE_RELEASE_PATH_FILE="${SCRIPT_DIR}/.domain-envelope-release-test-paths"
SUITE="all"
VALIDATE_ONLY=0
UNIT_TEST_WORKERS="${BACKEND_UNIT_TEST_WORKERS:-0}"
UNIT_TEST_DURATIONS="${BACKEND_UNIT_TEST_DURATIONS:-25}"
UNIT_TEST_JUNIT_XML="${BACKEND_UNIT_JUNIT_XML:-file_outputs/ci/backend-unit-junit.xml}"
UNIT_TEST_SUMMARY_FILE="${BACKEND_UNIT_SUMMARY_FILE:-file_outputs/ci/backend-unit-summary.md}"

usage() {
  cat <<'USAGE'
Usage: run_ci_unit_tests.sh [--validate-only] [--suite SUITE] [--workers N] [--durations N] [--junitxml PATH]

Suites:
  all                       Full unit suite with CI ignore paths and coverage.
  domain-envelope-release   Offline 0.7.0 domain-envelope release-gate unit slice.

Options:
  --workers N               Run the full unit suite with pytest-xdist workers.
                            Use 0 for serial execution. Defaults to
                            BACKEND_UNIT_TEST_WORKERS or 0.
  --durations N             Report the slowest N tests. Defaults to
                            BACKEND_UNIT_TEST_DURATIONS or 25.
  --junitxml PATH           Write JUnit timing/report XML for the full suite.
                            Defaults to BACKEND_UNIT_JUNIT_XML or
                            file_outputs/ci/backend-unit-junit.xml.
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
    --workers)
      UNIT_TEST_WORKERS="${2:?--workers requires a value}"
      shift 2
      ;;
    --workers=*)
      UNIT_TEST_WORKERS="${1#--workers=}"
      shift
      ;;
    --durations)
      UNIT_TEST_DURATIONS="${2:?--durations requires a value}"
      shift 2
      ;;
    --durations=*)
      UNIT_TEST_DURATIONS="${1#--durations=}"
      shift
      ;;
    --junitxml)
      UNIT_TEST_JUNIT_XML="${2:?--junitxml requires a value}"
      shift 2
      ;;
    --junitxml=*)
      UNIT_TEST_JUNIT_XML="${1#--junitxml=}"
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

case "${UNIT_TEST_WORKERS}" in
  ''|*[!0-9]*)
    echo "--workers must be a non-negative integer, got: ${UNIT_TEST_WORKERS}" >&2
    exit 2
    ;;
esac

case "${UNIT_TEST_DURATIONS}" in
  ''|*[!0-9]*)
    echo "--durations must be a non-negative integer, got: ${UNIT_TEST_DURATIONS}" >&2
    exit 2
    ;;
esac

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

assert_openai_agents_pin() {
  REPO_ROOT="${REPO_ROOT}" python - <<'PY'
import importlib.util
import os
from pathlib import Path
import sys

repo_root = Path(os.environ["REPO_ROOT"])
module_path = repo_root / "scripts" / "testing" / "dev_release_smoke.py"
spec = importlib.util.spec_from_file_location("dev_release_smoke", module_path)
if spec is None or spec.loader is None:
    raise SystemExit(f"Unable to load smoke module from {module_path}")

smoke = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)

try:
    payload = smoke.check_sdk_version_pin(checks=[], repo_root=repo_root)
except smoke.SmokeFailure as exc:
    raise SystemExit(str(exc)) from None

print(f"openai-agents-pin-check: ok ({payload['installed_version']})")
PY
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
    mkdir -p "$(dirname "${UNIT_TEST_JUNIT_XML}")" "$(dirname "${UNIT_TEST_SUMMARY_FILE}")"
    assert_openai_agents_pin

    pytest_args=(
      tests/unit/
      -v
      --tb=short
      --strict-markers
      "--durations=${UNIT_TEST_DURATIONS}"
      "--junitxml=${UNIT_TEST_JUNIT_XML}"
      --cov=src
      --cov-report=term-missing
      --cov-report=html
      --cov-report=xml:coverage.xml
      --cov-fail-under=50
      "${ignore_args[@]}"
    )

    if ((UNIT_TEST_WORKERS > 0)); then
      pytest_args+=(-n "${UNIT_TEST_WORKERS}" --dist loadscope)
    fi

    start_seconds="${SECONDS}"
    set +e
    python -m pytest "${pytest_args[@]}"
    pytest_status="$?"
    set -e
    duration_seconds="$((SECONDS - start_seconds))"

    {
      echo "### Backend unit pytest"
      echo ""
      echo "- Suite: \`${SUITE}\`"
      echo "- Workers: \`${UNIT_TEST_WORKERS}\`"
      echo "- Distribution: \`$([[ "${UNIT_TEST_WORKERS}" == "0" ]] && echo serial || echo loadscope)\`"
      echo "- Duration: \`${duration_seconds}s\`"
      echo "- Exit status: \`${pytest_status}\`"
      echo "- Slow-test report: \`--durations=${UNIT_TEST_DURATIONS}\`"
      echo "- JUnit report: \`${UNIT_TEST_JUNIT_XML}\`"
      echo ""
    } >> "${UNIT_TEST_SUMMARY_FILE}"

    exit "${pytest_status}"
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
