#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=scripts/install/lib/common.sh
source "${repo_root}/scripts/install/lib/common.sh"

EXIT_OK=0
EXIT_HEALTH_FAILURE=20
EXIT_CONFIG_FAILURE=21
EXIT_MULTIPLE_FAILURES=22

curl_cmd="${TRACE_REVIEW_PREFLIGHT_CURL_CMD:-curl}"
ss_cmd="${TRACE_REVIEW_PREFLIGHT_SS_CMD:-ss}"
nc_cmd="${TRACE_REVIEW_PREFLIGHT_NC_CMD:-nc}"
ip_cmd="${TRACE_REVIEW_PREFLIGHT_IP_CMD:-ip}"
getent_cmd="${TRACE_REVIEW_PREFLIGHT_GETENT_CMD:-getent}"
python_cmd="${TRACE_REVIEW_PREFLIGHT_PYTHON_CMD:-python3}"
timeout_seconds="${TRACE_REVIEW_PREFLIGHT_TIMEOUT_SECONDS:-5}"

backend_url=""
selected_source="${TRACE_REVIEW_PREFLIGHT_SOURCE:-remote}"
require_production_readiness="${TRACE_REVIEW_PREFLIGHT_REQUIRE_PRODUCTION:-false}"
ssh_host="${TRACE_REVIEW_PRODUCTION_SSH_HOST:-}"
ssh_port="${TRACE_REVIEW_PRODUCTION_SSH_PORT:-22}"

declare -i health_failures=0
declare -i config_failures=0
declare -i warnings=0

usage() {
  cat <<'EOF'
Usage: scripts/testing/trace_review_preflight.sh [--backend-url URL] [--source remote|local]

Runs report-only TraceReview diagnostics:
- local TraceReview backend health and backend identity
- Langfuse source selection, credential presence, and health
- port 8001 listener/proxy confusion hints
- production-readiness hints for VPN route, SSH TCP reachability, and non-secret env presence

Environment:
  TRACE_REVIEW_PREFLIGHT_SOURCE              Default trace source when --source is omitted
  TRACE_REVIEW_PREFLIGHT_TIMEOUT_SECONDS     curl/TCP timeout in seconds
  TRACE_REVIEW_PREFLIGHT_REQUIRE_PRODUCTION  true to make production-readiness warnings hard failures
  TRACE_REVIEW_BACKEND_HOST_PORT             TraceReview backend host port when --backend-url is omitted
  TRACE_REVIEW_PRODUCTION_SSH_HOST           Optional production SSH host to TCP-probe on port 22
  TRACE_REVIEW_PRODUCTION_SSH_PORT           Optional production SSH port, default 22
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend-url)
      backend_url="${2:?--backend-url requires a value}"
      shift 2
      ;;
    --source)
      selected_source="${2:?--source requires a value}"
      shift 2
      ;;
    --ssh-host)
      ssh_host="${2:?--ssh-host requires a value}"
      shift 2
      ;;
    --ssh-port)
      ssh_port="${2:?--ssh-port requires a value}"
      shift 2
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

command_exists() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1
}

record_health_failure() {
  local message="$1"
  health_failures+=1
  log_error "$message"
}

record_config_failure() {
  local message="$1"
  config_failures+=1
  log_error "$message"
}

record_warning() {
  local message="$1"
  warnings+=1
  log_warn "$message"
}

load_env_defaults() {
  local env_file="$1"
  [[ -f "$env_file" ]] || return 0

  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue

    if [[ "$line" == export[[:space:]]* ]]; then
      line="${line#export }"
    fi

    key="${line%%=*}"
    value="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"

    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ "$value" =~ ^\".*\"$ || "$value" =~ ^\'.*\'$ ]]; then
      value="${value:1:${#value}-2}"
    fi

    if [[ -z "${!key+x}" ]]; then
      export "${key}=${value}"
    fi
  done <"$env_file"
}

env_value() {
  local key="$1"
  local default_value="${2:-}"
  if [[ -n "${!key+x}" ]]; then
    printf '%s' "${!key}"
    return 0
  fi

  printf '%s' "$default_value"
}

check_required_tools() {
  local missing=0
  local required_cmd

  for required_cmd in "$curl_cmd" "$python_cmd" "$ss_cmd" "$nc_cmd"; do
    if ! command_exists "$required_cmd"; then
      record_config_failure "Required command not available: ${required_cmd}"
      missing=1
    fi
  done

  return "$missing"
}

