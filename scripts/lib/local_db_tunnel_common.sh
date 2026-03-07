#!/usr/bin/env bash
# Shared helpers for local/Symphony curation DB tunnel management.

if [[ -n "${LOCAL_DB_TUNNEL_COMMON_SH_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
LOCAL_DB_TUNNEL_COMMON_SH_LOADED=1

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

local_db_tunnel_info() {
  echo -e "${GREEN}$*${NC}"
}

local_db_tunnel_warn() {
  echo -e "${YELLOW}$*${NC}"
}

local_db_tunnel_error() {
  echo -e "${RED}$*${NC}" >&2
}

local_db_tunnel_repo_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "${script_dir}/../.." && pwd
}

local_db_tunnel_load_private_env() {
  local agr_env_file="${AGR_AI_CURATION_ENV_FILE:-${HOME}/.agr_ai_curation/.env}"
  if [[ -f "${agr_env_file}" ]]; then
    local_db_tunnel_info "🔐 Loading local environment: ${agr_env_file}"
    set -a
    # shellcheck disable=SC1090
    source "${agr_env_file}"
    set +a
  fi
}

local_db_tunnel_require_prereqs() {
  if ! command -v aws >/dev/null 2>&1; then
    local_db_tunnel_error "❌ AWS CLI not found. Please install it first."
    return 1
  fi

  if ! command -v session-manager-plugin >/dev/null 2>&1; then
    local_db_tunnel_error "❌ Session Manager plugin not found. Please install it first."
    echo "   https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html" >&2
    return 1
  fi

  if ! command -v jq >/dev/null 2>&1; then
    local_db_tunnel_error "❌ jq not found. Please install it first."
    return 1
  fi

  if ! command -v socat >/dev/null 2>&1; then
    local_db_tunnel_error "❌ socat not found. Please install it first."
    return 1
  fi

  if ! command -v pg_isready >/dev/null 2>&1; then
    local_db_tunnel_warn "⚠️  PostgreSQL client tools not found. Install with: sudo apt-get install postgresql-client"
  fi
}

local_db_tunnel_load_remote_config() {
  local config db_creds

  local_db_tunnel_info "📦 Retrieving SSM instance ID from AWS..."
  config="$(
    aws secretsmanager get-secret-value \
      --profile "${AWS_PROFILE:-ctabone}" \
      --secret-id "${SSM_CONFIG_SECRET_ID:-/claude-code-pr/config}" \
      --query SecretString --output text 2>&1
  )" || {
    local_db_tunnel_error "❌ Failed to retrieve config from AWS Secrets Manager"
    echo "   Make sure you have AWS credentials configured (aws configure --profile ${AWS_PROFILE:-ctabone})" >&2
    return 1
  }

  SSM_INSTANCE_ID="$(echo "${config}" | jq -r .ssm_instance_id)"
  if [[ -z "${SSM_INSTANCE_ID}" || "${SSM_INSTANCE_ID}" == "null" ]]; then
    local_db_tunnel_error "❌ SSM instance ID missing from /claude-code-pr/config"
    return 1
  fi

  local_db_tunnel_info "🔑 Retrieving database connection details from ai-curation secret..."
  db_creds="$(
    aws secretsmanager get-secret-value \
      --profile "${AWS_PROFILE:-ctabone}" \
      --secret-id "${CURATION_DB_SECRET_ID:-ai-curation/db/curation-readonly}" \
      --query SecretString --output text
  )"

  DB_HOST="$(echo "${db_creds}" | jq -r .host)"
  DB_PORT="$(echo "${db_creds}" | jq -r .port)"
  DB_NAME="$(echo "${db_creds}" | jq -r .dbname)"
  DB_USER="$(echo "${db_creds}" | jq -r .username)"
  DB_PASSWORD="$(echo "${db_creds}" | jq -r .password)"

  local var
  for var in DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD; do
    if [[ -z "${!var}" || "${!var}" == "null" ]]; then
      local_db_tunnel_error "❌ Missing required field in ai-curation secret: ${var}"
      return 1
    fi
  done

  local_db_tunnel_info "✅ Using database: ${DB_HOST}:${DB_PORT}/${DB_NAME}"
  local_db_tunnel_info "✅ Using credentials: ${DB_USER}"
}

