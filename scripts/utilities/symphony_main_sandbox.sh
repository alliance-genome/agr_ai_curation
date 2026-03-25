#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
REPO_NAME="$(basename "${REPO_ROOT}")"

usage() {
  cat <<'EOF'
Usage:
  symphony_main_sandbox.sh <prepare|cleanup> [options]

Options:
  --sandbox-dir DIR      Sandbox checkout directory
  --compose-project NAME Docker Compose project name
  --remote NAME          Git remote to fetch (default: origin)
  --branch NAME          Branch to sync (default: main)
  --review-host HOST     Override review host shown in URLs
  --dry-run              Print the plan without mutating anything
  -h, --help             Show this help
EOF
}

action="${1:-}"
if [[ -z "${action}" || "${action}" == "-h" || "${action}" == "--help" ]]; then
  usage
  exit 0
fi
shift || true

SANDBOX_ROOT_DEFAULT="${SYMPHONY_MAIN_SANDBOX_ROOT:-${HOME}/.symphony/sandboxes/${REPO_NAME}}"
SANDBOX_DIR="${SANDBOX_ROOT_DEFAULT}/main"
COMPOSE_PROJECT="${SYMPHONY_MAIN_SANDBOX_COMPOSE_PROJECT:-agrmainsandbox}"
REMOTE_NAME="${SYMPHONY_MAIN_SANDBOX_REMOTE:-origin}"
BRANCH_NAME="${SYMPHONY_MAIN_SANDBOX_BRANCH:-main}"
REVIEW_HOST="${REVIEW_HOST:-${SYMPHONY_REVIEW_HOST:-}}"
DRY_RUN=0

FRONTEND_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_FRONTEND_PORT:-}"
BACKEND_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_BACKEND_PORT:-}"
POSTGRES_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_POSTGRES_PORT:-54330}"
REDIS_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_REDIS_PORT:-63830}"
LANGFUSE_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_LANGFUSE_PORT:-33330}"
WEAVIATE_HTTP_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_WEAVIATE_HTTP_PORT:-18430}"
WEAVIATE_GRPC_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_WEAVIATE_GRPC_PORT:-15430}"
FRONTEND_PORT_RANGE_START="${SYMPHONY_MAIN_SANDBOX_FRONTEND_PORT_START:-3900}"
FRONTEND_PORT_RANGE_END="${SYMPHONY_MAIN_SANDBOX_FRONTEND_PORT_END:-3999}"
BACKEND_PORT_OFFSET=5000
PERMISSION_FIX_IMAGE="${SYMPHONY_MAIN_SANDBOX_PERMISSION_FIX_IMAGE:-public.ecr.aws/docker/library/python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sandbox-dir)
      SANDBOX_DIR="${2:-}"
      shift 2
      ;;
    --compose-project)
      COMPOSE_PROJECT="${2:-}"
      shift 2
      ;;
    --remote)
      REMOTE_NAME="${2:-}"
      shift 2
      ;;
    --branch)
      BRANCH_NAME="${2:-}"
      shift 2
      ;;
    --review-host)
      REVIEW_HOST="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
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

SANDBOX_DIR="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "${SANDBOX_DIR}")"
SANDBOX_ROOT="$(dirname "${SANDBOX_DIR}")"
TARGET_REF="refs/remotes/${REMOTE_NAME}/${BRANCH_NAME}"

kv() {
  printf '%s=%s\n' "$1" "$2"
}

require_repo() {
  if ! git -C "${REPO_ROOT}" rev-parse --git-dir >/dev/null 2>&1; then
    echo "Repository root is not a git checkout: ${REPO_ROOT}" >&2
    exit 2
  fi
}

run_and_print() {
  local output_file
  output_file="$(mktemp)"

  set +e
  "$@" >"${output_file}" 2>&1
  local status=$?
  set -e

  cat "${output_file}"
  rm -f "${output_file}"
  return "${status}"
}

filter_runtime_git_status() {
  while IFS= read -r line; do
    case "${line}" in
      "?? .symphony/"*|"?? .symphony-docker-config"*|"?? scripts/local_db_tunnel_env.sh"|"?? scripts/utilities/symphony_main_sandbox.sh")
        ;;
      *)
        printf '%s\n' "${line}"
        ;;
    esac
  done
}

port_available() {
  python3 - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError:
        sys.exit(1)

sys.exit(0)
PY
}

