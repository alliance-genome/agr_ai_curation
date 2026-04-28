#!/usr/bin/env bash
# Wrapper for Symphony Human Review Prep local stack bring-up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/lib/rerank_provider_common.sh"

usage() {
  cat <<'USAGE'
Usage:
  symphony_human_review_prep.sh [options]

Options:
  --workspace-dir DIR      Workspace/repo to prepare (default: current directory)
  --issue-key KEY          Issue key used for deterministic port allocation
  --compose-project NAME   Docker Compose project name (default: derived from issue key)
  --review-host HOST       Host/IP to publish in review URLs
  --env-file PATH          Private env file (default: ~/.agr_ai_curation/.env when present)
  --build-backend          Rebuild the backend image before review (default)
  --build-frontend         Rebuild the frontend image before review (default)
  --skip-build-backend     Skip backend image rebuild during review prep
  --skip-build-frontend    Skip frontend image rebuild during review prep
  --skip-db-tunnel         Skip DB tunnel startup
  --skip-runtime-refresh   Skip managed workspace runtime refresh before prep
  --start-test-containers BOOL
                           Opt-in: when 'true', boot the local stack
                           (DB tunnel, dependency services, build, up, and
                           health checks). When 'false' (default), the wrapper
                           prints a short "skipped" context and exits 0.
  -h, --help              Show this help
USAGE
}

WORKSPACE_DIR="${PWD}"
ISSUE_KEY="${ISSUE_KEY:-}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-}"
REVIEW_HOST="${REVIEW_HOST:-${SYMPHONY_REVIEW_HOST:-}}"
PRIVATE_ENV_FILE="${AGR_AI_CURATION_ENV_FILE:-${HOME}/.agr_ai_curation/.env}"
BUILD_BACKEND="${SYMPHONY_REVIEW_BUILD_BACKEND:-1}"
BUILD_FRONTEND="${SYMPHONY_REVIEW_BUILD_FRONTEND:-1}"
INCLUDE_LANGFUSE_STACK="${SYMPHONY_REVIEW_INCLUDE_LANGFUSE_STACK:-0}"
SKIP_DB_TUNNEL=0
SKIP_RUNTIME_REFRESH="${SYMPHONY_REVIEW_PREP_REFRESH_MANAGED:-1}"
START_TEST_CONTAINERS=0
DEPENDENCY_START_MAX_ATTEMPTS="${SYMPHONY_REVIEW_DEPENDENCY_START_MAX_ATTEMPTS:-2}"
DEPENDENCY_START_RETRY_SLEEP="${SYMPHONY_REVIEW_DEPENDENCY_START_RETRY_SLEEP_SECONDS:-3}"
FRONTEND_HEALTH_ATTEMPTS="${SYMPHONY_REVIEW_FRONTEND_HEALTH_ATTEMPTS:-20}"
FRONTEND_HEALTH_SLEEP="${SYMPHONY_REVIEW_FRONTEND_HEALTH_SLEEP_SECONDS:-2}"
BACKEND_HEALTH_ATTEMPTS="${SYMPHONY_REVIEW_BACKEND_HEALTH_ATTEMPTS:-60}"
BACKEND_HEALTH_SLEEP="${SYMPHONY_REVIEW_BACKEND_HEALTH_SLEEP_SECONDS:-2}"
CURATION_HEALTH_ATTEMPTS="${SYMPHONY_REVIEW_CURATION_HEALTH_ATTEMPTS:-15}"
CURATION_HEALTH_SLEEP="${SYMPHONY_REVIEW_CURATION_HEALTH_SLEEP_SECONDS:-2}"
PDF_HEALTH_ATTEMPTS="${SYMPHONY_REVIEW_PDF_HEALTH_ATTEMPTS:-3}"
PDF_HEALTH_SLEEP="${SYMPHONY_REVIEW_PDF_HEALTH_SLEEP_SECONDS:-2}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      WORKSPACE_DIR="$2"
      shift 2
      ;;
    --issue-key)
      ISSUE_KEY="$2"
      shift 2
      ;;
    --compose-project)
      COMPOSE_PROJECT="$2"
      shift 2
      ;;
    --review-host)
      REVIEW_HOST="$2"
      shift 2
      ;;
    --env-file)
      PRIVATE_ENV_FILE="$2"
      shift 2
      ;;
    --build-backend)
      BUILD_BACKEND=1
      shift
      ;;
    --build-frontend)
      BUILD_FRONTEND=1
      shift
      ;;
    --skip-build-backend)
      BUILD_BACKEND=0
      shift
      ;;
    --skip-build-frontend)
      BUILD_FRONTEND=0
      shift
      ;;
    --skip-db-tunnel)
      SKIP_DB_TUNNEL=1
      shift
      ;;
    --skip-runtime-refresh)
      SKIP_RUNTIME_REFRESH=0
      shift
      ;;
    --start-test-containers)
      case "$2" in
        true) START_TEST_CONTAINERS=1 ;;
        false) START_TEST_CONTAINERS=0 ;;
        *)
          echo "--start-test-containers requires 'true' or 'false' (got: $2)" >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    --start-test-containers=*)
      case "${1#*=}" in
        true) START_TEST_CONTAINERS=1 ;;
        false) START_TEST_CONTAINERS=0 ;;
        *)
          echo "--start-test-containers requires 'true' or 'false' (got: ${1#*=})" >&2
          exit 2
          ;;
      esac
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

