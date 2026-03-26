#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd -P)"
REPO_NAME="$(basename "${REPO_ROOT}")"

usage() {
  cat <<'EOF'
Usage:
  symphony_main_sandbox.sh <prepare|repair|cleanup> [options]

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
PRIVATE_ENV_FILE="${AGR_AI_CURATION_ENV_FILE:-${HOME}/.agr_ai_curation/.env}"
DRY_RUN=0

FRONTEND_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_FRONTEND_PORT:-}"
BACKEND_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_BACKEND_PORT:-}"
POSTGRES_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_POSTGRES_PORT:-54330}"
REDIS_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_REDIS_PORT:-63830}"
LANGFUSE_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_LANGFUSE_PORT:-33330}"
WEAVIATE_HTTP_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_WEAVIATE_HTTP_PORT:-18430}"
WEAVIATE_GRPC_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_WEAVIATE_GRPC_PORT:-15430}"
TRACE_REVIEW_FRONTEND_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_FRONTEND_PORT:-3901}"
TRACE_REVIEW_BACKEND_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_BACKEND_PORT:-8901}"
FRONTEND_PORT_RANGE_START="${SYMPHONY_MAIN_SANDBOX_FRONTEND_PORT_START:-3900}"
FRONTEND_PORT_RANGE_END="${SYMPHONY_MAIN_SANDBOX_FRONTEND_PORT_END:-3999}"
BACKEND_PORT_OFFSET=5000
DB_TUNNEL_LOCAL_PORT="${SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_LOCAL_PORT:-6330}"
DB_TUNNEL_DOCKER_PORT="${SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_DOCKER_PORT:-6331}"
DB_TUNNEL_PORTS_EXPLICIT=0
PERMISSION_FIX_IMAGE="${SYMPHONY_MAIN_SANDBOX_PERMISSION_FIX_IMAGE:-public.ecr.aws/docker/library/python:3.11-slim@sha256:9358444059ed78e2975ada2c189f1c1a3144a5dab6f35bff8c981afb38946634}"

if [[ -n "${SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_LOCAL_PORT:-}" || -n "${SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_DOCKER_PORT:-}" ]]; then
  DB_TUNNEL_PORTS_EXPLICIT=1
fi

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
STATE_FILE="${SANDBOX_ROOT}/.symphony-main-sandbox-state.json"
TRACE_REVIEW_COMPOSE_PROJECT="${SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_COMPOSE_PROJECT:-${COMPOSE_PROJECT}tracereview}"

if [[ -z "${REVIEW_HOST}" ]] && command -v hostname >/dev/null 2>&1; then
  REVIEW_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi

kv() {
  printf '%s=%s\n' "$1" "$2"
}

