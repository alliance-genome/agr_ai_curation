#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
QUERY_HELPER="${REPO_ROOT}/scripts/utilities/symphony_prod_loki_query.sh"
STATUS_HELPER="${REPO_ROOT}/scripts/utilities/symphony_prod_loki_status.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

assert_not_contains() {
  local unexpected="$1"
  local actual="$2"
  if [[ "${actual}" == *"${unexpected}"* ]]; then
    echo "Expected output not to contain '${unexpected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

write_endpoint_file() {
  local file="$1"
  mkdir -p "$(dirname "${file}")"
  cat > "${file}" <<'EOF'
export LOKI_URL=http://10.222.162.1:43100
export LOKI_TUNNEL_OWNER=host
export LOKI_TUNNEL_MODE=readonly-proxy
EOF
}

write_stub_curl() {
  local stub_dir="$1"
  local capture="$2"
  mkdir -p "${stub_dir}" "${capture}"
  cat > "${stub_dir}/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
{
  printf 'args='
  printf '[%s]' "$@"
  printf '\n'
} >> "${CAPTURE_DIR}/curl-calls.txt"

for arg in "$@"; do
  case "${arg}" in
    */ready)
      echo "ready"
      exit 0
      ;;
    */loki/api/v1/labels)
      echo '{"status":"success","data":["service"]}'
      exit 0
      ;;
    */loki/api/v1/label/service/values)
      echo '{"status":"success","data":["backend","trace_review_backend"]}'
      exit 0
      ;;
  esac
done

echo '{"status":"success","data":{"result":[]}}'
EOF
  chmod +x "${stub_dir}/curl"
}

test_builds_expected_logql() {
  local temp_dir endpoint output
  temp_dir="$(mktemp -d)"
  endpoint="${temp_dir}/prod_loki_endpoint.env"
  write_endpoint_file "${endpoint}"

  output="$(bash "${QUERY_HELPER}" --endpoint-file "${endpoint}" --service backend --contains TraceContextError --trace-id abc123 --level ERROR --print-logql)"

  assert_contains '{service="backend"}' "${output}"
  assert_contains '|= "ERROR"' "${output}"
  assert_contains '|= "TraceContextError"' "${output}"
  assert_contains '|= "abc123"' "${output}"
}

test_raw_logql_wins() {
  local temp_dir endpoint output
  temp_dir="$(mktemp -d)"
  endpoint="${temp_dir}/prod_loki_endpoint.env"
  write_endpoint_file "${endpoint}"

  output="$(bash "${QUERY_HELPER}" --endpoint-file "${endpoint}" --raw-logql '{service="promtail"} |= "error"' --print-logql)"

  assert_contains '{service="promtail"} |= "error"' "${output}"
}

test_limit_cap_fails_before_curl() {
  local temp_dir endpoint output status
  temp_dir="$(mktemp -d)"
  endpoint="${temp_dir}/prod_loki_endpoint.env"
  write_endpoint_file "${endpoint}"

  set +e
  output="$(bash "${QUERY_HELPER}" --endpoint-file "${endpoint}" --service backend --limit 5001 --json 2>&1)"
  status=$?
  set -e

  [[ "${status}" -eq 2 ]] || {
    echo "Expected status 2, got ${status}" >&2
    exit 1
  }
  assert_contains "exceeds helper maximum" "${output}"
}

test_services_uses_endpoint_url() {
  local temp_dir endpoint stub_dir capture output old_path
  temp_dir="$(mktemp -d)"
  endpoint="${temp_dir}/prod_loki_endpoint.env"
  stub_dir="${temp_dir}/bin"
  capture="${temp_dir}/capture"
  write_endpoint_file "${endpoint}"
  write_stub_curl "${stub_dir}" "${capture}"
  old_path="${PATH}"

  output="$(CAPTURE_DIR="${capture}" PATH="${stub_dir}:${PATH}" bash "${QUERY_HELPER}" --endpoint-file "${endpoint}" --services)"
  PATH="${old_path}"

  assert_contains "backend" "${output}"
  assert_contains "http://10.222.162.1:43100/loki/api/v1/label/service/values" "$(cat "${capture}/curl-calls.txt")"
}

test_vm_status_uses_only_endpoint_env_and_http() {
  local temp_dir endpoint stub_dir capture output old_path
  temp_dir="$(mktemp -d)"
  endpoint="${temp_dir}/prod_loki_endpoint.env"
  stub_dir="${temp_dir}/bin"
  capture="${temp_dir}/capture"
  write_endpoint_file "${endpoint}"
  write_stub_curl "${stub_dir}" "${capture}"
  old_path="${PATH}"

  output="$(CAPTURE_DIR="${capture}" PATH="${stub_dir}:${PATH}" bash "${STATUS_HELPER}" --endpoint-file "${endpoint}")"
  PATH="${old_path}"

  assert_contains "status=online" "${output}"
  assert_contains "ready_status=ready" "${output}"
  assert_contains "labels_status=ready" "${output}"
  assert_not_contains "PID" "${output}"
  assert_not_contains "systemd" "${output}"
}

test_builds_expected_logql
test_raw_logql_wins
test_limit_cap_fails_before_curl
test_services_uses_endpoint_url
test_vm_status_uses_only_endpoint_env_and_http

echo "symphony_prod_loki_query tests passed"