WORKSPACE_DIR="$(cd "${WORKSPACE_DIR}" && pwd -P)"
COMPOSE_FILE="${WORKSPACE_DIR}/docker-compose.yml"
TUNNEL_ENV_FILE="${WORKSPACE_DIR}/scripts/local_db_tunnel_env.sh"
REVIEW_DEPENDENCY_SERVICES=()
REVIEW_RERANK_PROVIDER="none"
REVIEW_RERANKER_REQUIRED=0

if [[ ! -d "${WORKSPACE_DIR}" ]]; then
  echo "Workspace directory does not exist: ${WORKSPACE_DIR}" >&2
  exit 2
fi

resolve_helper() {
  local relative_path="$1"
  if [[ -x "${WORKSPACE_DIR}/${relative_path}" ]]; then
    printf '%s\n' "${WORKSPACE_DIR}/${relative_path}"
    return 0
  fi
  if [[ -x "${REPO_ROOT}/${relative_path}" ]]; then
    printf '%s\n' "${REPO_ROOT}/${relative_path}"
    return 0
  fi
  return 1
}

log_step() {
  printf '\n[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"
}

emit_wrapper_status() {
  local status="$1"
  local reason="${2:-}"

  echo "human_review_prep_wrapper_status=${status}"
  echo "human_review_prep_wrapper_reason=${reason}"
}

normalize_private_env_file() {
  if [[ ! -f "${PRIVATE_ENV_FILE}" ]]; then
    return 0
  fi

  local helper=""
  helper="$(resolve_helper "scripts/utilities/ensure_local_langfuse_env.sh" || true)"
  if [[ -z "${helper}" ]]; then
    return 0
  fi

  log_step "Normalizing local Langfuse env values"
  bash "${helper}" "${PRIVATE_ENV_FILE}"
}

refresh_workspace_runtime() {
  if [[ "${SKIP_RUNTIME_REFRESH}" != "1" ]]; then
    return 0
  fi

  local refresh_mode="${SYMPHONY_RUNTIME_REFRESH_MODE:-refresh}"
  local helper=""
  if [[ -x "${REPO_ROOT}/scripts/utilities/symphony_ensure_workspace_runtime.sh" ]]; then
    helper="${REPO_ROOT}/scripts/utilities/symphony_ensure_workspace_runtime.sh"
  elif [[ -x "${WORKSPACE_DIR}/scripts/utilities/symphony_ensure_workspace_runtime.sh" ]]; then
    helper="${WORKSPACE_DIR}/scripts/utilities/symphony_ensure_workspace_runtime.sh"
  fi

  if [[ -z "${helper}" ]]; then
    echo "warning_runtime_refresh=missing_helper" >&2
    return 0
  fi

  case "${refresh_mode}" in
    ensure)
      log_step "Ensuring Symphony runtime files"
      bash "${helper}" --workspace-dir "${WORKSPACE_DIR}"
      ;;
    refresh)
      log_step "Refreshing managed Symphony runtime files"
      bash "${helper}" --workspace-dir "${WORKSPACE_DIR}" --refresh-managed
      ;;
    *)
      echo "Unknown SYMPHONY_RUNTIME_REFRESH_MODE: ${refresh_mode}" >&2
      exit 2
      ;;
  esac
}

