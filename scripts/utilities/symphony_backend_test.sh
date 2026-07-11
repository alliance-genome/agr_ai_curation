#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_HELPER="${SYMPHONY_BACKEND_TEST_COMPOSE_HELPER:-${REPO_ROOT}/scripts/testing/docker-test-compose.sh}"
LOCK_TIMEOUT_SECONDS="${SYMPHONY_BACKEND_TEST_LOCK_TIMEOUT_SECONDS:-1800}"
COLLISION_RETRY_COUNT="${SYMPHONY_BACKEND_TEST_COLLISION_RETRY_COUNT:-1}"
LOCK_ROOT="${SYMPHONY_BACKEND_TEST_LOCK_ROOT:-${XDG_RUNTIME_DIR:-/tmp}/agr-ai-curation-symphony-backend-tests}"
workspace_dir=""
repair_known_collision=0

usage() {
  cat <<'EOF'
Usage:
  symphony_backend_test.sh [wrapper options] [--] COMPOSE_ARGS...

Purpose:
  Serialize docker-compose.test.yml commands per workspace and derived
  daemon/project so concurrent Symphony validation does not collide on test
  containers or networks.

Wrapper options:
  --workspace-dir PATH       Workspace used to derive the lock (default: Git root or current directory).
  --lock-timeout-seconds N   Seconds to wait for the workspace lock (default: env or 1800).
  --repair-known-collision   On a recognized Compose container/network collision,
                             run down --remove-orphans and retry within the
                             configured retry limit. Cleanup is never automatic.
  --help                     Show this help.

Environment:
  SYMPHONY_BACKEND_TEST_LOCK_TIMEOUT_SECONDS
  SYMPHONY_BACKEND_TEST_COLLISION_RETRY_COUNT
  SYMPHONY_BACKEND_TEST_LOCK_ROOT

Compose context:
  --rootful and --rootless are supported and preserved during explicit repair.
  Custom project, project-directory, env-file, profile, and compose-file
  selectors are rejected so locking and cleanup cannot target different stacks.

Examples:
  bash scripts/utilities/symphony_backend_test.sh run --rm backend-unit-tests

  bash scripts/utilities/symphony_backend_test.sh run --rm backend-unit-tests \
    bash -lc "python -m pytest tests/unit/path/to/test.py -v --tb=short"

  bash scripts/utilities/symphony_backend_test.sh \
    --repair-known-collision -- run --rm backend-contract-tests

Exit codes:
  0   Compose command succeeded.
  2   Invalid wrapper arguments or configuration.
  75  Timed out waiting for the per-workspace lock.
  other
      Underlying Compose command or explicit repair failure.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      workspace_dir="${2:-}"
      shift 2
      ;;
    --lock-timeout-seconds)
      LOCK_TIMEOUT_SECONDS="${2:-}"
      shift 2
      ;;
    --repair-known-collision)
      repair_known_collision=1
      shift
      ;;
    --)
      shift
      break
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -eq 0 ]]; then
  echo "A docker compose command is required." >&2
  usage >&2
  exit 2
fi
if ! [[ "${LOCK_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "lock-timeout-seconds must be a non-negative integer." >&2
  exit 2
fi
if ! [[ "${COLLISION_RETRY_COUNT}" =~ ^[0-9]+$ ]]; then
  echo "SYMPHONY_BACKEND_TEST_COLLISION_RETRY_COUNT must be a non-negative integer." >&2
  exit 2
fi
if [[ ! -x "${COMPOSE_HELPER}" ]]; then
  echo "Compose helper is not executable: ${COMPOSE_HELPER}" >&2
  exit 2
fi

compose_args=("$@")
cleanup_context_args=()
docker_mode="${AI_CURATION_TEST_DOCKER_MODE:-rootless}"
context_index=0
case "${compose_args[0]:-}" in
  --rootful)
    docker_mode="rootful"
    cleanup_context_args+=(--rootful)
    context_index=1
    ;;
  --rootless)
    docker_mode="rootless"
    cleanup_context_args+=(--rootless)
    context_index=1
    ;;
esac

# The canonical helper owns the Compose file and project identity. Allowing
# callers to override either would require a different shared-lock and cleanup
# scope, so fail before running Docker rather than risk cleaning the wrong stack.
while [[ "${context_index}" -lt "${#compose_args[@]}" ]]; do
  context_arg="${compose_args[context_index]}"
  case "${context_arg}" in
    -p|--project-name|--project-directory|--env-file|--profile|-f|--file)
      echo "Unsupported Compose context selector for Symphony backend tests: ${context_arg}" >&2
      echo "Use the workspace-derived test project; custom project, directory, env-file, and compose-file selectors are not allowed." >&2
      exit 2
      ;;
    -p?*|-f?*)
      echo "Unsupported attached Compose context selector for Symphony backend tests: ${context_arg:0:2}" >&2
      echo "Use the workspace-derived test project and canonical docker-compose.test.yml file." >&2
      exit 2
      ;;
    --project-name=*|--project-directory=*|--env-file=*|--profile=*|--file=*)
      echo "Unsupported Compose context selector for Symphony backend tests: ${context_arg%%=*}" >&2
      echo "Use the workspace-derived test project; custom project, directory, env-file, and compose-file selectors are not allowed." >&2
      exit 2
      ;;
    --)
      break
      ;;
    -*)
      context_index=$((context_index + 1))
      ;;
    *)
      break
      ;;
  esac