local_db_tunnel_list_used_ports() {
  if command -v ss >/dev/null 2>&1; then
    ss -lnt | awk 'NR>1{print $4}' | awk -F: '{print $NF}' | sort -un
    return
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $9}' | awk -F: '{print $NF}' | sort -un
    return
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -lnt 2>/dev/null | awk 'NR>2{print $4}' | awk -F: '{print $NF}' | sort -un
    return
  fi
}

local_db_tunnel_port_in_use() {
  local port="$1"
  local used_ports
  used_ports="$(local_db_tunnel_list_used_ports || true)"
  echo "${used_ports}" | grep -q "^${port}$"
}

local_db_tunnel_choose_ports() {
  local used_ports candidate_ports port

  used_ports="$(local_db_tunnel_list_used_ports || true)"

  LOCAL_PORT="${CURATION_DB_TUNNEL_LOCAL_PORT:-}"
  if [[ -n "${LOCAL_PORT}" ]]; then
    if echo "${used_ports}" | grep -q "^${LOCAL_PORT}$"; then
      local_db_tunnel_error "❌ Requested CURATION_DB_TUNNEL_LOCAL_PORT=${LOCAL_PORT} is already in use"
      return 1
    fi
  else
    if command -v shuf >/dev/null 2>&1; then
      candidate_ports="$(seq 5500 6500 | shuf)"
    else
      candidate_ports="$(seq 5500 6500)"
    fi

    for port in ${candidate_ports}; do
      if ! echo "${used_ports}" | grep -q "^${port}$"; then
        LOCAL_PORT="${port}"
        break
      fi
    done
  fi

  if [[ -z "${LOCAL_PORT}" ]]; then
    LOCAL_PORT=5555
  fi

  DOCKER_PORT="${CURATION_DB_TUNNEL_DOCKER_PORT:-$((LOCAL_PORT + 1))}"
  if [[ "${DOCKER_PORT}" == "${LOCAL_PORT}" ]]; then
    local_db_tunnel_error "❌ CURATION_DB_TUNNEL_DOCKER_PORT must differ from CURATION_DB_TUNNEL_LOCAL_PORT"
    return 1
  fi
  if echo "${used_ports}" | grep -q "^${DOCKER_PORT}$"; then
    local_db_tunnel_error "❌ Requested CURATION_DB_TUNNEL_DOCKER_PORT=${DOCKER_PORT} is already in use"
    return 1
  fi
}

local_db_tunnel_resolve_docker_gateway_ip() {
  if [[ -n "${CURATION_DB_TUNNEL_BIND_IP:-}" ]]; then
    echo "${CURATION_DB_TUNNEL_BIND_IP}"
    return 0
  fi

  if command -v docker >/dev/null 2>&1; then
    local docker_gateway
    docker_gateway="$(
      docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true
    )"
    if [[ -n "${docker_gateway}" && "${docker_gateway}" != "<no value>" ]]; then
      echo "${docker_gateway}"
      return 0
    fi
  fi

  if command -v ip >/dev/null 2>&1; then
    local bridge_ip
    bridge_ip="$(
      ip -4 addr show docker0 2>/dev/null | awk '/inet / {print $2}' | cut -d/ -f1 | head -n 1
    )"
    if [[ -n "${bridge_ip}" ]]; then
      echo "${bridge_ip}"
      return 0
    fi
  fi

  if command -v getent >/dev/null 2>&1; then
    local host_ip
    host_ip="$(getent hosts host.docker.internal 2>/dev/null | awk '{print $1; exit}')"
    if [[ -n "${host_ip}" ]]; then
      echo "${host_ip}"
      return 0
    fi
  fi

  echo "127.0.0.1"
}

local_db_tunnel_forward_host() {
  echo "${CURATION_DB_TUNNEL_FORWARD_HOST:-host.docker.internal}"
}

local_db_tunnel_urlencode() {
  printf '%s' "$1" | jq -sRr @uri
}