json_compact() {
  local raw="$1"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "${raw}" | jq -c . 2>/dev/null || printf '%s' "${raw}"
    return 0
  fi
  printf '%s' "${raw}"
}

derive_issue_key() {
  local value="${1:-}"
  if [[ -n "${value}" ]]; then
    printf '%s\n' "${value}"
    return 0
  fi

  value="$(basename "${WORKSPACE_DIR}")"
  if [[ "${value}" =~ ^[A-Za-z]+-[0-9]+$ ]]; then
    printf '%s\n' "${value}"
    return 0
  fi

  echo "Unable to derive issue key from workspace '${WORKSPACE_DIR}'. Use --issue-key." >&2
  exit 2
}

issue_number_from_key() {
  local key="$1"
  if [[ "${key}" =~ -([0-9]+)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  echo "Issue key must end with a numeric suffix: ${key}" >&2
  exit 2
}

compose_project_from_issue() {
  printf '%s' "$1" | tr -cd '[:alnum:]' | tr '[:upper:]' '[:lower:]'
}

seed_port_env() {
  local issue_number="$1"

  export LANGFUSE_HOST_PORT="${LANGFUSE_HOST_PORT:-$((3400 + issue_number))}"
  export FRONTEND_HOST_PORT="${FRONTEND_HOST_PORT:-$((3000 + issue_number))}"
  export BACKEND_HOST_PORT="${BACKEND_HOST_PORT:-$((8000 + issue_number))}"
  export POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-$((5400 + issue_number))}"
  export REDIS_HOST_PORT="${REDIS_HOST_PORT:-$((6400 + issue_number))}"
  export WEAVIATE_HTTP_HOST_PORT="${WEAVIATE_HTTP_HOST_PORT:-$((18400 + issue_number))}"
  export WEAVIATE_GRPC_HOST_PORT="${WEAVIATE_GRPC_HOST_PORT:-$((15400 + issue_number))}"
}

capture_review_port_env() {
  REVIEW_PORT_LANGFUSE_HOST_PORT="${LANGFUSE_HOST_PORT:-}"
  REVIEW_PORT_FRONTEND_HOST_PORT="${FRONTEND_HOST_PORT:-}"
  REVIEW_PORT_BACKEND_HOST_PORT="${BACKEND_HOST_PORT:-}"
  REVIEW_PORT_POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-}"
  REVIEW_PORT_REDIS_HOST_PORT="${REDIS_HOST_PORT:-}"
  REVIEW_PORT_WEAVIATE_HTTP_HOST_PORT="${WEAVIATE_HTTP_HOST_PORT:-}"
  REVIEW_PORT_WEAVIATE_GRPC_HOST_PORT="${WEAVIATE_GRPC_HOST_PORT:-}"
}

restore_review_port_env() {
  export LANGFUSE_HOST_PORT="${REVIEW_PORT_LANGFUSE_HOST_PORT}"
  export FRONTEND_HOST_PORT="${REVIEW_PORT_FRONTEND_HOST_PORT}"
  export BACKEND_HOST_PORT="${REVIEW_PORT_BACKEND_HOST_PORT}"
  export POSTGRES_HOST_PORT="${REVIEW_PORT_POSTGRES_HOST_PORT}"
  export REDIS_HOST_PORT="${REVIEW_PORT_REDIS_HOST_PORT}"
  export WEAVIATE_HTTP_HOST_PORT="${REVIEW_PORT_WEAVIATE_HTTP_HOST_PORT}"
  export WEAVIATE_GRPC_HOST_PORT="${REVIEW_PORT_WEAVIATE_GRPC_HOST_PORT}"
}

ensure_langfuse_nextauth_url() {
  if [[ "${INCLUDE_LANGFUSE_STACK}" != "1" ]]; then
    return 0
  fi

  local langfuse_host="${REVIEW_HOST:-127.0.0.1}"
  local expected_nextauth_url="http://${langfuse_host}:${LANGFUSE_HOST_PORT}"

  if [[ "${NEXTAUTH_URL:-}" == "${expected_nextauth_url}" ]]; then
    return 0
  fi

  export NEXTAUTH_URL="${expected_nextauth_url}"
}