load_exported_env_file() {
  local env_file="$1"
  if [[ -f "${env_file}" ]]; then
    local restore_nounset=0
    if [[ $- == *u* ]]; then
      restore_nounset=1
      set +u
    fi
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
    if [[ "${restore_nounset}" == "1" ]]; then
      set -u
    fi
  fi
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
  local mode="${1:-prepare}"

  if [[ -n "${FRONTEND_HOST_PORT}" || -n "${BACKEND_HOST_PORT}" ]]; then
    if [[ -z "${FRONTEND_HOST_PORT}" || -z "${BACKEND_HOST_PORT}" ]]; then
      echo "Set both SYMPHONY_MAIN_SANDBOX_FRONTEND_PORT and SYMPHONY_MAIN_SANDBOX_BACKEND_PORT, or neither." >&2
      exit 2
    fi
    return 0
  fi

  if [[ "${mode}" == "repair" ]]; then
    if [[ -f "${STATE_FILE}" ]] && command -v jq >/dev/null 2>&1; then
      FRONTEND_HOST_PORT="$(jq -r '.sandbox_frontend_port // empty' "${STATE_FILE}")"
      BACKEND_HOST_PORT="$(jq -r '.sandbox_backend_port // empty' "${STATE_FILE}")"
    fi

    if [[ -z "${FRONTEND_HOST_PORT}" || -z "${BACKEND_HOST_PORT}" ]]; then
      if [[ "${DRY_RUN}" == "1" ]]; then
        FRONTEND_HOST_PORT="${FRONTEND_PORT_RANGE_START}"
        BACKEND_HOST_PORT="$((FRONTEND_PORT_RANGE_START + BACKEND_PORT_OFFSET))"
        return 0
      fi

      echo "Unable to determine the current main sandbox frontend/backend ports for repair." >&2
      exit 2
    fi

    return 0
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

ensure_tunnel_ports() {
  local mode="${1:-prepare}"

  if [[ "${DB_TUNNEL_PORTS_EXPLICIT}" == "1" ]]; then
    if [[ -z "${SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_LOCAL_PORT:-}" || -z "${SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_DOCKER_PORT:-}" ]]; then
      echo "Set both SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_LOCAL_PORT and SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_DOCKER_PORT, or neither." >&2
      exit 2
    fi
  elif [[ "${mode}" == "repair" && -f "${STATE_FILE}" ]] && command -v jq >/dev/null 2>&1; then
    local state_local_port
    local state_docker_port

    state_local_port="$(jq -r '.sandbox_db_tunnel_local_port // empty' "${STATE_FILE}")"
    state_docker_port="$(jq -r '.sandbox_db_tunnel_docker_port // empty' "${STATE_FILE}")"

    if [[ -n "${state_local_port}" && -n "${state_docker_port}" ]]; then
      DB_TUNNEL_LOCAL_PORT="${state_local_port}"
      DB_TUNNEL_DOCKER_PORT="${state_docker_port}"
    fi
  fi

  if [[ -z "${DB_TUNNEL_LOCAL_PORT}" || -z "${DB_TUNNEL_DOCKER_PORT}" ]]; then
    echo "SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_LOCAL_PORT and SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_DOCKER_PORT must both be set." >&2
    exit 2
  fi

  if [[ "${DB_TUNNEL_LOCAL_PORT}" == "${DB_TUNNEL_DOCKER_PORT}" ]]; then
    echo "Main sandbox DB tunnel ports must differ." >&2
    exit 2
  fi
}

ensure_trace_review_ports() {
  local mode="${1:-prepare}"

  if [[ -n "${SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_FRONTEND_PORT:-}" || -n "${SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_BACKEND_PORT:-}" ]]; then
    if [[ -z "${SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_FRONTEND_PORT:-}" || -z "${SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_BACKEND_PORT:-}" ]]; then
      echo "Set both SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_FRONTEND_PORT and SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_BACKEND_PORT, or neither." >&2
      exit 2
    fi

    TRACE_REVIEW_FRONTEND_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_FRONTEND_PORT}"
    TRACE_REVIEW_BACKEND_HOST_PORT="${SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_BACKEND_PORT}"
    return 0
  fi

  if [[ "${mode}" == "repair" ]] && [[ -f "${STATE_FILE}" ]] && command -v jq >/dev/null 2>&1; then
    local state_trace_review_frontend_port
    local state_trace_review_backend_port

    state_trace_review_frontend_port="$(jq -r '.trace_review_frontend_port // empty' "${STATE_FILE}")"
    state_trace_review_backend_port="$(jq -r '.trace_review_backend_port // empty' "${STATE_FILE}")"

    if [[ -n "${state_trace_review_frontend_port}" && -n "${state_trace_review_backend_port}" ]]; then
      TRACE_REVIEW_FRONTEND_HOST_PORT="${state_trace_review_frontend_port}"
      TRACE_REVIEW_BACKEND_HOST_PORT="${state_trace_review_backend_port}"
    fi
  fi
}