local_db_tunnel_write_env_file() {
  local env_file="$1"
  local db_name="$2"
  local db_user="$3"
  local db_password="$4"
  local local_port="$5"
  local docker_port="$6"
  local bind_ip="$7"
  local forward_host="$8"
  local encoded_password

  encoded_password="$(local_db_tunnel_urlencode "${db_password}")"

  mkdir -p "$(dirname "${env_file}")"
  {
    cat <<EOF
# Source this file before running tests:
# source ~/.agr_ai_curation/.env
# source ${env_file}

export PERSISTENT_STORE_DB_HOST=localhost
export PERSISTENT_STORE_DB_PORT=${local_port}
export PERSISTENT_STORE_DB_NAME=${db_name}
export PERSISTENT_STORE_DB_USERNAME=${db_user}
EOF
    printf 'export PERSISTENT_STORE_DB_PASSWORD=%q\n' "${db_password}"
    cat <<EOF
export CURATION_DB_TUNNEL_LOCAL_PORT=${local_port}
export CURATION_DB_TUNNEL_DOCKER_PORT=${docker_port}
export CURATION_DB_TUNNEL_BIND_IP=${bind_ip}
export CURATION_DB_TUNNEL_FORWARD_HOST=${forward_host}
export CURATION_DB_URL="postgresql://${db_user}:${encoded_password}@${forward_host}:${docker_port}/${db_name}"

# Docker connection details (via socat):
# Host: ${forward_host}
# Port: ${docker_port}
#
# Host-shell tools should continue using localhost:${local_port} directly.
EOF
  } > "${env_file}"
  chmod 600 "${env_file}"
}

local_db_tunnel_hash_string() {
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "$1" | sha256sum | awk '{print substr($1, 1, 12)}'
    return 0
  fi
  cksum <<<"$1" | awk '{print $1}'
}

local_db_tunnel_state_root() {
  local explicit_root="${SYMPHONY_LOCAL_DB_TUNNEL_STATE_ROOT:-}"
  local candidates=()
  local candidate

  if [[ -n "${explicit_root}" ]]; then
    candidates+=("${explicit_root}")
  else
    if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
      candidates+=("${XDG_RUNTIME_DIR}/agr_ai_curation_symphony_db_tunnels")
    fi
    if [[ -n "${HOME:-}" ]]; then
      candidates+=("${HOME}/.local/state/agr_ai_curation_symphony_db_tunnels")
    fi
    candidates+=("/tmp/agr_ai_curation_symphony_db_tunnels")
  fi

  for candidate in "${candidates[@]}"; do
    if mkdir -p "${candidate}" >/dev/null 2>&1; then
      echo "${candidate}"
      return 0
    fi
  done

  local_db_tunnel_error "❌ Unable to create a writable state root for Symphony DB tunnel metadata"
  return 1
}

local_db_tunnel_state_dir() {
  local workspace_dir="${1:-$PWD}"
  local root
  local safe_name hash
  root="$(local_db_tunnel_state_root)"
  safe_name="$(basename "${workspace_dir}" | tr -cs 'A-Za-z0-9._-' '-')"
  hash="$(local_db_tunnel_hash_string "${workspace_dir}")"
  echo "${root}/${safe_name}-${hash}"
}

local_db_tunnel_pid_running() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

local_db_tunnel_tcp_ready() {
  local host="$1"
  local port="$2"
  (exec 3<>"/dev/tcp/${host}/${port}") >/dev/null 2>&1
}

local_db_tunnel_wait_for_tunnel() {
  local local_port="$1"
  local max_iterations="${2:-60}"
  local sleep_interval="${3:-2}"
  local i

  for i in $(seq 1 "${max_iterations}"); do
    if command -v pg_isready >/dev/null 2>&1; then
      if pg_isready -h localhost -p "${local_port}" -t 2 >/dev/null 2>&1; then
        return 0
      fi
    elif local_db_tunnel_tcp_ready "127.0.0.1" "${local_port}"; then
      return 0
    fi

    sleep "${sleep_interval}"
  done

  return 1
}

local_db_tunnel_wait_for_listener() {
  local host="$1"
  local port="$2"
  local max_iterations="${3:-30}"
  local sleep_interval="${4:-1}"
  local i

  for i in $(seq 1 "${max_iterations}"); do
    if local_db_tunnel_tcp_ready "${host}" "${port}"; then
      return 0
    fi
    sleep "${sleep_interval}"
  done

  return 1
}

local_db_tunnel_local_probe_ready() {
  local local_port="$1"
  local max_iterations="${2:-1}"
  local sleep_interval="${3:-0}"
  local i

  for i in $(seq 1 "${max_iterations}"); do
    if local_db_tunnel_wait_for_tunnel "${local_port}" 1 0; then
      return 0
    fi
    sleep "${sleep_interval}"
  done

  return 1
}

local_db_tunnel_extract_session_id() {
  local log_file="$1"
  if [[ -f "${log_file}" ]]; then
    grep -oE 'SessionId: [A-Za-z0-9-]+' "${log_file}" | awk '{print $2}' | tail -n 1 || true
  fi
}
