#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_main_sandbox.sh"

assert_output_contains() {
  local pattern="$1"
  local output="$2"
  if ! printf '%s\n' "${output}" | rg -n --fixed-strings "${pattern}" >/dev/null 2>&1; then
    echo "Expected to find '${pattern}' in output:" >&2
    printf '%s\n' "${output}" >&2
    exit 1
  fi
}

run_sandbox_helper() {
  local sandbox_root="$1"
  shift

  (
    cd "${REPO_ROOT}"
    SYMPHONY_MAIN_SANDBOX_ROOT="${sandbox_root}" bash "${SCRIPT_PATH}" "$@"
  )
}

test_prepare_dry_run_uses_stable_ports() {
  local temp_dir output
  temp_dir="$(mktemp -d)"

  output="$(run_sandbox_helper "${temp_dir}" prepare --dry-run)"

  assert_output_contains "sandbox_action=prepare" "${output}"
  assert_output_contains "sandbox_frontend_port=3900" "${output}"
  assert_output_contains "sandbox_backend_port=8900" "${output}"
  assert_output_contains "sandbox_db_tunnel_local_port=6330" "${output}"
  assert_output_contains "sandbox_db_tunnel_docker_port=6331" "${output}"
  assert_output_contains "sandbox_status=dry_run" "${output}"
}

test_repair_dry_run_reuses_persisted_ports() {
  local temp_dir output
  temp_dir="$(mktemp -d)"
  mkdir -p "${temp_dir}"
  cat > "${temp_dir}/.symphony-main-sandbox-state.json" <<'EOF'
{"sandbox_frontend_port":"3917","sandbox_backend_port":"8917","sandbox_db_tunnel_local_port":"7440","sandbox_db_tunnel_docker_port":"7441"}
EOF

  output="$(run_sandbox_helper "${temp_dir}" repair --dry-run)"

  assert_output_contains "sandbox_action=repair" "${output}"
  assert_output_contains "sandbox_frontend_port=3917" "${output}"
  assert_output_contains "sandbox_backend_port=8917" "${output}"
  assert_output_contains "sandbox_db_tunnel_local_port=7440" "${output}"
  assert_output_contains "sandbox_db_tunnel_docker_port=7441" "${output}"
  assert_output_contains "sandbox_status=dry_run" "${output}"
}

test_env_overrides_replace_default_tunnel_ports() {
  local temp_dir output
  temp_dir="$(mktemp -d)"

  output="$(
    cd "${REPO_ROOT}" && \
      SYMPHONY_MAIN_SANDBOX_ROOT="${temp_dir}" \
      SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_LOCAL_PORT=7440 \
      SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_DOCKER_PORT=7441 \
      bash "${SCRIPT_PATH}" prepare --dry-run
  )"

  assert_output_contains "sandbox_db_tunnel_local_port=7440" "${output}"
  assert_output_contains "sandbox_db_tunnel_docker_port=7441" "${output}"
}

test_prepare_dry_run_uses_stable_ports
test_repair_dry_run_reuses_persisted_ports
test_env_overrides_replace_default_tunnel_ports

echo "symphony_main_sandbox tests passed"
