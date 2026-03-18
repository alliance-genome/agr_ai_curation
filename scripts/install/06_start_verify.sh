#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

install_home_dir="${INSTALL_HOME_DIR:-${HOME}/.agr_ai_curation}"
env_output_path="${INSTALL_ENV_PATH:-${install_home_dir}/.env}"
pdfx_state_path="${INSTALL_PDFX_STATE_PATH:-${install_home_dir}/.install_pdfx.env}"
docker_cmd="${INSTALL_DOCKER_CMD:-docker}"
curl_cmd="${INSTALL_CURL_CMD:-curl}"
timeout_seconds="${INSTALL_START_VERIFY_TIMEOUT_SECONDS:-300}"
poll_interval_seconds="${INSTALL_START_VERIFY_POLL_INTERVAL_SECONDS:-5}"
main_compose_file="${INSTALL_MAIN_COMPOSE_FILE:-${repo_root}/docker-compose.production.yml}"

declare -a main_services=()
declare -A service_statuses=()

load_main_env() {
  set -a
  # shellcheck disable=SC1090
  source "$env_output_path"
  set +a
}

normalize_main_compose_file() {
  if [[ "$main_compose_file" != /* ]]; then
    main_compose_file="${repo_root}/${main_compose_file}"
  fi
}

run_compose() {
  local workdir="$1"
  shift

  (
    cd "$workdir"
    "$docker_cmd" compose "$@"
  )
}

run_main_compose() {
  run_compose "$install_home_dir" --env-file "$env_output_path" -f "$main_compose_file" "$@"
}

load_main_services() {
  mapfile -t main_services < <(run_main_compose config --services)
}

frontend_url() {
  printf 'http://localhost:%s\n' "${FRONTEND_HOST_PORT:-3002}"
}

backend_url() {
  printf 'http://localhost:%s\n' "${BACKEND_HOST_PORT:-8000}"
}

backend_docs_url() {
  printf '%s/docs\n' "$(backend_url)"
}

backend_health_url() {
  printf '%s/health\n' "$(backend_url)"
}

langfuse_url() {
  printf 'http://localhost:%s\n' "${LANGFUSE_HOST_PORT:-3000}"
}

langfuse_health_url() {
  printf '%s/api/public/health\n' "$(langfuse_url)"
}

trace_review_health_url() {
  printf 'http://localhost:%s/health\n' "${TRACE_REVIEW_BACKEND_HOST_PORT:-8001}"
}

service_port_label() {
  local service="$1"

  case "$service" in
    frontend) printf '%s' "${FRONTEND_HOST_PORT:-3002}" ;;
    backend) printf '%s' "${BACKEND_HOST_PORT:-8000}" ;;
    langfuse) printf '%s' "${LANGFUSE_HOST_PORT:-3000}" ;;
    trace_review_backend) printf '%s' "${TRACE_REVIEW_BACKEND_HOST_PORT:-8001}" ;;
    postgres) printf '%s' "${POSTGRES_HOST_PORT:-5434}" ;;
    redis) printf '%s' "${REDIS_HOST_PORT:-6379}" ;;
    weaviate) printf '%s' "${WEAVIATE_HTTP_HOST_PORT:-8080}" ;;
    clickhouse) printf '%s' "${CLICKHOUSE_HTTP_HOST_PORT:-8123}" ;;
    minio) printf '%s' "${MINIO_API_HOST_PORT:-9090}" ;;
    loki) printf '%s' "${LOKI_HOST_PORT:-3100}" ;;
    *) printf '%s' "-" ;;
  esac
}

container_health_state() {
  local service="$1"
  local container_id=""
  local container_ids=()

  mapfile -t container_ids < <(run_main_compose ps -q "$service" 2>/dev/null || true)
  container_id="${container_ids[0]:-}"
  if [[ -z "$container_id" ]]; then
    printf '%s\n' "missing"
    return 0
  fi

  "$docker_cmd" inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id"
}

container_status_ok() {
  local service="$1"
  local state=""

  state="$(container_health_state "$service")"
  [[ "$state" == "healthy" || "$state" == "running" ]]
}

check_http_ok() {
  local url="$1"
  "$curl_cmd" -fs -o /dev/null --max-time 5 "$url" 2>/dev/null
}

refresh_service_statuses() {
  local service=""
  local endpoint_ok=1

  service_statuses=()

  for service in "${main_services[@]}"; do
    if ! container_status_ok "$service"; then
      service_statuses["$service"]="FAIL"
      continue
    fi

    endpoint_ok=1
    case "$service" in
      backend)
        check_http_ok "$(backend_health_url)" || endpoint_ok=0
        ;;
      frontend)
        check_http_ok "$(frontend_url)" || endpoint_ok=0
        ;;
      langfuse)
        check_http_ok "$(langfuse_health_url)" || endpoint_ok=0
        ;;
      trace_review_backend)
        check_http_ok "$(trace_review_health_url)" || endpoint_ok=0
        ;;
    esac

    if (( endpoint_ok == 1 )); then
      service_statuses["$service"]="OK"
    else
      service_statuses["$service"]="FAIL"
    fi
  done

  if [[ -n "${PDF_EXTRACTION_SERVICE_URL:-}" ]]; then
    if check_http_ok "${PDF_EXTRACTION_SERVICE_URL%/}/api/v1/health"; then
      service_statuses["pdf_extraction"]="OK"
    else
      service_statuses["pdf_extraction"]="FAIL"
    fi
  else
    service_statuses["pdf_extraction"]="Skipped"
  fi
}

pending_checks() {
  local pending=()
  local service=""

  for service in "${main_services[@]}"; do
    if [[ "${service_statuses[$service]:-FAIL}" != "OK" ]]; then
      pending+=("$service")
    fi
  done

  if [[ -n "${PDF_EXTRACTION_SERVICE_URL:-}" ]] && [[ "${service_statuses[pdf_extraction]:-FAIL}" != "OK" ]]; then
    pending+=("pdf_extraction")
  fi

  if (( ${#pending[@]} > 0 )); then
    printf '%s\n' "${pending[*]}"
  fi
}

wait_for_health_checks() {
  local elapsed=0
  local pending=""

  while (( elapsed <= timeout_seconds )); do
    refresh_service_statuses
    pending="$(pending_checks)"

    if [[ -z "$pending" ]]; then
      printf '\r'
      log_success "All health checks passed"
      return 0
    fi

    printf '\r[INFO] Waiting for health checks (%ss/%ss): %s   ' "$elapsed" "$timeout_seconds" "$pending"
    sleep "$poll_interval_seconds"
    elapsed=$((elapsed + poll_interval_seconds))
  done

  printf '\n'
  log_warn "Timed out waiting for health checks after ${timeout_seconds}s"
  return 1
}

pdfx_port_label() {
  if [[ "${PDF_EXTRACTION_SERVICE_URL:-}" =~ :([0-9]+)(/|$) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi

  printf '%s\n' "-"
}

require_runtime_var() {
  local key="$1"
  local value="$2"

  require_non_empty "$key" "$value" || {
    log_error "Re-run Stage 2: Core Configuration to regenerate the installed runtime layout."
    return 1
  }
}

validate_runtime_layout() {
  require_file_exists "$main_compose_file"

  require_runtime_var "AGR_RUNTIME_CONFIG_HOST_DIR" "${AGR_RUNTIME_CONFIG_HOST_DIR:-}" || return 1
  require_runtime_var "AGR_RUNTIME_PACKAGES_HOST_DIR" "${AGR_RUNTIME_PACKAGES_HOST_DIR:-}" || return 1
  require_runtime_var "AGR_RUNTIME_STATE_HOST_DIR" "${AGR_RUNTIME_STATE_HOST_DIR:-}" || return 1
  require_runtime_var "PDF_STORAGE_HOST_DIR" "${PDF_STORAGE_HOST_DIR:-}" || return 1
  require_runtime_var "FILE_OUTPUT_STORAGE_HOST_DIR" "${FILE_OUTPUT_STORAGE_HOST_DIR:-}" || return 1
  require_runtime_var "WEAVIATE_DATA_HOST_DIR" "${WEAVIATE_DATA_HOST_DIR:-}" || return 1

  require_directory_exists "${AGR_RUNTIME_CONFIG_HOST_DIR}"
  require_directory_exists "${AGR_RUNTIME_PACKAGES_HOST_DIR}"
  require_directory_exists "${AGR_RUNTIME_STATE_HOST_DIR}"
  require_directory_exists "${PDF_STORAGE_HOST_DIR}"
  require_directory_exists "${FILE_OUTPUT_STORAGE_HOST_DIR}"
  require_directory_exists "${WEAVIATE_DATA_HOST_DIR}"

  require_file_exists "${AGR_RUNTIME_PACKAGES_HOST_DIR}/core/package.yaml"
}

print_runtime_layout() {
  echo "  Compose file: ${main_compose_file}"
  echo "  Runtime config: ${AGR_RUNTIME_CONFIG_HOST_DIR}"
  echo "  Runtime packages: ${AGR_RUNTIME_PACKAGES_HOST_DIR}"
  echo "  Runtime state: ${AGR_RUNTIME_STATE_HOST_DIR}"
  echo "  PDF storage: ${PDF_STORAGE_HOST_DIR}"
  echo "  File outputs: ${FILE_OUTPUT_STORAGE_HOST_DIR}"
  echo "  Weaviate data: ${WEAVIATE_DATA_HOST_DIR}"
  echo
}

print_status_table() {
  local service=""

  echo
  echo "Service Status"
  printf '%-24s %-10s %-8s\n' "Service" "Port" "Status"
  printf '%-24s %-10s %-8s\n' "-------" "----" "------"

  for service in "${main_services[@]}"; do
    printf '%-24s %-10s %-8s\n' "$service" "$(service_port_label "$service")" "${service_statuses[$service]:-FAIL}"
  done

  printf '%-24s %-10s %-8s\n' "pdf_extraction" "$(pdfx_port_label)" "${service_statuses[pdf_extraction]:-Skipped}"
}

print_summary() {
  local auth_mode="oidc"

  if [[ "${AUTH_PROVIDER:-}" == "dev" || "${DEV_MODE:-false}" == "true" ]]; then
    auth_mode="dev"
  fi

  echo
  echo "URLs"
  echo "Application: $(frontend_url)"
  echo "API Docs: $(backend_docs_url)"
  echo "Langfuse: $(langfuse_url)"
  echo "Health: $(backend_health_url)"
  echo "Compose file: ${main_compose_file}"
  echo "Runtime config: ${AGR_RUNTIME_CONFIG_HOST_DIR}"
  echo "Runtime packages: ${AGR_RUNTIME_PACKAGES_HOST_DIR}"
  echo "Runtime state: ${AGR_RUNTIME_STATE_HOST_DIR}"
  echo
  echo "Auth mode: ${auth_mode}"
  if [[ "$auth_mode" == "dev" ]]; then
    echo "Next steps: open the application URL and sign in with dev mode enabled."
  else
    echo "Next steps: open the application URL and complete your configured OIDC sign-in flow."
  fi
  echo "Restart command: docker compose --env-file ${env_output_path} -f ${main_compose_file} up -d"
  echo "Alliance note: deploy_alliance.sh is Alliance-internal only."
}

start_pdfx_stack_if_configured() {
  if [[ -z "${PDF_EXTRACTION_SERVICE_URL:-}" ]]; then
    log_info "PDF extraction service not configured; skipping PDFX stack startup"
    return 0
  fi

  if [[ ! -f "$pdfx_state_path" ]]; then
    log_error "PDFX state file not found: ${pdfx_state_path}. Re-run Stage 5 without skipping PDF extraction setup to regenerate it."
    exit 1
  fi
  # shellcheck disable=SC1090
  source "$pdfx_state_path"
  require_non_empty "INSTALL_PDFX_CLONE_PATH" "${INSTALL_PDFX_CLONE_PATH:-}"

  if [[ ! -d "${INSTALL_PDFX_CLONE_PATH}" ]]; then
    log_error "PDF extraction clone path does not exist: ${INSTALL_PDFX_CLONE_PATH}"
    exit 1
  fi

  local pdfx_compose_file="docker-compose.yml"
  if [[ "${INSTALL_PDFX_GPU_ENABLED:-false}" == "true" ]]; then
    pdfx_compose_file="docker-compose.gpu.yml"
  fi

  log_info "Starting PDF extraction stack (${pdfx_compose_file})"
  run_compose "${INSTALL_PDFX_CLONE_PATH}/deploy" --env-file "${INSTALL_PDFX_CLONE_PATH}/.env" -f "$pdfx_compose_file" up -d
}

start_main_stack() {
  log_info "Starting main stack"
  RUN_DB_BOOTSTRAP_ON_START=true RUN_DB_MIGRATIONS_ON_START=true run_main_compose up -d
}

main() {
  require_file_exists "$env_output_path"
  require_command "$docker_cmd"
  require_command "$curl_cmd"
  normalize_main_compose_file

  echo
  log_info "=== Stage 6 of 6: Start & Verify ==="
  echo
  echo "  Launching the standalone production stack from published images and verifying it's healthy."
  echo
  echo "  This will:"
  echo "    - Pull published Docker images (first run can take 5-15 minutes on a fresh machine)"
  echo "    - Use the extracted bundle compose file plus the installed runtime/data directories under ${install_home_dir}"
  echo "    - Start the database, vector store, backend, frontend, diagnostics, and observability stack"
  echo "    - Run database migrations automatically"
  echo "    - Poll health endpoints until everything reports OK (up to 5 min timeout)"
  echo
  echo "  If the PDF extraction service was configured in Stage 5, it will be"
  echo "  started first in its own Docker Compose stack."
  echo "  No application images are built from source in this stage."
  echo
  local yellow='\033[1;33m'
  local reset='\033[0m'
  if ! supports_color; then yellow="" reset=""; fi
  printf "  ${yellow}This is the slowest stage -- first run can take 5-15 minutes${reset}\n"
  printf "  ${yellow}while Docker pulls the published images. Grab a coffee!${reset}\n"
  printf "\n"
  printf "  ${yellow}NOTE: You will see docker WARN messages, curl errors, and 404s${reset}\n"
  printf "  ${yellow}during startup -- this is normal while services are initializing.${reset}\n"
  printf "  ${yellow}The health check loop will keep retrying until everything is up.${reset}\n"
  echo

  load_main_env
  validate_runtime_layout
  print_runtime_layout
  start_pdfx_stack_if_configured
  start_main_stack
  load_main_services

  if ! wait_for_health_checks; then
    refresh_service_statuses
    print_status_table
    print_summary
    echo
    echo "  Troubleshooting:"
    echo "    - Check container logs:    docker compose --env-file ${env_output_path} -f ${main_compose_file} logs <service>"
    echo "    - Restart a single service: docker compose --env-file ${env_output_path} -f ${main_compose_file} restart <service>"
    echo "    - Re-run this stage:        scripts/install/install.sh --from-stage 6"
    echo
    log_error "One or more services failed verification"
    exit 1
  fi

  print_status_table
  print_summary
  log_success "Standalone install is ready"
}

main "$@"