ensure_review_ports() {
  if [[ -n "${FRONTEND_HOST_PORT}" && -n "${BACKEND_HOST_PORT}" ]]; then
    return 0
  fi

  if [[ -n "${FRONTEND_HOST_PORT}" || -n "${BACKEND_HOST_PORT}" ]]; then
    echo "Set both SYMPHONY_MAIN_SANDBOX_FRONTEND_PORT and SYMPHONY_MAIN_SANDBOX_BACKEND_PORT, or neither." >&2
    exit 2
  fi

  if [[ "${DRY_RUN}" == "1" ]]; then
    FRONTEND_HOST_PORT="${FRONTEND_PORT_RANGE_START}"
    BACKEND_HOST_PORT="$((FRONTEND_PORT_RANGE_START + BACKEND_PORT_OFFSET))"
    return 0
  fi

  local frontend_port
  local backend_port
  for frontend_port in $(seq "${FRONTEND_PORT_RANGE_START}" "${FRONTEND_PORT_RANGE_END}"); do
    backend_port="$((frontend_port + BACKEND_PORT_OFFSET))"
    if port_available "${frontend_port}" && port_available "${backend_port}"; then
      FRONTEND_HOST_PORT="${frontend_port}"
      BACKEND_HOST_PORT="${backend_port}"
      return 0
    fi
  done

  echo "Unable to find a free proxied port pair in ${FRONTEND_PORT_RANGE_START}-${FRONTEND_PORT_RANGE_END} and $((FRONTEND_PORT_RANGE_START + BACKEND_PORT_OFFSET))-$((FRONTEND_PORT_RANGE_END + BACKEND_PORT_OFFSET))." >&2
  exit 2
}

stop_existing_runtime() {
  if [[ ! -d "${SANDBOX_DIR}" ]]; then
    return 0
  fi

  if [[ -f "${SANDBOX_DIR}/docker-compose.yml" ]]; then
    run_and_print bash -lc \
      "cd \"${SANDBOX_DIR}\" && docker compose -f docker-compose.yml -p \"${COMPOSE_PROJECT}\" down --remove-orphans -v" || true
  fi

  if [[ -x "${SANDBOX_DIR}/scripts/utilities/symphony_local_db_tunnel_stop.sh" ]]; then
    "${SANDBOX_DIR}/scripts/utilities/symphony_local_db_tunnel_stop.sh" \
      --workspace-dir "${SANDBOX_DIR}" >/dev/null 2>&1 || true
  fi
}

repair_workspace_permissions() {
  if [[ ! -d "${SANDBOX_DIR}" ]] || ! command -v docker >/dev/null 2>&1; then
    return 0
  fi

  docker run --rm \
    -v "${SANDBOX_DIR}:/workspace" \
    "${PERMISSION_FIX_IMAGE}" \
    sh -c "chown -R $(id -u):$(id -g) /workspace" >/dev/null 2>&1 || true
}

worktree_dirty() {
  git -C "${SANDBOX_DIR}" status --porcelain=v1 --untracked-files=normal 2>/dev/null | filter_runtime_git_status
}

