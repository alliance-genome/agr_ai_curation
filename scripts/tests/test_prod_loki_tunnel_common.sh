#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=scripts/lib/prod_loki_tunnel_common.sh
source "${REPO_ROOT}/scripts/lib/prod_loki_tunnel_common.sh"

assert_equals() {
  local expected="$1"
  local actual="$2"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "Expected '${expected}', got '${actual}'" >&2
    exit 1
  fi
}

assert_fails() {
  if "$@" >/dev/null 2>&1; then
    echo "Expected command to fail: $*" >&2
    exit 1
  fi
}

assert_succeeds() {
  if ! "$@" >/dev/null 2>&1; then
    echo "Expected command to succeed: $*" >&2
    exit 1
  fi
}

test_bind_validation_rejects_public_and_lan_defaults() {
  assert_fails prod_loki_validate_bind_ip "0.0.0.0"
  assert_fails prod_loki_validate_bind_ip "127.0.0.1"
  assert_fails prod_loki_validate_bind_ip "192.168.86.44"
  assert_succeeds prod_loki_validate_bind_ip "10.222.162.1"
  assert_succeeds prod_loki_validate_bind_ip "172.31.0.1"
}

test_bind_validation_allows_explicit_test_overrides() {
  SYMPHONY_PROD_LOKI_ALLOW_LOCALHOST_BIND=1 assert_succeeds prod_loki_validate_bind_ip "127.0.0.1"
  SYMPHONY_PROD_LOKI_ALLOW_LAN_BIND=1 assert_succeeds prod_loki_validate_bind_ip "192.168.86.44"
}

test_port_validation() {
  assert_succeeds prod_loki_validate_port "43100"
  assert_fails prod_loki_validate_port "0"
  assert_fails prod_loki_validate_port "65536"
  assert_fails prod_loki_validate_port "abc"
}

test_endpoint_file_contains_only_non_secret_values() {
  local temp_dir endpoint_file
  temp_dir="$(mktemp -d)"
  endpoint_file="${temp_dir}/prod_loki_endpoint.env"

  prod_loki_write_endpoint_file "${endpoint_file}" "http://10.222.162.1:43100"

  # shellcheck disable=SC1090
  source "${endpoint_file}"
  assert_equals "http://10.222.162.1:43100" "${LOKI_URL}"
  assert_equals "host" "${LOKI_TUNNEL_OWNER}"
  assert_equals "readonly-proxy" "${LOKI_TUNNEL_MODE}"

  if rg -n "PID|ssh|pem|AWS|PROFILE|172\\.31\\.29\\.141" "${endpoint_file}" >/dev/null 2>&1; then
    echo "Endpoint file should not contain process, credential, or production config details" >&2
    cat "${endpoint_file}" >&2
    exit 1
  fi
}

test_incus_bind_validation_rejects_other_private_addresses() {
  local temp_dir stub_dir
  temp_dir="$(mktemp -d)"
  stub_dir="${temp_dir}/bin"
  mkdir -p "${stub_dir}"
  cat > "${stub_dir}/incus" <<'EOF'
#!/usr/bin/env bash
echo "10.222.162.1"
EOF
  chmod +x "${stub_dir}/incus"

  PATH="${stub_dir}:${PATH}" assert_succeeds prod_loki_validate_incus_bind_ip "10.222.162.1"
  PATH="${stub_dir}:${PATH}" assert_fails prod_loki_validate_incus_bind_ip "10.0.0.5"
  SYMPHONY_PROD_LOKI_BIND_IP="10.0.0.5" PATH="${stub_dir}:${PATH}" assert_fails prod_loki_validate_incus_bind_ip "10.0.0.5"
}

test_incus_bind_validation_fails_closed_without_discovery() {
  local temp_dir stub_dir
  temp_dir="$(mktemp -d)"
  stub_dir="${temp_dir}/bin"
  mkdir -p "${stub_dir}"
  cat > "${stub_dir}/incus" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
  chmod +x "${stub_dir}/incus"

  PATH="${stub_dir}:${PATH}" assert_fails prod_loki_validate_incus_bind_ip "10.222.162.1"
  SYMPHONY_PROD_LOKI_ALLOW_NON_INCUS_BIND=1 PATH="${stub_dir}:${PATH}" assert_succeeds prod_loki_validate_incus_bind_ip "10.222.162.1"
}

test_bind_validation_rejects_public_and_lan_defaults
test_bind_validation_allows_explicit_test_overrides
test_port_validation
test_endpoint_file_contains_only_non_secret_values
test_incus_bind_validation_rejects_other_private_addresses
test_incus_bind_validation_fails_closed_without_discovery

echo "prod_loki_tunnel_common tests passed"
