#!/usr/bin/env bash
# Shared helpers for Symphony production Loki read-only tunnel management.

if [[ -n "${PROD_LOKI_TUNNEL_COMMON_SH_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
PROD_LOKI_TUNNEL_COMMON_SH_LOADED=1

prod_loki_info() {
  echo "$*"
}

prod_loki_warn() {
  echo "$*" >&2
}

prod_loki_error() {
  echo "$*" >&2
}

prod_loki_repo_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "${script_dir}/../.." && pwd
}

prod_loki_endpoint_file() {
  local repo_root="${1:-$(prod_loki_repo_root)}"
  echo "${SYMPHONY_PROD_LOKI_ENDPOINT_FILE:-${repo_root}/.symphony/prod_loki_endpoint.env}"
}

prod_loki_state_root() {
  local explicit_root="${SYMPHONY_PROD_LOKI_STATE_ROOT:-}"
  local candidates=()
  local candidate

  if [[ -n "${explicit_root}" ]]; then
    candidates+=("${explicit_root}")
  else
    if [[ -n "${HOME:-}" ]]; then
      candidates+=("${HOME}/.local/state/agr_ai_curation_symphony_prod_loki")
    fi
    if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
      candidates+=("${XDG_RUNTIME_DIR}/agr_ai_curation_symphony_prod_loki")
    fi
    candidates+=("/tmp/agr_ai_curation_symphony_prod_loki")
  fi

  for candidate in "${candidates[@]}"; do
    if mkdir -p "${candidate}" >/dev/null 2>&1; then
      echo "${candidate}"
      return 0
    fi
  done

  prod_loki_error "Unable to create a writable state root for production Loki tunnel metadata"
  return 1
}

prod_loki_state_file() {
  echo "$(prod_loki_state_root)/tunnel.state"
}

prod_loki_pid_running() {
  local pid="${1:-}"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

prod_loki_spawn_detached() {
  local pid_file="$1"
  local log_file="$2"
  shift 2

  mkdir -p "$(dirname "${pid_file}")" "$(dirname "${log_file}")"
  rm -f "${pid_file}"

  if command -v setsid >/dev/null 2>&1; then
    setsid bash -c '
      pid_file="$1"
      log_file="$2"
      shift 2
      umask 077
      mkdir -p "$(dirname "${pid_file}")" "$(dirname "${log_file}")"
      exec </dev/null >>"${log_file}" 2>&1
      echo "$$" > "${pid_file}"
      exec "$@"
    ' bash "${pid_file}" "${log_file}" "$@" &
  else
    nohup bash -c '
      pid_file="$1"
      log_file="$2"
      shift 2
      umask 077
      mkdir -p "$(dirname "${pid_file}")" "$(dirname "${log_file}")"
      exec </dev/null >>"${log_file}" 2>&1
      echo "$$" > "${pid_file}"
      exec "$@"
    ' bash "${pid_file}" "${log_file}" "$@" >/dev/null 2>&1 &
  fi

  local launcher_pid=$!
  local i
  for i in $(seq 1 20); do
    if [[ -s "${pid_file}" ]]; then
      cat "${pid_file}"
      return 0
    fi
    if ! kill -0 "${launcher_pid}" 2>/dev/null; then
      break
    fi
    sleep 0.2
  done

  return 1
}

prod_loki_tcp_ready() {
  local host="$1"
  local port="$2"
  (exec 3<>"/dev/tcp/${host}/${port}") >/dev/null 2>&1
}

prod_loki_wait_for_http_ready() {
  local url="$1"
  local max_iterations="${2:-30}"
  local sleep_interval="${3:-1}"
  local i

  for i in $(seq 1 "${max_iterations}"); do
    if curl -fsS -m 3 "${url%/}/ready" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${sleep_interval}"
  done

  return 1
}

prod_loki_validate_port() {
  local port="$1"
  [[ "${port}" =~ ^[0-9]+$ ]] && [[ "${port}" -gt 0 && "${port}" -le 65535 ]]
}

prod_loki_is_lan_bind_ip() {
  local ip="$1"
  [[ "${ip}" =~ ^192\.168\. ]] || [[ "${ip}" =~ ^169\.254\. ]]
}

prod_loki_validate_bind_ip() {
  local bind_ip="$1"

  if [[ -z "${bind_ip}" ]]; then
    prod_loki_error "Bind IP cannot be empty"
    return 1
  fi
  if [[ "${bind_ip}" == "0.0.0.0" || "${bind_ip}" == "::" ]]; then
    prod_loki_error "Refusing to bind production Loki proxy to ${bind_ip}"
    return 1
  fi
  if [[ "${bind_ip}" == "127.0.0.1" && "${SYMPHONY_PROD_LOKI_ALLOW_LOCALHOST_BIND:-0}" != "1" ]]; then
    prod_loki_error "Refusing localhost bind for VM-facing production Loki proxy; set SYMPHONY_PROD_LOKI_ALLOW_LOCALHOST_BIND=1 only for tests"
    return 1
  fi
  if prod_loki_is_lan_bind_ip "${bind_ip}" && [[ "${SYMPHONY_PROD_LOKI_ALLOW_LAN_BIND:-0}" != "1" ]]; then
    prod_loki_error "Refusing LAN-facing production Loki proxy bind ${bind_ip}; set SYMPHONY_PROD_LOKI_ALLOW_LAN_BIND=1 only when intentional"
    return 1
  fi
}

prod_loki_validate_incus_bind_ip() {
  local bind_ip="$1"
  local expected_ip

  if [[ "${SYMPHONY_PROD_LOKI_ALLOW_NON_INCUS_BIND:-0}" == "1" ]]; then
    return 0
  fi
  if [[ "${bind_ip}" == "127.0.0.1" && "${SYMPHONY_PROD_LOKI_ALLOW_LOCALHOST_BIND:-0}" == "1" ]]; then
    return 0
  fi

  expected_ip="$(prod_loki_discover_incus_host_ip 2>/dev/null || true)"
  if [[ -z "${expected_ip}" ]]; then
    prod_loki_error "Unable to verify ${bind_ip} as the Incus host address. Set SYMPHONY_PROD_LOKI_ALLOW_NON_INCUS_BIND=1 only when intentional."
    return 1
  fi
  if [[ "${bind_ip}" != "${expected_ip}" ]]; then
    prod_loki_error "Refusing production Loki proxy bind ${bind_ip}; expected Incus host address ${expected_ip}. Set SYMPHONY_PROD_LOKI_ALLOW_NON_INCUS_BIND=1 only when intentional."
    return 1
  fi
}

prod_loki_discover_incus_host_ip() {
  if command -v incus >/dev/null 2>&1; then
    local ip
    ip="$(
      incus --project "${SYMPHONY_INCUS_PROJECT:-default}" exec "${SYMPHONY_INCUS_VM_NAME:-symphony-main}" -- \
        sh -lc "ip route | awk '/^default/ {print \$3; exit}'" 2>/dev/null || true
    )"
    if [[ -n "${ip}" ]]; then
      echo "${ip}"
      return 0
    fi
  fi
  return 1
}

prod_loki_default_bind_ip() {
  if [[ -n "${SYMPHONY_PROD_LOKI_BIND_IP:-}" ]]; then
    echo "${SYMPHONY_PROD_LOKI_BIND_IP}"
    return 0
  fi

  local discovered_ip
  discovered_ip="$(prod_loki_discover_incus_host_ip || true)"
  if [[ -n "${discovered_ip}" ]]; then
    echo "${discovered_ip}"
    return 0
  fi

  prod_loki_error "Unable to discover host Incus bind IP. Pass --bind-ip or set SYMPHONY_PROD_LOKI_BIND_IP."
  return 1
}

prod_loki_write_endpoint_file() {
  local endpoint_file="$1"
  local loki_url="$2"

  mkdir -p "$(dirname "${endpoint_file}")"
  {
    echo "# Non-secret Symphony production Loki endpoint."
    echo "# Generated by symphony_prod_loki_host_start.sh."
    printf 'export LOKI_URL=%q\n' "${loki_url}"
    echo "export LOKI_TUNNEL_OWNER=host"
    echo "export LOKI_TUNNEL_MODE=readonly-proxy"
  } > "${endpoint_file}"
  chmod 0644 "${endpoint_file}"
}

prod_loki_vm_repo_root() {
  echo "${SYMPHONY_PROD_LOKI_VM_REPO_ROOT:-/home/ctabone/programming/claude_code/analysis/alliance/ai_curation_new/agr_ai_curation}"
}

prod_loki_sync_endpoint_file_to_vm() {
  local endpoint_file="$1"
  local vm_repo_root
  local vm_name="${SYMPHONY_INCUS_VM_NAME:-symphony-main}"
  local project="${SYMPHONY_INCUS_PROJECT:-default}"

  if [[ "${SYMPHONY_PROD_LOKI_SKIP_VM_ENDPOINT_SYNC:-0}" == "1" ]]; then
    return 0
  fi
  if ! command -v incus >/dev/null 2>&1; then
    return 0
  fi
  if [[ ! -f "${endpoint_file}" ]]; then
    prod_loki_warn "Cannot sync missing endpoint file to VM: ${endpoint_file}"
    return 1
  fi

  vm_repo_root="$(prod_loki_vm_repo_root)"
  incus --project "${project}" exec "${vm_name}" -- mkdir -p "${vm_repo_root}/.symphony" >/dev/null 2>&1 || {
    prod_loki_warn "Could not prepare VM endpoint directory; agents may need manual endpoint sync."
    return 1
  }
  incus --project "${project}" file push "${endpoint_file}" "${vm_name}${vm_repo_root}/.symphony/prod_loki_endpoint.env" >/dev/null 2>&1 || {
    prod_loki_warn "Could not sync production Loki endpoint file into ${vm_name}; agents may need manual endpoint sync."
    return 1
  }
}

prod_loki_load_endpoint_file() {
  local endpoint_file="$1"
  if [[ ! -f "${endpoint_file}" ]]; then
    prod_loki_error "Missing production Loki endpoint file: ${endpoint_file}"
    return 1
  fi
  # shellcheck disable=SC1090
  source "${endpoint_file}"
  if [[ -z "${LOKI_URL:-}" ]]; then
    prod_loki_error "Endpoint file did not define LOKI_URL: ${endpoint_file}"
    return 1
  fi
}

prod_loki_urlencode() {
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""), end="")' "$1"
}