prepare_sandbox() {
  ensure_review_ports

  kv sandbox_action prepare
  kv sandbox_repo_root "${REPO_ROOT}"
  kv sandbox_root "${SANDBOX_ROOT}"
  kv sandbox_dir "${SANDBOX_DIR}"
  kv sandbox_compose_project "${COMPOSE_PROJECT}"
  kv sandbox_remote "${REMOTE_NAME}"
  kv sandbox_branch "${BRANCH_NAME}"
  kv sandbox_target_ref "${TARGET_REF}"
  kv sandbox_frontend_port "${FRONTEND_HOST_PORT}"
  kv sandbox_backend_port "${BACKEND_HOST_PORT}"

  require_repo

  if [[ "${DRY_RUN}" == "1" ]]; then
    kv sandbox_status dry_run
    exit 0
  fi

  git -C "${REPO_ROOT}" worktree prune >/dev/null 2>&1 || true
  git -C "${REPO_ROOT}" fetch --prune "${REMOTE_NAME}" "${BRANCH_NAME}"

  if ! git -C "${REPO_ROOT}" rev-parse --verify "${TARGET_REF}" >/dev/null 2>&1; then
    kv sandbox_status error
    kv sandbox_error "Unable to resolve ${TARGET_REF}"
    exit 2
  fi

  if [[ -e "${SANDBOX_DIR}" ]]; then
    if [[ ! -d "${SANDBOX_DIR}" ]]; then
      kv sandbox_status error
      kv sandbox_error "Sandbox path exists but is not a directory: ${SANDBOX_DIR}"
      exit 2
    fi

    if ! git -C "${SANDBOX_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      kv sandbox_status error
      kv sandbox_error "Sandbox path exists but is not a git worktree: ${SANDBOX_DIR}"
      exit 2
    fi

    dirty_output="$(worktree_dirty)"
    if [[ -n "${dirty_output}" ]]; then
      kv sandbox_status blocked_dirty
      kv sandbox_error "Sandbox has local changes. Clean it up before refreshing from ${TARGET_REF}."
      printf '%s\n' "${dirty_output}"
      exit 3
    fi

    stop_existing_runtime
    repair_workspace_permissions
    git -C "${REPO_ROOT}" worktree remove --force "${SANDBOX_DIR}" >/dev/null 2>&1 || true
  fi

  mkdir -p "${SANDBOX_ROOT}"
  git -C "${REPO_ROOT}" worktree add --detach "${SANDBOX_DIR}" "${TARGET_REF}"

  head_sha="$(git -C "${SANDBOX_DIR}" rev-parse HEAD)"
  current_ref="$(git -C "${SANDBOX_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'HEAD')"
  kv sandbox_head_sha "${head_sha}"
  kv sandbox_current_ref "${current_ref}"

  export FRONTEND_HOST_PORT
  export BACKEND_HOST_PORT
  export POSTGRES_HOST_PORT
  export REDIS_HOST_PORT
  export LANGFUSE_HOST_PORT
  export WEAVIATE_HTTP_HOST_PORT
  export WEAVIATE_GRPC_HOST_PORT
  export SYMPHONY_LOCAL_SOURCE_ROOT="${REPO_ROOT}"
  export SYMPHONY_HOOKS_SOURCE="${REPO_ROOT}/.git/hooks"
  export SYMPHONY_RUNTIME_REFRESH_MODE="ensure"
  export SYMPHONY_REVIEW_INCLUDE_LANGFUSE_STACK="1"

  prep_cmd=(
    "${REPO_ROOT}/scripts/utilities/symphony_human_review_prep.sh"
    --workspace-dir "${SANDBOX_DIR}"
    --issue-key "MAIN-SANDBOX-1"
    --compose-project "${COMPOSE_PROJECT}"
  )

  if [[ -n "${REVIEW_HOST}" ]]; then
    prep_cmd+=(--review-host "${REVIEW_HOST}")
  fi

  set +e
  run_and_print "${prep_cmd[@]}"
  prep_status=$?
  set -e

  if [[ "${prep_status}" -ne 0 ]]; then
    kv sandbox_status prep_failed
    kv sandbox_exit_status "${prep_status}"
    exit "${prep_status}"
  fi

  kv sandbox_status prepared
}

cleanup_sandbox() {
  kv sandbox_action cleanup
  kv sandbox_repo_root "${REPO_ROOT}"
  kv sandbox_root "${SANDBOX_ROOT}"
  kv sandbox_dir "${SANDBOX_DIR}"
  kv sandbox_compose_project "${COMPOSE_PROJECT}"

  require_repo

  if [[ "${DRY_RUN}" == "1" ]]; then
    kv sandbox_status dry_run
    exit 0
  fi

  if [[ ! -e "${SANDBOX_DIR}" ]]; then
    kv sandbox_status absent
    kv sandbox_removed 1
    exit 0
  fi

  stop_existing_runtime
  repair_workspace_permissions

  if git -C "${SANDBOX_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git -C "${REPO_ROOT}" worktree remove --force "${SANDBOX_DIR}" >/dev/null 2>&1 || true
  fi

  if [[ -e "${SANDBOX_DIR}" ]]; then
    rm -rf "${SANDBOX_DIR}"
  fi

  git -C "${REPO_ROOT}" worktree prune >/dev/null 2>&1 || true

  if [[ ! -e "${SANDBOX_DIR}" ]]; then
    kv sandbox_status cleaned
    kv sandbox_removed 1
    exit 0
  fi

  kv sandbox_status cleanup_failed
  kv sandbox_removed 0
  exit 1
}

case "${action}" in
  prepare)
    prepare_sandbox
    ;;
  cleanup)
    cleanup_sandbox
    ;;
  *)
    echo "Unknown action: ${action}" >&2
    usage >&2
    exit 2
    ;;
esac
