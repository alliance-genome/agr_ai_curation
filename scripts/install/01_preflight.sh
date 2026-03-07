#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

EXIT_OK=0
EXIT_PREREQ_FAILURE=10
EXIT_PORT_CONFLICT=11
EXIT_MULTIPLE_FAILURES=12

MIN_MEMORY_BYTES=$((8 * 1024 * 1024 * 1024))
MIN_DISK_BYTES=$((10 * 1024 * 1024 * 1024))

DOCKER_CMD="${PREFLIGHT_DOCKER_CMD:-docker}"
GIT_CMD="${PREFLIGHT_GIT_CMD:-git}"
LSOF_CMD="${PREFLIGHT_LSOF_CMD:-lsof}"
SS_CMD="${PREFLIGHT_SS_CMD:-ss}"

declare -i prereq_failures=0
declare -i port_conflicts=0
declare -i warnings=0

bytes_to_gib() {
  local bytes="$1"
  awk -v value="$bytes" 'BEGIN { printf "%.1f", value / 1024 / 1024 / 1024 }'
}

record_prereq_failure() {
  local message="$1"
  prereq_failures+=1
  log_error "$message"
}

record_port_conflict() {
  local message="$1"
  port_conflicts+=1
  log_error "$message"
}

record_warning() {
  local message="$1"
  warnings+=1
  log_warn "$message"
}

command_exists() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1
}

resolve_memory_bytes() {
  if [[ -n "${PREFLIGHT_MEMORY_BYTES_OVERRIDE:-}" ]]; then
    printf '%s\n' "${PREFLIGHT_MEMORY_BYTES_OVERRIDE}"
    return 0
  fi

  if [[ -r /proc/meminfo ]]; then
    awk '/MemTotal:/ { print $2 * 1024; exit }' /proc/meminfo
    return 0
  fi

  if command_exists "sysctl"; then
    sysctl -n hw.memsize 2>/dev/null || true
    return 0
  fi

  return 1
}

resolve_disk_bytes() {
  if [[ -n "${PREFLIGHT_DISK_BYTES_OVERRIDE:-}" ]]; then
    printf '%s\n' "${PREFLIGHT_DISK_BYTES_OVERRIDE}"
    return 0
  fi

  df -Pk . | awk 'NR==2 { print $4 * 1024; exit }'
}

resolve_compose_major() {
  local compose_output="$1"
  local version
  local major

  version="$(printf '%s\n' "$compose_output" | grep -Eo 'v?[0-9]+(\.[0-9]+)+' | head -n1 | sed 's/^v//')"
  if [[ -z "$version" ]]; then
    return 1
  fi

  major="${version%%.*}"
  if [[ -z "$major" || ! "$major" =~ ^[0-9]+$ ]]; then
    return 1
  fi

  printf '%s\n' "$major"
}