ensure_workspace_storage_env() {
  # Review workspaces use the repo-local dev compose file, which bind-mounts
  # mutable data under /app/*. Override any standalone-runtime relative paths
  # imported from ~/.agr_ai_curation/.env so uploaded PDFs remain reachable.
  export PDF_STORAGE_PATH="/app/pdf_storage"
  export FILE_OUTPUT_STORAGE_PATH="/app/file_outputs"
}

ensure_workspace_storage_permissions() {
  local storage_dir=""

  # The dev compose backend bind-mounts mutable state from the workspace. With
  # capabilities dropped, container root can no longer bypass host mode bits, so
  # these directories must stay world-writable like the standalone installer.
  for storage_dir in \
    "${WORKSPACE_DIR}/pdf_storage" \
    "${WORKSPACE_DIR}/file_outputs"; do
    mkdir -p "${storage_dir}"
    chmod 0777 "${storage_dir}"
  done
}

prepare_docker_config() {
  local helper_path="${1:-}"
  if [[ -n "${helper_path}" ]]; then
    bash "${helper_path}" --workspace-dir "${WORKSPACE_DIR}"
    return 0
  fi

  local docker_config_dir="${WORKSPACE_DIR}/.symphony-docker-config"
  local source_config="${HOME}/.docker/config.json"
  local target_config="${docker_config_dir}/config.json"
  mkdir -p "${docker_config_dir}"
  if [[ -f "${source_config}" && ! -f "${target_config}" ]]; then
    cp "${source_config}" "${target_config}" >/dev/null 2>&1 || true
  fi
  printf '%s\n' "${docker_config_dir}"
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

compose_run() {
  local args=()
  local compose_args=()
  if [[ -f "${PRIVATE_ENV_FILE}" ]]; then
    args+=(--env-file "${PRIVATE_ENV_FILE}")
  fi
  append_local_reranker_profile_args "${RERANK_PROVIDER:-none}" compose_args
  (
    cd "${WORKSPACE_DIR}"
    DOCKER_CONFIG="${WORKSPACE_DOCKER_CONFIG}" docker compose "${args[@]}" -f "${COMPOSE_FILE}" -p "${COMPOSE_PROJECT}" "${compose_args[@]}" "$@"
  )
}

compose_service_exists() {
  local service="$1"
  compose_run config --services 2>/dev/null | grep -Fx "${service}" >/dev/null 2>&1
}

resolve_review_dependency_services() {
  local service
  local candidates=(postgres redis weaviate)
  REVIEW_DEPENDENCY_SERVICES=()
  REVIEW_RERANK_PROVIDER="$(normalize_rerank_provider "${RERANK_PROVIDER:-none}")"
  REVIEW_RERANKER_REQUIRED=0

  if rerank_provider_requires_local_service "${REVIEW_RERANK_PROVIDER}"; then
    candidates=(postgres redis reranker-transformers weaviate)
    REVIEW_RERANKER_REQUIRED=1
  fi

  if [[ "${INCLUDE_LANGFUSE_STACK}" == "1" ]]; then
    candidates+=(clickhouse minio langfuse langfuse-worker)
  fi

  for service in "${candidates[@]}"; do
    if compose_service_exists "${service}"; then
      REVIEW_DEPENDENCY_SERVICES+=("${service}")
    fi
  done
}

print_compose_ps_snapshot() {
  echo "compose_ps_begin"
  compose_run ps 2>&1 || true
  echo "compose_ps_end"
}

print_service_logs_tail() {
  local service="$1"
  local tail_lines="${2:-80}"

  echo "service_logs_begin=${service}"
  compose_run logs --tail "${tail_lines}" "${service}" 2>&1 || true
  echo "service_logs_end=${service}"
}

print_dependency_diagnostics() {
  local service

  log_step "Collecting dependency diagnostics"
  print_compose_ps_snapshot
  for service in "${REVIEW_DEPENDENCY_SERVICES[@]}"; do
    print_service_logs_tail "${service}"
  done
}

start_dependency_services() {
  local attempt

  if [[ ${#REVIEW_DEPENDENCY_SERVICES[@]} -eq 0 ]]; then
    echo "dependency_services=none"
    echo "dependency_start_status=skipped"
    return 0
  fi

  echo "dependency_services=$(IFS=,; echo "${REVIEW_DEPENDENCY_SERVICES[*]}")"
  for attempt in $(seq 1 "${DEPENDENCY_START_MAX_ATTEMPTS}"); do
    if compose_run up -d --wait "${REVIEW_DEPENDENCY_SERVICES[@]}"; then
      echo "dependency_start_status=ready"
      if [[ "${attempt}" -gt 1 ]]; then
        echo "dependency_start_retry_success_attempt=${attempt}"
      fi
      return 0
    fi

    echo "dependency_start_attempt_failed=${attempt}"
    print_dependency_diagnostics
    if [[ "${attempt}" -lt "${DEPENDENCY_START_MAX_ATTEMPTS}" ]]; then
      sleep "${DEPENDENCY_START_RETRY_SLEEP}"
    fi
  done

  echo "dependency_start_status=failed"
  return 1
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

first_backend_root_cause() {
  compose_run logs backend 2>/dev/null | rg -m 1 'UndefinedColumn|ProgrammingError|Traceback|Exception|ERROR' || true
}

print_urls() {
  local frontend_local="http://127.0.0.1:${FRONTEND_HOST_PORT}/"
  local backend_local="http://127.0.0.1:${BACKEND_HOST_PORT}/health"
  local frontend_review="http://${REVIEW_HOST}:${FRONTEND_HOST_PORT}/"
  local backend_review="http://${REVIEW_HOST}:${BACKEND_HOST_PORT}/health"

  echo "review_frontend_local=${frontend_local}"
  echo "review_backend_local=${backend_local}"
  if [[ -n "${REVIEW_HOST}" ]]; then
    echo "review_frontend_url=${frontend_review}"
    echo "review_backend_url=${backend_review}"
  fi
}

check_required_vars() {
  local missing=()
  local key
  for key in OPENAI_API_KEY GROQ_API_KEY; do
    if [[ -z "${!key:-}" ]]; then
      missing+=("${key}")
    fi
  done

  if [[ ${#missing[@]} -gt 0 ]]; then
    printf 'warning_missing_runtime_vars=%s\n' "$(IFS=,; echo "${missing[*]}")"
  fi
}

ISSUE_KEY="$(derive_issue_key "${ISSUE_KEY}")"
ISSUE_NUMBER="$(issue_number_from_key "${ISSUE_KEY}")"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-$(compose_project_from_issue "${ISSUE_KEY}")}"
if [[ -z "${REVIEW_HOST}" ]] && command -v hostname >/dev/null 2>&1; then
  REVIEW_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi
refresh_workspace_runtime
if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "Workspace is missing docker-compose.yml: ${COMPOSE_FILE}" >&2
  exit 2
fi
seed_port_env "${ISSUE_NUMBER}"
capture_review_port_env
normalize_private_env_file
load_exported_env_file "${PRIVATE_ENV_FILE}"
restore_review_port_env
ensure_langfuse_nextauth_url
ensure_workspace_storage_env
ensure_workspace_storage_permissions
check_required_vars
export RUN_DB_BOOTSTRAP_ON_START="${RUN_DB_BOOTSTRAP_ON_START:-true}"
export RUN_DB_MIGRATIONS_ON_START="${RUN_DB_MIGRATIONS_ON_START:-true}"

if [[ "${START_TEST_CONTAINERS}" -eq 0 ]]; then
  log_step "Human Review Prep stack startup skipped for ${ISSUE_KEY} (start_test_containers=false)"
  emit_wrapper_status "skipped" "start_test_containers_false"
  echo "workspace_dir=${WORKSPACE_DIR}"
  echo "compose_file=${COMPOSE_FILE}"
  echo "compose_project=${COMPOSE_PROJECT}"
  echo "frontend_host_port=${FRONTEND_HOST_PORT}"
  echo "backend_host_port=${BACKEND_HOST_PORT}"
  echo "postgres_host_port=${POSTGRES_HOST_PORT}"
  echo "redis_host_port=${REDIS_HOST_PORT}"
  echo "langfuse_host_port=${LANGFUSE_HOST_PORT}"
  echo "weaviate_http_host_port=${WEAVIATE_HTTP_HOST_PORT}"
  echo "weaviate_grpc_host_port=${WEAVIATE_GRPC_HOST_PORT}"
  echo "start_test_containers=0"
  echo "stack_startup=skipped_by_flag"
  echo "dependency_start_status=skipped_by_flag"
  echo "frontend_health=skipped_by_flag"
  echo "backend_health=skipped_by_flag"
  echo "curation_db_health=skipped_by_flag"
  echo "pdf_extraction_health=skipped_by_flag"
  exit 0
fi

if [[ "${SKIP_DB_TUNNEL}" -eq 0 ]]; then
  TUNNEL_START_HELPER="$(resolve_helper "scripts/utilities/symphony_local_db_tunnel_start.sh" || true)"
  TUNNEL_STATUS_HELPER="$(resolve_helper "scripts/utilities/symphony_local_db_tunnel_status.sh" || true)"
  if [[ -z "${TUNNEL_START_HELPER}" ]]; then
    echo "Unable to find symphony_local_db_tunnel_start.sh in workspace or repo" >&2
    exit 2
  fi
else
  TUNNEL_START_HELPER=""
  TUNNEL_STATUS_HELPER=""
fi

DOCKER_CONFIG_HELPER="$(resolve_helper "scripts/utilities/symphony_prepare_docker_config.sh" || true)"
WORKSPACE_DOCKER_CONFIG="$(prepare_docker_config "${DOCKER_CONFIG_HELPER}")"
resolve_review_dependency_services

log_step "Preparing Human Review stack for ${ISSUE_KEY}"
echo "workspace_dir=${WORKSPACE_DIR}"
echo "compose_file=${COMPOSE_FILE}"
echo "compose_project=${COMPOSE_PROJECT}"
echo "workspace_docker_config=${WORKSPACE_DOCKER_CONFIG}"
echo "frontend_host_port=${FRONTEND_HOST_PORT}"
echo "backend_host_port=${BACKEND_HOST_PORT}"
echo "postgres_host_port=${POSTGRES_HOST_PORT}"
echo "redis_host_port=${REDIS_HOST_PORT}"
echo "langfuse_host_port=${LANGFUSE_HOST_PORT}"
echo "weaviate_http_host_port=${WEAVIATE_HTTP_HOST_PORT}"
echo "weaviate_grpc_host_port=${WEAVIATE_GRPC_HOST_PORT}"
echo "build_backend=${BUILD_BACKEND}"
echo "build_frontend=${BUILD_FRONTEND}"
echo "include_langfuse_stack=${INCLUDE_LANGFUSE_STACK}"
echo "start_test_containers=1"
echo "rerank_provider=${REVIEW_RERANK_PROVIDER}"
echo "reranker_dependency_required=${REVIEW_RERANKER_REQUIRED}"
echo "dependency_start_max_attempts=${DEPENDENCY_START_MAX_ATTEMPTS}"
echo "run_db_bootstrap_on_start=${RUN_DB_BOOTSTRAP_ON_START}"
echo "run_db_migrations_on_start=${RUN_DB_MIGRATIONS_ON_START}"

if [[ "${SKIP_DB_TUNNEL}" -eq 0 ]]; then
  log_step "Starting DB tunnel"
  bash "${TUNNEL_START_HELPER}" --workspace-dir "${WORKSPACE_DIR}"
  if [[ -f "${TUNNEL_ENV_FILE}" ]]; then
    load_exported_env_file "${TUNNEL_ENV_FILE}"
    echo "tunnel_env_file=${TUNNEL_ENV_FILE}"
    echo "tunnel_curation_db_url_host=${CURATION_DB_TUNNEL_FORWARD_HOST:-}"
    echo "tunnel_curation_db_url_port=${CURATION_DB_TUNNEL_DOCKER_PORT:-}"
  fi
  if [[ -n "${TUNNEL_STATUS_HELPER}" ]]; then
    bash "${TUNNEL_STATUS_HELPER}" --workspace-dir "${WORKSPACE_DIR}" || true
  fi
fi

log_step "Starting dependency services"
set +e
start_dependency_services
dependency_start_rc=$?
set -e
if [[ "${dependency_start_rc}" -ne 0 ]]; then
  emit_wrapper_status "failed" "dependency_start_failed"
  exit "${dependency_start_rc}"
fi

log_step "Building review services"
if [[ "${BUILD_BACKEND}" -eq 1 || "${BUILD_FRONTEND}" -eq 1 ]]; then
  build_targets=()
  [[ "${BUILD_BACKEND}" -eq 1 ]] && build_targets+=(backend)
  [[ "${BUILD_FRONTEND}" -eq 1 ]] && build_targets+=(frontend)
  set +e
  compose_run build "${build_targets[@]}"
  build_rc=$?
  set -e
  if [[ "${build_rc}" -ne 0 ]]; then
    emit_wrapper_status "failed" "service_build_failed"
    exit "${build_rc}"
  fi
fi

log_step "Starting Docker services"
set +e
compose_run up -d backend frontend
app_start_rc=$?
set -e
if [[ "${app_start_rc}" -ne 0 ]]; then
  emit_wrapper_status "failed" "app_start_failed"
  exit "${app_start_rc}"
fi

log_step "Force-recreating backend to pick up fresh tunnel/runtime env"
set +e
compose_run up -d --force-recreate backend
backend_recreate_rc=$?
set -e
if [[ "${backend_recreate_rc}" -ne 0 ]]; then
  emit_wrapper_status "failed" "backend_recreate_failed"
  exit "${backend_recreate_rc}"
fi

FRONTEND_URL="http://127.0.0.1:${FRONTEND_HOST_PORT}/"
BACKEND_HEALTH_URL="http://127.0.0.1:${BACKEND_HOST_PORT}/health"
CURATION_HEALTH_URL="http://127.0.0.1:${BACKEND_HOST_PORT}/api/admin/health/connections/curation_db"

log_step "Checking service health"
frontend_status="unreachable"
backend_status="unreachable"
curation_status="skipped"
pdf_status="skipped"

if wait_for_url "${FRONTEND_URL}" "${FRONTEND_HEALTH_ATTEMPTS}" "${FRONTEND_HEALTH_SLEEP}" >/dev/null; then
  frontend_status="healthy"
fi

backend_payload=""
if backend_payload="$(wait_for_url "${BACKEND_HEALTH_URL}" "${BACKEND_HEALTH_ATTEMPTS}" "${BACKEND_HEALTH_SLEEP}")"; then
  backend_status="$(json_compact "${backend_payload}")"
fi

if [[ "${SKIP_DB_TUNNEL}" -eq 0 ]]; then
  curation_payload=""
  if curation_payload="$(wait_for_url "${CURATION_HEALTH_URL}" "${CURATION_HEALTH_ATTEMPTS}" "${CURATION_HEALTH_SLEEP}")"; then
    curation_status="$(json_compact "${curation_payload}")"
  else
    curation_status="unreachable"
  fi
fi

if [[ -n "${PDF_EXTRACTION_SERVICE_URL:-}" ]]; then
  pdf_payload=""
  if pdf_payload="$(wait_for_url "${PDF_EXTRACTION_SERVICE_URL%/}/api/v1/health" "${PDF_HEALTH_ATTEMPTS}" "${PDF_HEALTH_SLEEP}")"; then
    pdf_status="$(json_compact "${pdf_payload}")"
  else
    pdf_status="unreachable"
  fi
fi

echo "frontend_health=${frontend_status}"
echo "backend_health=${backend_status}"
echo "curation_db_health=${curation_status}"
echo "pdf_extraction_health=${pdf_status}"
print_urls

if [[ "${backend_status}" == "unreachable" ]]; then
  backend_root_cause="$(first_backend_root_cause)"
  if [[ -n "${backend_root_cause}" ]]; then
    echo "backend_root_cause=${backend_root_cause}"
  fi
  emit_wrapper_status "partial" "backend_unreachable"
  exit 1
fi

wrapper_status="ready"
wrapper_reason="healthy"
if [[ "${frontend_status}" != "healthy" ]]; then
  wrapper_status="partial"
  wrapper_reason="frontend_${frontend_status}"
elif [[ "${curation_status}" == "unreachable" ]]; then
  wrapper_status="partial"
  wrapper_reason="curation_db_unreachable"
elif [[ "${pdf_status}" == "unreachable" ]]; then
  wrapper_status="partial"
  wrapper_reason="pdf_extraction_unreachable"
fi

emit_wrapper_status "${wrapper_status}" "${wrapper_reason}"