sanitize_url() {
  local url="$1"

  if [[ -z "$url" ]]; then
    printf '%s' ""
    return 0
  fi

  "$python_cmd" - "$url" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit

url = sys.argv[1]
try:
    parts = urlsplit(url)
    port = parts.port
    host = parts.hostname or ""
except ValueError:
    print("[unparseable-url]")
    raise SystemExit(0)

if "@" not in parts.netloc:
    print(url)
    raise SystemExit(0)

port_text = f":{port}" if port else ""
print(urlunsplit((parts.scheme, f"[redacted]@{host}{port_text}", parts.path, parts.query, parts.fragment)))
PY
}

url_host() {
  local url="$1"

  "$python_cmd" - "$url" <<'PY'
import sys
from urllib.parse import urlsplit

try:
    parts = urlsplit(sys.argv[1])
    host = parts.hostname or ""
except ValueError:
    print("")
else:
    print(host)
PY
}

url_port() {
  local url="$1"

  "$python_cmd" - "$url" <<'PY'
import sys
from urllib.parse import urlsplit

try:
    parts = urlsplit(sys.argv[1])
    port = parts.port
except ValueError:
    print("")
else:
    if port:
        print(port)
    elif parts.scheme == "https":
        print(443)
    elif parts.scheme == "http":
        print(80)
    else:
        print("")
PY
}

source_host() {
  case "$1" in
    local) env_value "LANGFUSE_LOCAL_HOST" "http://host.docker.internal:3000" ;;
    remote) env_value "LANGFUSE_HOST" "https://cloud.langfuse.com" ;;
  esac
}

source_public_key() {
  case "$1" in
    local) env_value "LANGFUSE_LOCAL_PUBLIC_KEY" "" ;;
    remote) env_value "LANGFUSE_PUBLIC_KEY" "" ;;
  esac
}

source_secret_key() {
  case "$1" in
    local) env_value "LANGFUSE_LOCAL_SECRET_KEY" "" ;;
    remote) env_value "LANGFUSE_SECRET_KEY" "" ;;
  esac
}

source_required_env() {
  case "$1" in
    local) printf '%s\n' "LANGFUSE_LOCAL_HOST LANGFUSE_LOCAL_PUBLIC_KEY LANGFUSE_LOCAL_SECRET_KEY" ;;
    remote) printf '%s\n' "LANGFUSE_HOST LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY" ;;
  esac
}

find_port_owner() {
  local port="$1"
  local owner=""
  local ss_output

  ss_output="$("$ss_cmd" -ltnp "sport = :${port}" 2>/dev/null || true)"
  owner="$("$python_cmd" - "$ss_output" <<'PY'
import re
import sys

for line in sys.argv[1].splitlines():
    if "LISTEN" not in line:
        continue

    match = re.search(r'users:\(\("([^"]+)".*?pid=([0-9]+)', line)
    if match:
        print(f"{match.group(1)}/{match.group(2)}")
    else:
        print("unknown process")
    raise SystemExit(0)

raise SystemExit(0)
PY
)"
  if [[ -n "$owner" ]]; then
    printf '%s\n' "$owner"
    return 0
  fi

  return 1
}

http_get() {
  local url="$1"
  local output_file="$2"

  "$curl_cmd" -fsS --max-time "$timeout_seconds" "$url" >"$output_file" 2>"${output_file}.err"
}

payload_is_trace_review_health() {
  local payload_file="$1"

  "$python_cmd" - "$payload_file" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    raise SystemExit(1)

message = str(payload.get("message", ""))
status = str(payload.get("status", ""))
if status in {"ok", "starting"} and "Trace Review API" in message:
    raise SystemExit(0)
raise SystemExit(1)
PY
}