done

if [[ -z "${workspace_dir}" ]]; then
  workspace_dir="$(git -C "${PWD}" rev-parse --show-toplevel 2>/dev/null || printf '%s' "${PWD}")"
fi
if [[ ! -d "${workspace_dir}" ]]; then
  echo "Workspace directory does not exist: ${workspace_dir}" >&2
  exit 2
fi
workspace_dir="$(cd "${workspace_dir}" && pwd -P)"

mkdir -p "${LOCK_ROOT}"
workspace_key="$(printf '%s' "${workspace_dir}" | sha256sum | awk '{print $1}')"
lock_file="${LOCK_ROOT}/${workspace_key}.lock"
compose_project="${COMPOSE_PROJECT_NAME:-$(basename "${workspace_dir}" | tr '[:upper:]' '[:lower:]')}"
project_key="$(printf '%s' "${docker_mode}:${compose_project}" | sha256sum | awk '{print $1}')"
project_lock_file="${LOCK_ROOT}/project-${project_key}.lock"
exec {lock_fd}> "${lock_file}"

echo "SYMPHONY_BACKEND_TEST_LOCK_STATUS=waiting" >&2
echo "SYMPHONY_BACKEND_TEST_WORKSPACE=${workspace_dir}" >&2
echo "SYMPHONY_BACKEND_TEST_LOCK_FILE=${lock_file}" >&2
if ! flock -w "${LOCK_TIMEOUT_SECONDS}" "${lock_fd}"; then
  echo "SYMPHONY_BACKEND_TEST_STATUS=lock_timeout" >&2
  echo "SYMPHONY_BACKEND_TEST_LOCK_STATUS=timeout" >&2
  exit 75
fi
echo "SYMPHONY_BACKEND_TEST_LOCK_STATUS=acquired" >&2

exec {project_lock_fd}> "${project_lock_file}"
echo "SYMPHONY_BACKEND_TEST_PROJECT_LOCK_FILE=${project_lock_file}" >&2
if ! flock -w "${LOCK_TIMEOUT_SECONDS}" "${project_lock_fd}"; then
  echo "SYMPHONY_BACKEND_TEST_STATUS=project_lock_timeout" >&2
  echo "SYMPHONY_BACKEND_TEST_PROJECT_LOCK_STATUS=timeout" >&2
  exit 75
fi
echo "SYMPHONY_BACKEND_TEST_PROJECT_LOCK_STATUS=acquired" >&2

output_file="$(mktemp "${TMPDIR:-/tmp}/symphony-backend-test-output-XXXXXX.log")"
cleanup() {
  rm -f "${output_file}"
}
trap cleanup EXIT

known_collision() {
  rg -qi \
    -e 'container name .* is already in use' \
    -e 'conflict.*container name' \
    -e 'network .* already exists' \
    -e 'failed to create network.*already exists' \
    -e 'network .* has active endpoints' \
    "${output_file}"
}

run_compose() {
  : > "${output_file}"
  set +e
  (
    cd "${workspace_dir}"
    "${COMPOSE_HELPER}" "$@"
  ) 2>&1 | tee "${output_file}"
  local compose_rc=${PIPESTATUS[0]}
  return "${compose_rc}"
}

attempt=0
while true; do
  set +e
  run_compose "${compose_args[@]}"
  compose_rc=$?
  set -e

  if [[ "${compose_rc}" -eq 0 ]]; then
    echo "SYMPHONY_BACKEND_TEST_STATUS=ok" >&2
    echo "SYMPHONY_BACKEND_TEST_ATTEMPTS=$((attempt + 1))" >&2
    exit 0
  fi

  collision_recognized=false
  if known_collision; then
    collision_recognized=true
  fi
  if [[ "${repair_known_collision}" -ne 1 || "${attempt}" -ge "${COLLISION_RETRY_COUNT}" || "${collision_recognized}" != "true" ]]; then
    echo "SYMPHONY_BACKEND_TEST_STATUS=failed" >&2
    echo "SYMPHONY_BACKEND_TEST_EXIT_STATUS=${compose_rc}" >&2
    echo "SYMPHONY_BACKEND_TEST_COLLISION_RECOGNIZED=${collision_recognized}" >&2
    if [[ "${collision_recognized}" == "true" && "${repair_known_collision}" -ne 1 ]]; then
      echo "SYMPHONY_BACKEND_TEST_REPAIR_HINT=Rerun with --repair-known-collision only after confirming no raw Compose job is active in this workspace." >&2
    fi
    exit "${compose_rc}"
  fi

  echo "SYMPHONY_BACKEND_TEST_STATUS=repairing_known_collision" >&2
  set +e
  (
    cd "${workspace_dir}"
    "${COMPOSE_HELPER}" "${cleanup_context_args[@]}" down --remove-orphans
  )
  cleanup_rc=$?
  set -e
  if [[ "${cleanup_rc}" -ne 0 ]]; then
    echo "SYMPHONY_BACKEND_TEST_STATUS=repair_failed" >&2
    echo "SYMPHONY_BACKEND_TEST_REPAIR_EXIT_STATUS=${cleanup_rc}" >&2
    exit "${cleanup_rc}"
  fi

  attempt=$((attempt + 1))
  echo "SYMPHONY_BACKEND_TEST_RETRY=${attempt}/${COLLISION_RETRY_COUNT}" >&2
done