ensure_unique_requested_ports() {
  local -A first_name_by_port=()
  local duplicates=()
  local named_ports=(
    "main frontend:${FRONTEND_HOST_PORT}"
    "main backend:${BACKEND_HOST_PORT}"
    "trace review frontend:${TRACE_REVIEW_FRONTEND_HOST_PORT}"
    "trace review backend:${TRACE_REVIEW_BACKEND_HOST_PORT}"
    "postgres:${POSTGRES_HOST_PORT}"
    "redis:${REDIS_HOST_PORT}"
    "langfuse:${LANGFUSE_HOST_PORT}"
    "weaviate http:${WEAVIATE_HTTP_HOST_PORT}"
    "weaviate grpc:${WEAVIATE_GRPC_HOST_PORT}"
    "db tunnel local:${DB_TUNNEL_LOCAL_PORT}"
    "db tunnel docker:${DB_TUNNEL_DOCKER_PORT}"
  )
  local pair
  local name
  local port

  for pair in "${named_ports[@]}"; do
    name="${pair%%:*}"
    port="${pair##*:}"
    if [[ -z "${port}" ]]; then
      continue
    fi

    if [[ -n "${first_name_by_port[${port}]:-}" ]]; then
      duplicates+=("${first_name_by_port[${port}]}:${port}")
      duplicates+=("${name}:${port}")
      continue
    fi

    first_name_by_port["${port}"]="${name}"
  done

  if [[ ${#duplicates[@]} -gt 0 ]]; then
    printf '%s\n' "${duplicates[@]}" | sort -u | paste -sd ', ' - | {
      read -r duplicate_summary
      echo "Main sandbox requested overlapping host ports: ${duplicate_summary}." >&2
    }
    exit 2
  fi
}

ensure_prepare_ports_available() {
  local unavailable=()
  local port

  ensure_unique_requested_ports

  if [[ "${DRY_RUN}" == "1" ]]; then
    return 0
  fi

  for port in \
    "${FRONTEND_HOST_PORT}" \
    "${BACKEND_HOST_PORT}" \
    "${TRACE_REVIEW_FRONTEND_HOST_PORT}" \
    "${TRACE_REVIEW_BACKEND_HOST_PORT}" \
    "${POSTGRES_HOST_PORT}" \
    "${REDIS_HOST_PORT}" \
    "${LANGFUSE_HOST_PORT}" \
    "${WEAVIATE_HTTP_HOST_PORT}" \
    "${WEAVIATE_GRPC_HOST_PORT}" \
    "${DB_TUNNEL_LOCAL_PORT}" \
    "${DB_TUNNEL_DOCKER_PORT}"; do
    if ! port_available "${port}"; then
      unavailable+=("${port}")
    fi
  done

  if [[ ${#unavailable[@]} -gt 0 ]]; then
    echo "Main sandbox requires free ports, but these are already in use: $(IFS=,; echo "${unavailable[*]}")." >&2
    exit 2
  fi
}

export_sandbox_runtime_env() {
  local review_public_host="${REVIEW_HOST:-127.0.0.1}"
  local selected_frontend_host_port="${FRONTEND_HOST_PORT}"
  local selected_backend_host_port="${BACKEND_HOST_PORT}"
  local selected_postgres_host_port="${POSTGRES_HOST_PORT}"
  local selected_redis_host_port="${REDIS_HOST_PORT}"
  local selected_langfuse_host_port="${LANGFUSE_HOST_PORT}"
  local selected_weaviate_http_host_port="${WEAVIATE_HTTP_HOST_PORT}"
  local selected_weaviate_grpc_host_port="${WEAVIATE_GRPC_HOST_PORT}"
  local selected_trace_review_frontend_port="${TRACE_REVIEW_FRONTEND_HOST_PORT}"
  local selected_trace_review_backend_port="${TRACE_REVIEW_BACKEND_HOST_PORT}"

  load_exported_env_file "${PRIVATE_ENV_FILE}"

  FRONTEND_HOST_PORT="${selected_frontend_host_port}"
  BACKEND_HOST_PORT="${selected_backend_host_port}"
  POSTGRES_HOST_PORT="${selected_postgres_host_port}"
  REDIS_HOST_PORT="${selected_redis_host_port}"
  LANGFUSE_HOST_PORT="${selected_langfuse_host_port}"
  WEAVIATE_HTTP_HOST_PORT="${selected_weaviate_http_host_port}"
  WEAVIATE_GRPC_HOST_PORT="${selected_weaviate_grpc_host_port}"
  TRACE_REVIEW_FRONTEND_HOST_PORT="${selected_trace_review_frontend_port}"
  TRACE_REVIEW_BACKEND_HOST_PORT="${selected_trace_review_backend_port}"

  export FRONTEND_HOST_PORT
  export BACKEND_HOST_PORT
  export POSTGRES_HOST_PORT
  export REDIS_HOST_PORT
  export LANGFUSE_HOST_PORT
  export WEAVIATE_HTTP_HOST_PORT
  export WEAVIATE_GRPC_HOST_PORT
  export TRACE_REVIEW_FRONTEND_HOST_PORT
  export TRACE_REVIEW_BACKEND_HOST_PORT
  export CURATION_DB_TUNNEL_LOCAL_PORT="${DB_TUNNEL_LOCAL_PORT}"
  export CURATION_DB_TUNNEL_DOCKER_PORT="${DB_TUNNEL_DOCKER_PORT}"
  export SYMPHONY_LOCAL_SOURCE_ROOT="${REPO_ROOT}"
  export SYMPHONY_HOOKS_SOURCE="${REPO_ROOT}/.git/hooks"
  export SYMPHONY_RUNTIME_REFRESH_MODE="ensure"
  export SYMPHONY_REVIEW_INCLUDE_LANGFUSE_STACK="1"
  export TRACE_REVIEW_URL="http://host.docker.internal:${TRACE_REVIEW_BACKEND_HOST_PORT}"
  export TRACE_REVIEW_FRONTEND_URL="http://${review_public_host}:${TRACE_REVIEW_FRONTEND_HOST_PORT}"
  export TRACE_REVIEW_PUBLIC_API_URL="http://host.docker.internal:${TRACE_REVIEW_BACKEND_HOST_PORT}"
  export LANGFUSE_LOCAL_HOST="http://127.0.0.1:${LANGFUSE_HOST_PORT}"
  export LANGFUSE_HOST="${LANGFUSE_HOST:-${LANGFUSE_LOCAL_HOST}}"
  export LANGFUSE_LOCAL_PUBLIC_KEY="${LANGFUSE_LOCAL_PUBLIC_KEY:-${LANGFUSE_PUBLIC_KEY:-}}"
  export LANGFUSE_LOCAL_SECRET_KEY="${LANGFUSE_LOCAL_SECRET_KEY:-${LANGFUSE_SECRET_KEY:-}}"
  export DEV_MODE="true"
  export VITE_DEV_MODE="true"
}

run_review_prep() {
  local mode="$1"
  local prep_cmd=(
    "${REPO_ROOT}/scripts/utilities/symphony_human_review_prep.sh"
    --workspace-dir "${SANDBOX_DIR}"
    --issue-key "MAIN-SANDBOX-1"
    --compose-project "${COMPOSE_PROJECT}"
  )

  if [[ "${mode}" == "repair" ]]; then
    prep_cmd+=(--skip-build-backend --skip-build-frontend)
  fi

  if [[ -n "${REVIEW_HOST}" ]]; then
    prep_cmd+=(--review-host "${REVIEW_HOST}")
  fi

  set +e
  run_and_print "${prep_cmd[@]}"
  local prep_status=$?
  set -e
  return "${prep_status}"
}

trace_review_compose_file() {
  printf '%s\n' "${SANDBOX_DIR}/trace_review/docker-compose.yml"
}

trace_review_dir() {
  printf '%s\n' "${SANDBOX_DIR}/trace_review"
}

trace_review_compose_run() {
  local compose_file
  compose_file="$(trace_review_compose_file)"

  if [[ ! -f "${compose_file}" ]]; then
    echo "Trace Review compose file is missing: ${compose_file}" >&2
    return 1
  fi

  (
    cd "$(trace_review_dir)"
    docker compose -f "${compose_file}" -p "${TRACE_REVIEW_COMPOSE_PROJECT}" "$@"
  )
}

wait_for_url() {
  local url="$1"
  local attempts="${2:-30}"
  local sleep_seconds="${3:-2}"
  local result=""
  local i

  for i in $(seq 1 "${attempts}"); do
    if result="$(curl -fsS -m 5 "${url}" 2>/dev/null)"; then
      printf '%s\n' "${result}"
      return 0
    fi
    sleep "${sleep_seconds}"
  done

  return 1
}

persist_state_snapshot() {
  local timestamp_key="$1"
  local sandbox_status_value="$2"
  local review_public_host="${REVIEW_HOST:-127.0.0.1}"
  local timestamp_value
  local review_frontend_local
  local review_backend_local
  local review_frontend_url
  local review_backend_url
  local frontend_health="unreachable"
  local backend_health=""
  local curation_db_health=""
  local pdf_extraction_health=""
  local trace_review_frontend_local
  local trace_review_backend_local
  local trace_review_frontend_url
  local trace_review_backend_url
  local trace_review_frontend_health="unreachable"
  local trace_review_backend_health=""
  local jq_expr
  local temp_file

  timestamp_value="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  review_frontend_local="http://127.0.0.1:${FRONTEND_HOST_PORT}/"
  review_backend_local="http://127.0.0.1:${BACKEND_HOST_PORT}/health"
  review_frontend_url="http://${review_public_host}:${FRONTEND_HOST_PORT}/"
  review_backend_url="http://${review_public_host}:${BACKEND_HOST_PORT}/health"
  trace_review_frontend_local="http://127.0.0.1:${TRACE_REVIEW_FRONTEND_HOST_PORT}/"
  trace_review_backend_local="http://127.0.0.1:${TRACE_REVIEW_BACKEND_HOST_PORT}/health"
  trace_review_frontend_url="http://${review_public_host}:${TRACE_REVIEW_FRONTEND_HOST_PORT}/"
  trace_review_backend_url="http://${review_public_host}:${TRACE_REVIEW_BACKEND_HOST_PORT}/health"

  if curl -fsS -m 5 "${review_frontend_local}" >/dev/null 2>&1; then
    frontend_health="healthy"
  fi

  backend_health="$(wait_for_url "${review_backend_local}" 2 1 || true)"
  curation_db_health="$(wait_for_url "http://127.0.0.1:${BACKEND_HOST_PORT}/api/admin/health/connections/curation_db" 2 1 || true)"
  pdf_extraction_health="$(wait_for_url "http://127.0.0.1:${BACKEND_HOST_PORT}/api/weaviate/documents/pdf-extraction-health" 2 1 || true)"

  if curl -fsS -m 5 "${trace_review_frontend_local}" >/dev/null 2>&1; then
    trace_review_frontend_health="healthy"
  fi

  trace_review_backend_health="$(wait_for_url "${trace_review_backend_local}" 2 1 || true)"

  mkdir -p "${SANDBOX_ROOT}"
  temp_file="$(mktemp)"
  jq_expr='
    . + {
      sandbox_repo_root: $sandbox_repo_root,
      sandbox_root: $sandbox_root,
      sandbox_dir: $sandbox_dir,
      sandbox_compose_project: $sandbox_compose_project,
      sandbox_frontend_port: $sandbox_frontend_port,
      sandbox_backend_port: $sandbox_backend_port,
      sandbox_db_tunnel_local_port: $sandbox_db_tunnel_local_port,
      sandbox_db_tunnel_docker_port: $sandbox_db_tunnel_docker_port,
      sandbox_head_sha: $sandbox_head_sha,
      sandbox_current_ref: $sandbox_current_ref,
      sandbox_target_ref: $sandbox_target_ref,
      review_frontend_local: $review_frontend_local,
      review_backend_local: $review_backend_local,
      review_frontend_url: $review_frontend_url,
      review_backend_url: $review_backend_url,
      frontend_health: $frontend_health,
      backend_health: $backend_health,
      curation_db_health: $curation_db_health,
      pdf_extraction_health: $pdf_extraction_health,
      trace_review_compose_project: $trace_review_compose_project,
      trace_review_frontend_port: $trace_review_frontend_port,
      trace_review_backend_port: $trace_review_backend_port,
      trace_review_frontend_local: $trace_review_frontend_local,
      trace_review_backend_local: $trace_review_backend_local,
      trace_review_frontend_url: $trace_review_frontend_url,
      trace_review_backend_url: $trace_review_backend_url,
      trace_review_frontend_health: $trace_review_frontend_health,
      trace_review_backend_health: $trace_review_backend_health,
      sandbox_status: $sandbox_status
    }
    | . + {($timestamp_key): $timestamp_value}
  '

  if [[ -f "${STATE_FILE}" ]] && jq empty "${STATE_FILE}" >/dev/null 2>&1; then
    jq \
      --arg sandbox_repo_root "${REPO_ROOT}" \
      --arg sandbox_root "${SANDBOX_ROOT}" \
      --arg sandbox_dir "${SANDBOX_DIR}" \
      --arg sandbox_compose_project "${COMPOSE_PROJECT}" \
      --arg sandbox_frontend_port "${FRONTEND_HOST_PORT}" \
      --arg sandbox_backend_port "${BACKEND_HOST_PORT}" \
      --arg sandbox_db_tunnel_local_port "${DB_TUNNEL_LOCAL_PORT}" \
      --arg sandbox_db_tunnel_docker_port "${DB_TUNNEL_DOCKER_PORT}" \
      --arg sandbox_head_sha "${head_sha:-}" \
      --arg sandbox_current_ref "${current_ref:-}" \
      --arg sandbox_target_ref "${TARGET_REF:-}" \
      --arg review_frontend_local "${review_frontend_local}" \
      --arg review_backend_local "${review_backend_local}" \
      --arg review_frontend_url "${review_frontend_url}" \
      --arg review_backend_url "${review_backend_url}" \
      --arg frontend_health "${frontend_health}" \
      --arg backend_health "${backend_health}" \
      --arg curation_db_health "${curation_db_health}" \
      --arg pdf_extraction_health "${pdf_extraction_health}" \
      --arg trace_review_compose_project "${TRACE_REVIEW_COMPOSE_PROJECT}" \
      --arg trace_review_frontend_port "${TRACE_REVIEW_FRONTEND_HOST_PORT}" \
      --arg trace_review_backend_port "${TRACE_REVIEW_BACKEND_HOST_PORT}" \
      --arg trace_review_frontend_local "${trace_review_frontend_local}" \
      --arg trace_review_backend_local "${trace_review_backend_local}" \
      --arg trace_review_frontend_url "${trace_review_frontend_url}" \
      --arg trace_review_backend_url "${trace_review_backend_url}" \
      --arg trace_review_frontend_health "${trace_review_frontend_health}" \
      --arg trace_review_backend_health "${trace_review_backend_health}" \
      --arg sandbox_status "${sandbox_status_value}" \
      --arg timestamp_key "${timestamp_key}" \
      --arg timestamp_value "${timestamp_value}" \
      "${jq_expr}" \
      "${STATE_FILE}" > "${temp_file}"
  else
    jq -n \
      --arg sandbox_repo_root "${REPO_ROOT}" \
      --arg sandbox_root "${SANDBOX_ROOT}" \
      --arg sandbox_dir "${SANDBOX_DIR}" \
      --arg sandbox_compose_project "${COMPOSE_PROJECT}" \
      --arg sandbox_frontend_port "${FRONTEND_HOST_PORT}" \
      --arg sandbox_backend_port "${BACKEND_HOST_PORT}" \
      --arg sandbox_db_tunnel_local_port "${DB_TUNNEL_LOCAL_PORT}" \
      --arg sandbox_db_tunnel_docker_port "${DB_TUNNEL_DOCKER_PORT}" \
      --arg sandbox_head_sha "${head_sha:-}" \
      --arg sandbox_current_ref "${current_ref:-}" \
      --arg sandbox_target_ref "${TARGET_REF:-}" \
      --arg review_frontend_local "${review_frontend_local}" \
      --arg review_backend_local "${review_backend_local}" \
      --arg review_frontend_url "${review_frontend_url}" \
      --arg review_backend_url "${review_backend_url}" \
      --arg frontend_health "${frontend_health}" \
      --arg backend_health "${backend_health}" \
      --arg curation_db_health "${curation_db_health}" \
      --arg pdf_extraction_health "${pdf_extraction_health}" \
      --arg trace_review_compose_project "${TRACE_REVIEW_COMPOSE_PROJECT}" \
      --arg trace_review_frontend_port "${TRACE_REVIEW_FRONTEND_HOST_PORT}" \
      --arg trace_review_backend_port "${TRACE_REVIEW_BACKEND_HOST_PORT}" \
      --arg trace_review_frontend_local "${trace_review_frontend_local}" \
      --arg trace_review_backend_local "${trace_review_backend_local}" \
      --arg trace_review_frontend_url "${trace_review_frontend_url}" \
      --arg trace_review_backend_url "${trace_review_backend_url}" \
      --arg trace_review_frontend_health "${trace_review_frontend_health}" \
      --arg trace_review_backend_health "${trace_review_backend_health}" \
      --arg sandbox_status "${sandbox_status_value}" \
      --arg timestamp_key "${timestamp_key}" \
      --arg timestamp_value "${timestamp_value}" \
      "${jq_expr}" > "${temp_file}"
  fi

  mv "${temp_file}" "${STATE_FILE}"
}

start_trace_review_stack() {
  local mode="$1"
  local review_public_host="${REVIEW_HOST:-127.0.0.1}"
  local build_flag=(--build)

  kv trace_review_compose_project "${TRACE_REVIEW_COMPOSE_PROJECT}"
  kv trace_review_frontend_port "${TRACE_REVIEW_FRONTEND_HOST_PORT}"
  kv trace_review_backend_port "${TRACE_REVIEW_BACKEND_HOST_PORT}"
  kv trace_review_frontend_local "http://127.0.0.1:${TRACE_REVIEW_FRONTEND_HOST_PORT}/"
  kv trace_review_backend_local "http://127.0.0.1:${TRACE_REVIEW_BACKEND_HOST_PORT}/health"
  kv trace_review_frontend_url "http://${review_public_host}:${TRACE_REVIEW_FRONTEND_HOST_PORT}/"
  kv trace_review_backend_url "http://${review_public_host}:${TRACE_REVIEW_BACKEND_HOST_PORT}/health"

  if [[ ! -f "$(trace_review_compose_file)" ]]; then
    kv trace_review_status missing
    kv trace_review_error "Trace Review compose file is missing from the sandbox checkout."
    return 1
  fi

  trace_review_compose_run up -d "${build_flag[@]}" backend frontend

  local frontend_url="http://127.0.0.1:${TRACE_REVIEW_FRONTEND_HOST_PORT}/"
  local backend_health_url="http://127.0.0.1:${TRACE_REVIEW_BACKEND_HOST_PORT}/health"
  local frontend_status="unreachable"
  local backend_status="unreachable"
  local backend_payload=""

  if wait_for_url "${frontend_url}" 20 2 >/dev/null; then
    frontend_status="healthy"
  fi

  if backend_payload="$(wait_for_url "${backend_health_url}" 30 2)"; then
    backend_status="${backend_payload}"
  fi

  kv trace_review_frontend_health "${frontend_status}"
  kv trace_review_backend_health "${backend_status}"

  if [[ "${frontend_status}" != "healthy" || "${backend_status}" == "unreachable" ]]; then
    kv trace_review_status failed
    kv trace_review_error "Trace Review stack failed health checks."
    return 1
  fi

  kv trace_review_status ready
}

stop_trace_review_runtime() {
  if [[ -f "$(trace_review_compose_file)" ]]; then
    trace_review_compose_run down --remove-orphans -v >/dev/null 2>&1 || true
  fi
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

  stop_trace_review_runtime
}

stop_existing_tunnel() {
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
  ensure_trace_review_ports prepare
  ensure_tunnel_ports prepare
  ensure_unique_requested_ports

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
  kv sandbox_db_tunnel_local_port "${DB_TUNNEL_LOCAL_PORT}"
  kv sandbox_db_tunnel_docker_port "${DB_TUNNEL_DOCKER_PORT}"
  kv trace_review_frontend_port "${TRACE_REVIEW_FRONTEND_HOST_PORT}"
  kv trace_review_backend_port "${TRACE_REVIEW_BACKEND_HOST_PORT}"
  kv trace_review_compose_project "${TRACE_REVIEW_COMPOSE_PROJECT}"

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
    ensure_prepare_ports_available
    git -C "${REPO_ROOT}" worktree remove --force "${SANDBOX_DIR}" >/dev/null 2>&1 || true
  else
    ensure_prepare_ports_available
  fi

  mkdir -p "${SANDBOX_ROOT}"
  git -C "${REPO_ROOT}" worktree add --detach "${SANDBOX_DIR}" "${TARGET_REF}"

  head_sha="$(git -C "${SANDBOX_DIR}" rev-parse HEAD)"
  current_ref="$(git -C "${SANDBOX_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'HEAD')"
  kv sandbox_head_sha "${head_sha}"
  kv sandbox_current_ref "${current_ref}"

  export_sandbox_runtime_env
  run_review_prep prepare
  prep_status=$?

  if [[ "${prep_status}" -ne 0 ]]; then
    kv sandbox_status prep_failed
    kv sandbox_exit_status "${prep_status}"
    persist_state_snapshot "last_prepared_at" "prep_failed"
    exit "${prep_status}"
  fi

  if ! start_trace_review_stack prepare; then
    kv sandbox_status prep_failed
    persist_state_snapshot "last_prepared_at" "prep_failed"
    exit 1
  fi

  kv sandbox_status prepared
  persist_state_snapshot "last_prepared_at" "prepared"
}

repair_sandbox() {
  ensure_review_ports repair
  ensure_trace_review_ports repair
  ensure_tunnel_ports repair
  ensure_unique_requested_ports

  kv sandbox_action repair
  kv sandbox_repo_root "${REPO_ROOT}"
  kv sandbox_root "${SANDBOX_ROOT}"
  kv sandbox_dir "${SANDBOX_DIR}"
  kv sandbox_compose_project "${COMPOSE_PROJECT}"
  kv sandbox_frontend_port "${FRONTEND_HOST_PORT}"
  kv sandbox_backend_port "${BACKEND_HOST_PORT}"
  kv sandbox_db_tunnel_local_port "${DB_TUNNEL_LOCAL_PORT}"
  kv sandbox_db_tunnel_docker_port "${DB_TUNNEL_DOCKER_PORT}"
  kv trace_review_frontend_port "${TRACE_REVIEW_FRONTEND_HOST_PORT}"
  kv trace_review_backend_port "${TRACE_REVIEW_BACKEND_HOST_PORT}"
  kv trace_review_compose_project "${TRACE_REVIEW_COMPOSE_PROJECT}"

  require_repo

  if [[ "${DRY_RUN}" == "1" ]]; then
    kv sandbox_status dry_run
    exit 0
  fi

  if [[ ! -d "${SANDBOX_DIR}" ]]; then
    kv sandbox_status absent
    kv sandbox_error "Main sandbox checkout does not exist yet."
    exit 2
  fi

  if ! git -C "${SANDBOX_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    kv sandbox_status error
    kv sandbox_error "Sandbox path exists but is not a git worktree: ${SANDBOX_DIR}"
    exit 2
  fi

  head_sha="$(git -C "${SANDBOX_DIR}" rev-parse HEAD)"
  current_ref="$(git -C "${SANDBOX_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || printf 'HEAD')"
  kv sandbox_head_sha "${head_sha}"
  kv sandbox_current_ref "${current_ref}"

  repair_workspace_permissions
  stop_existing_tunnel
  export_sandbox_runtime_env
  run_review_prep repair
  repair_status=$?

  if [[ "${repair_status}" -ne 0 ]]; then
    kv sandbox_status repair_failed
    kv sandbox_exit_status "${repair_status}"
    persist_state_snapshot "last_repaired_at" "repair_failed"
    exit "${repair_status}"
  fi

  if ! start_trace_review_stack repair; then
    kv sandbox_status repair_failed
    persist_state_snapshot "last_repaired_at" "repair_failed"
    exit 1
  fi

  kv sandbox_status repaired
  persist_state_snapshot "last_repaired_at" "repaired"
}

cleanup_sandbox() {
  kv sandbox_action cleanup
  kv sandbox_repo_root "${REPO_ROOT}"
  kv sandbox_root "${SANDBOX_ROOT}"
  kv sandbox_dir "${SANDBOX_DIR}"
  kv sandbox_compose_project "${COMPOSE_PROJECT}"
  kv trace_review_compose_project "${TRACE_REVIEW_COMPOSE_PROJECT}"

  require_repo

  if [[ "${DRY_RUN}" == "1" ]]; then
    kv sandbox_status dry_run
    exit 0
  fi

  rm -f "${STATE_FILE}"

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
  repair)
    repair_sandbox
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