check_trace_review_backend() {
  local payload_file
  local preflight_payload_file
  local backend_port
  local owner
  payload_file="$(mktemp)"
  preflight_payload_file="$(mktemp)"

  backend_port="$(url_port "$backend_url")"
  if [[ -n "$backend_port" ]]; then
    owner="$(find_port_owner "$backend_port" || true)"
    if [[ -n "$owner" ]]; then
      log_info "Port ${backend_port} listener: ${owner}"
    else
      log_info "Port ${backend_port} listener: none detected before HTTP probe"
    fi
  fi

  if ! http_get "${backend_url%/}/health" "$payload_file"; then
    record_health_failure "TraceReview backend health unreachable at ${backend_url%/}/health. No service restart was attempted."
    if [[ "$backend_port" == "8001" ]]; then
      record_warning "Port 8001 is the default TraceReview backend port and is easy to confuse with a Symphony review proxy; verify the listener or pass --backend-url."
    fi
    rm -f "$payload_file" "${payload_file}.err" "$preflight_payload_file" "${preflight_payload_file}.err"
    return 0
  fi

  if payload_is_trace_review_health "$payload_file"; then
    log_success "TraceReview backend health OK at ${backend_url%/}/health"
  else
    record_health_failure "Backend URL responded but does not look like TraceReview API. This often means port ${backend_port:-unknown} is a Symphony review proxy or another local service."
  fi

  if http_get "${backend_url%/}/health/preflight?source=${selected_source}" "$preflight_payload_file"; then
    log_success "TraceReview backend preflight endpoint responded for source '${selected_source}'."
  else
    record_warning "TraceReview /health/preflight endpoint was not reachable. Rebuild/redeploy TraceReview if this backend predates the diagnostic endpoint."
  fi

  rm -f "$payload_file" "${payload_file}.err" "$preflight_payload_file" "${preflight_payload_file}.err"
}

check_source_selection() {
  case "$selected_source" in
    remote|local)
      log_info "Selected trace source: ${selected_source}"
      ;;
    *)
      record_config_failure "Unsupported trace source '${selected_source}'. Expected 'remote' or 'local'."
      ;;
  esac
}

check_langfuse_source() {
  local source="$1"
  local host public_key secret_key missing_env health_payload_file
  host="$(source_host "$source")"
  public_key="$(source_public_key "$source")"
  secret_key="$(source_secret_key "$source")"
  health_payload_file="$(mktemp)"

  log_info "Langfuse ${source}: host=$(sanitize_url "$host") public_key_present=$([[ -n "$public_key" ]] && printf true || printf false) secret_key_present=$([[ -n "$secret_key" ]] && printf true || printf false)"

  if [[ -z "$host" || -z "$public_key" || -z "$secret_key" ]]; then
    missing_env="$(source_required_env "$source")"
    if [[ "$source" == "$selected_source" ]]; then
      record_config_failure "Selected source '${source}' is missing Langfuse configuration. Required env: ${missing_env}."
    else
      record_warning "Unselected source '${source}' is not fully configured. Required env: ${missing_env}."
    fi
    rm -f "$health_payload_file" "${health_payload_file}.err"
    return 0
  fi

  if http_get "${host%/}/api/public/health" "$health_payload_file"; then
    log_success "Langfuse ${source} health OK at $(sanitize_url "${host%/}/api/public/health")"
  elif [[ "$source" == "$selected_source" ]]; then
    record_health_failure "Langfuse ${source} health failed at $(sanitize_url "${host%/}/api/public/health"). Check source selection, credentials, service health, and VPN/network access."
  else
    record_warning "Unselected Langfuse source '${source}' health failed at $(sanitize_url "${host%/}/api/public/health")."
  fi

  rm -f "$health_payload_file" "${health_payload_file}.err"
}

check_vpn_route_hint() {
  local remote_host_url host resolved_ip route_line
  remote_host_url="$(source_host "remote")"
  host="$(url_host "$remote_host_url")"

  if [[ -z "$host" ]]; then
    record_warning "VPN route hint skipped because LANGFUSE_HOST is not configured."
    return 0
  fi

  if ! command_exists "$getent_cmd"; then
    record_warning "VPN route hint skipped because '${getent_cmd}' is unavailable; remote Langfuse host is ${host}."
    return 0
  fi

  resolved_ip="$("$getent_cmd" hosts "$host" 2>/dev/null | awk '{ print $1; exit }' || true)"
  if [[ -z "$resolved_ip" ]]; then
    record_warning "VPN route hint: unable to resolve remote Langfuse host ${host}. Check VPN/DNS before remote trace review."
    return 0
  fi

  if ! command_exists "$ip_cmd"; then
    record_warning "VPN route hint: ${host} resolves to ${resolved_ip}, but '${ip_cmd}' is unavailable for route inspection."
    return 0
  fi

  route_line="$("$ip_cmd" route get "$resolved_ip" 2>/dev/null | head -n 1 || true)"
  if [[ -n "$route_line" ]]; then
    log_info "VPN route hint for ${host} (${resolved_ip}): ${route_line}"
  else
    record_warning "VPN route hint: no route found for ${host} (${resolved_ip}). Check VPN before remote trace review."
  fi
}

