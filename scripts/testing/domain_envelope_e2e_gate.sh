#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
PATH_FILE="${BACKEND_DIR}/tests/integration/.domain-envelope-e2e-test-paths"
VALIDATE_ONLY=0

usage() {
  cat <<'USAGE'
Usage: domain_envelope_e2e_gate.sh [--validate-only]

Runs the 0.7.0 domain-envelope end-to-end integration gate:
- persisted envelope extraction -> review/materialization -> export/submission paths,
- curation workspace session API visibility,
- retained legacy workspace one-off migration verification.
USAGE
}

while (($#)); do
  case "$1" in
    --validate-only)
      VALIDATE_ONLY=1
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

if [[ ! -f "${PATH_FILE}" ]]; then
  echo "Missing domain-envelope e2e path file: ${PATH_FILE}" >&2
  exit 1
fi

integration_paths=()
persistence_paths=()
while IFS= read -r path; do
  [[ -z "${path}" || "${path}" =~ ^# ]] && continue
  if [[ ! -e "${BACKEND_DIR}/${path}" ]]; then
    echo "Missing domain-envelope e2e test path: ${path}" >&2
    exit 1
  fi
  if [[ "${path}" == tests/integration/persistence/* ]]; then
    persistence_paths+=("${path}")
  else
    integration_paths+=("${path}")
  fi
done < "${PATH_FILE}"

if [[ "${#integration_paths[@]}" -eq 0 && "${#persistence_paths[@]}" -eq 0 ]]; then
  echo "Domain-envelope e2e path file contains no test paths: ${PATH_FILE}" >&2
  exit 1
fi

if [[ "${VALIDATE_ONLY}" == "1" ]]; then
  echo "domain-envelope-e2e-path-check: ok"
  exit 0
fi

"${SCRIPT_DIR}/prepare-test-stack.sh"

if [[ "${#integration_paths[@]}" -gt 0 ]]; then
  "${SCRIPT_DIR}/docker-test-compose.sh" run --rm backend-integration-tests \
    python -m pytest -q --tb=short --strict-markers "${integration_paths[@]}"
fi

if [[ "${#persistence_paths[@]}" -gt 0 ]]; then
  # Retained-legacy migration tests start from an empty database and run the
  # one-off migration path themselves. The integration stack above is migrated
  # for API tests, so recreate Postgres before entering the persistence suite.
  "${SCRIPT_DIR}/docker-test-compose.sh" rm -sf postgres-test >/dev/null
  "${SCRIPT_DIR}/docker-test-compose.sh" up -d --wait postgres-test redis-test weaviate-test

  "${SCRIPT_DIR}/docker-test-compose.sh" run --rm backend-persistence-tests \
    python -m pytest -q --tb=short --strict-markers "${persistence_paths[@]}"
fi