find_port_owner() {
  local port="$1"
  local owner=""

  if command_exists "$LSOF_CMD"; then
    owner="$("$LSOF_CMD" -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 { print $1 "/" $2; exit }')"
    if [[ -n "$owner" ]]; then
      printf '%s\n' "$owner"
      return 0
    fi
  fi

  if command_exists "$SS_CMD"; then
    local ss_line
    ss_line="$("$SS_CMD" -ltnp "( sport = :${port} )" 2>/dev/null | awk 'NR>1 && $1=="LISTEN" { print; exit }')"
    if [[ -n "$ss_line" ]]; then
      owner="$(printf '%s\n' "$ss_line" | sed -n 's/.*users:(("\([^"]*\)".*pid=\([0-9]*\).*/\1\/\2/p')"
      if [[ -z "$owner" ]]; then
        owner="unknown process"
      fi
      printf '%s\n' "$owner"
      return 0
    fi
  fi

  return 1
}

check_required_tools() {
  if ! command_exists "$DOCKER_CMD"; then
    record_prereq_failure "Docker CLI not found: ${DOCKER_CMD}"
  elif "$DOCKER_CMD" info >/dev/null 2>&1; then
    log_success "Docker daemon is reachable."
  else
    record_prereq_failure "Docker daemon check failed: '${DOCKER_CMD} info' did not succeed."
  fi

  if ! command_exists "$DOCKER_CMD"; then
    record_prereq_failure "Docker Compose v2 check failed because Docker CLI is unavailable."
  else
    local compose_output
    local compose_major
    compose_output="$("$DOCKER_CMD" compose version 2>&1 || true)"
    compose_major="$(resolve_compose_major "$compose_output" || true)"
    if [[ -z "$compose_major" ]]; then
      record_prereq_failure "Unable to determine Docker Compose version from: ${compose_output}"
    elif (( compose_major < 2 )); then
      record_prereq_failure "Docker Compose 2.x+ required. Found: ${compose_output}"
    else
      log_success "Docker Compose check passed: ${compose_output}"
    fi
  fi

  if ! command_exists "$GIT_CMD"; then
    record_prereq_failure "Git CLI not found: ${GIT_CMD}"
  else
    local git_version
    git_version="$("$GIT_CMD" --version 2>/dev/null || true)"
    if [[ -z "$git_version" ]]; then
      record_prereq_failure "Git version check failed: '${GIT_CMD} --version' did not succeed."
    else
      log_success "Git check passed: ${git_version}"
    fi
  fi
}

check_memory_warning() {
  local memory_bytes
  memory_bytes="$(resolve_memory_bytes || true)"
  if [[ -z "$memory_bytes" || ! "$memory_bytes" =~ ^[0-9]+$ ]]; then
    record_warning "Unable to determine available system memory. Recommended: 8.0 GiB+ (reranker model is memory-intensive)."
    return 0
  fi

  if (( memory_bytes < MIN_MEMORY_BYTES )); then
    record_warning "Available memory is $(bytes_to_gib "$memory_bytes") GiB (< 8.0 GiB). reranker model is memory-intensive."
  else
    log_success "Memory check passed: $(bytes_to_gib "$memory_bytes") GiB available."
  fi
}

check_disk_warning() {
  local disk_bytes
  disk_bytes="$(resolve_disk_bytes || true)"
  if [[ -z "$disk_bytes" || ! "$disk_bytes" =~ ^[0-9]+$ ]]; then
    record_warning "Unable to determine free disk space. Recommended: 10.0 GiB+ for Docker images."
    return 0
  fi

  if (( disk_bytes < MIN_DISK_BYTES )); then
    record_warning "Free disk space is $(bytes_to_gib "$disk_bytes") GiB (< 10.0 GiB) for Docker images."
  else
    log_success "Disk check passed: $(bytes_to_gib "$disk_bytes") GiB free."
  fi
}

check_required_ports() {
  local port_specs=(
    "FRONTEND_HOST_PORT:3002:Frontend"
    "BACKEND_HOST_PORT:8000:Backend"
    "WEAVIATE_HTTP_HOST_PORT:8080:Weaviate HTTP"
    "WEAVIATE_GRPC_HOST_PORT:50051:Weaviate gRPC"
    "POSTGRES_HOST_PORT:5434:PostgreSQL"
    "REDIS_HOST_PORT:6379:Redis"
    "LANGFUSE_HOST_PORT:3000:Langfuse"
    "CLICKHOUSE_HTTP_HOST_PORT:8123:ClickHouse HTTP"
    "CLICKHOUSE_NATIVE_HOST_PORT:9000:ClickHouse Native"
    "MINIO_API_HOST_PORT:9090:MinIO API"
    "MINIO_CONSOLE_HOST_PORT:9091:MinIO Console"
    "LOKI_HOST_PORT:3100:Loki"
    "TRACE_REVIEW_BACKEND_PORT:8001:trace-review"
  )

  if ! command_exists "$LSOF_CMD" && ! command_exists "$SS_CMD"; then
    record_prereq_failure "Port checks require '${LSOF_CMD}' or '${SS_CMD}', but neither is available."
    return 0
  fi

  local entry
  for entry in "${port_specs[@]}"; do
    local env_var
    local default_port
    local service
    local port
    local owner

    IFS=":" read -r env_var default_port service <<<"$entry"
    port="${!env_var:-$default_port}"

    if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
      record_prereq_failure "Invalid port value for ${env_var}: ${port}"
      continue
    fi

    owner="$(find_port_owner "$port" || true)"
    if [[ -n "$owner" ]]; then
      record_port_conflict "Port ${port} in use by ${owner} (${service}; env ${env_var})"
    else
      log_success "Port ${port} available (${service}; env ${env_var})."
    fi
  done
}

emit_summary_and_exit() {
  local exit_code="$EXIT_OK"
  if (( prereq_failures > 0 && port_conflicts > 0 )); then
    exit_code="$EXIT_MULTIPLE_FAILURES"
  elif (( port_conflicts > 0 )); then
    exit_code="$EXIT_PORT_CONFLICT"
  elif (( prereq_failures > 0 )); then
    exit_code="$EXIT_PREREQ_FAILURE"
  fi

  local hard_failures=$((prereq_failures + port_conflicts))
  if (( hard_failures > 0 )); then
    log_error "Preflight failed with ${hard_failures} hard failure(s) and ${warnings} warning(s)."
  else
    log_success "Preflight passed with ${warnings} warning(s)."
  fi

  printf 'PREFLIGHT_RESULT exit_code=%d hard_failures=%d prereq_failures=%d port_conflicts=%d warnings=%d\n' \
    "$exit_code" "$hard_failures" "$prereq_failures" "$port_conflicts" "$warnings"
  exit "$exit_code"
}

main() {
  log_info "Running install preflight checks"
  check_required_tools
  check_memory_warning
  check_disk_warning
  check_required_ports
  emit_summary_and_exit
}

main "$@"