tcp_check() {
  local host="$1"
  local port="$2"

  "$nc_cmd" -z -w "$timeout_seconds" "$host" "$port" >/dev/null 2>&1
}

production_readiness_warning() {
  local message="$1"
  if [[ "$require_production_readiness" == "true" ]]; then
    record_config_failure "$message"
  else
    record_warning "$message"
  fi
}

check_production_readiness() {
  local curation_source ssh_key_file

  log_info "Production-readiness checks are report-only; no SSH session or production command is executed."
  check_vpn_route_hint

  if [[ -z "$ssh_host" ]]; then
    production_readiness_warning "Production SSH host not configured; set TRACE_REVIEW_PRODUCTION_SSH_HOST to enable TCP reachability checks."
  else
    if tcp_check "$ssh_host" "$ssh_port"; then
      log_success "Production SSH TCP reachable at ${ssh_host}:${ssh_port}."
    else
      production_readiness_warning "Production SSH TCP check failed for ${ssh_host}:${ssh_port}. Check VPN, security group, host, and port."
    fi
  fi

  ssh_key_file="${TRACE_REVIEW_PRODUCTION_SSH_KEY_FILE:-}"
  if [[ -z "$ssh_key_file" ]]; then
    record_warning "TRACE_REVIEW_PRODUCTION_SSH_KEY_FILE is not set; PDF/log retrieval over SSH may need an explicit key."
  elif [[ -r "$ssh_key_file" ]]; then
    log_success "Production SSH key file is configured and readable."
  else
    production_readiness_warning "TRACE_REVIEW_PRODUCTION_SSH_KEY_FILE is set but not readable."
  fi

  curation_source="${CURATION_DB_CREDENTIALS_SOURCE:-}"
  log_info "Production env presence: CURATION_DB_URL_present=$([[ -n "${CURATION_DB_URL:-}" ]] && printf true || printf false) CURATION_DB_CREDENTIALS_SOURCE=${curation_source:-unset} CURATION_DB_AWS_SECRET_ID_present=$([[ -n "${CURATION_DB_AWS_SECRET_ID:-}" ]] && printf true || printf false) AWS_REGION_present=$([[ -n "${AWS_REGION:-${AWS_DEFAULT_REGION:-}}" ]] && printf true || printf false)"
}

emit_summary_and_exit() {
  local exit_code="$EXIT_OK"
  local hard_failures=$((health_failures + config_failures))

  if (( health_failures > 0 && config_failures > 0 )); then
    exit_code="$EXIT_MULTIPLE_FAILURES"
  elif (( health_failures > 0 )); then
    exit_code="$EXIT_HEALTH_FAILURE"
  elif (( config_failures > 0 )); then
    exit_code="$EXIT_CONFIG_FAILURE"
  fi

  if (( hard_failures > 0 )); then
    log_error "TraceReview preflight found ${hard_failures} hard failure(s) and ${warnings} warning(s)."
  else
    log_success "TraceReview preflight passed with ${warnings} warning(s)."
  fi

  echo "No production changes were attempted."
  printf 'TRACE_REVIEW_PREFLIGHT_RESULT exit_code=%d hard_failures=%d health_failures=%d config_failures=%d warnings=%d source=%s backend_url=%s\n' \
    "$exit_code" "$hard_failures" "$health_failures" "$config_failures" "$warnings" "$selected_source" "$backend_url"
  exit "$exit_code"
}

main() {
  local backend_port

  load_env_defaults "${HOME}/.agr_ai_curation/.env"
  load_env_defaults "${HOME}/.agr_ai_curation/trace_review/.env"
  load_env_defaults "${repo_root}/trace_review/backend/.env"

  if [[ -z "$backend_url" ]]; then
    backend_port="$(env_value "TRACE_REVIEW_BACKEND_HOST_PORT" "8001")"
    backend_url="http://127.0.0.1:${backend_port}"
  fi

  echo
  log_info "=== TraceReview Preflight Diagnostics ==="
  echo
  echo "  Report-only checks for TraceReview backend, Langfuse source health,"
  echo "  port/proxy confusion, and production-readiness hints."
  echo "  This command does not start, stop, restart, or mutate services."
  echo

  if ! check_required_tools; then
    emit_summary_and_exit
  fi
  check_source_selection
  check_trace_review_backend
  check_langfuse_source "remote"
  check_langfuse_source "local"
  check_production_readiness
  emit_summary_and_exit
}

main "$@"
