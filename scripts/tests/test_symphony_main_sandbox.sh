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
  assert_output_contains "trace_review_frontend_port=3901" "${output}"
  assert_output_contains "trace_review_backend_port=8901" "${output}"
  assert_output_contains "sandbox_db_tunnel_local_port=6330" "${output}"
  assert_output_contains "sandbox_db_tunnel_docker_port=6331" "${output}"
  assert_output_contains "sandbox_status=dry_run" "${output}"
}

test_repair_dry_run_reuses_persisted_ports() {
  local temp_dir output
  temp_dir="$(mktemp -d)"
  mkdir -p "${temp_dir}"
  cat > "${temp_dir}/.symphony-main-sandbox-state.json" <<'EOF'
{"sandbox_frontend_port":"3917","sandbox_backend_port":"8917","sandbox_db_tunnel_local_port":"7440","sandbox_db_tunnel_docker_port":"7441","trace_review_frontend_port":"3918","trace_review_backend_port":"8918"}
EOF

  output="$(run_sandbox_helper "${temp_dir}" repair --dry-run)"

  assert_output_contains "sandbox_action=repair" "${output}"
  assert_output_contains "sandbox_frontend_port=3917" "${output}"
  assert_output_contains "sandbox_backend_port=8917" "${output}"
  assert_output_contains "trace_review_frontend_port=3918" "${output}"
  assert_output_contains "trace_review_backend_port=8918" "${output}"
  assert_output_contains "sandbox_db_tunnel_local_port=7440" "${output}"
  assert_output_contains "sandbox_db_tunnel_docker_port=7441" "${output}"
  assert_output_contains "sandbox_status=dry_run" "${output}"
}

test_prepare_dry_run_reuses_persisted_ports() {
  local temp_dir output
  temp_dir="$(mktemp -d)"
  mkdir -p "${temp_dir}"
  cat > "${temp_dir}/.symphony-main-sandbox-state.json" <<'EOF'
{"sandbox_frontend_port":"3900","sandbox_backend_port":"8900","sandbox_db_tunnel_local_port":"6330","sandbox_db_tunnel_docker_port":"6331","trace_review_frontend_port":"3901","trace_review_backend_port":"8901"}
EOF

  output="$(run_sandbox_helper "${temp_dir}" prepare --dry-run)"

  assert_output_contains "sandbox_action=prepare" "${output}"
  assert_output_contains "sandbox_frontend_port=3900" "${output}"
  assert_output_contains "sandbox_backend_port=8900" "${output}"
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

test_prepare_dry_run_rejects_overlapping_trace_review_ports() {
  local temp_dir output
  temp_dir="$(mktemp -d)"

  if output="$(
    cd "${REPO_ROOT}" && \
      SYMPHONY_MAIN_SANDBOX_ROOT="${temp_dir}" \
      SYMPHONY_MAIN_SANDBOX_FRONTEND_PORT=3900 \
      SYMPHONY_MAIN_SANDBOX_BACKEND_PORT=8900 \
      SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_FRONTEND_PORT=3900 \
      SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_BACKEND_PORT=8901 \
      bash "${SCRIPT_PATH}" prepare --dry-run 2>&1
  )"; then
    echo "Expected overlapping trace review ports to fail" >&2
    exit 1
  fi

  assert_output_contains "Main sandbox requested overlapping host ports" "${output}"
}

test_prepare_dry_run_auto_shifts_trace_review_ports_after_main_override() {
  local temp_dir output
  temp_dir="$(mktemp -d)"

  output="$(
    cd "${REPO_ROOT}" && \
      SYMPHONY_MAIN_SANDBOX_ROOT="${temp_dir}" \
      SYMPHONY_MAIN_SANDBOX_FRONTEND_PORT=3901 \
      SYMPHONY_MAIN_SANDBOX_BACKEND_PORT=8901 \
      bash "${SCRIPT_PATH}" prepare --dry-run
  )"

  assert_output_contains "sandbox_frontend_port=3901" "${output}"
  assert_output_contains "sandbox_backend_port=8901" "${output}"
  assert_output_contains "trace_review_frontend_port=3902" "${output}"
  assert_output_contains "trace_review_backend_port=8902" "${output}"
}

test_repair_fails_when_git_safety_tools_are_unavailable() {
  local temp_dir output
  temp_dir="$(mktemp -d)"

  if output="$(
    cd "${REPO_ROOT}" && \
      SYMPHONY_MAIN_SANDBOX_ROOT="${temp_dir}" \
      SYMPHONY_MAIN_SANDBOX_FRONTEND_PORT=53900 \
      SYMPHONY_MAIN_SANDBOX_BACKEND_PORT=58900 \
      SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_FRONTEND_PORT=53901 \
      SYMPHONY_MAIN_SANDBOX_TRACE_REVIEW_BACKEND_PORT=58901 \
      SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_LOCAL_PORT=56330 \
      SYMPHONY_MAIN_SANDBOX_DB_TUNNEL_DOCKER_PORT=56331 \
      SYMPHONY_GIT_SAFETY_TOOLS_INSTALLER="${temp_dir}/missing-installer.sh" \
      bash "${SCRIPT_PATH}" repair 2>&1
  )"; then
    echo "Expected repair to fail when git safety tools installer is unavailable" >&2
    exit 1
  fi

  assert_output_contains "sandbox_status=error" "${output}"
  assert_output_contains "sandbox_error=Git safety tools are unavailable. Run scripts/utilities/symphony_ensure_git_safety_tools.sh and retry." "${output}"
}

test_trace_review_compose_accepts_dev_and_langfuse_env() {
  local output

  output="$(
    cd "${REPO_ROOT}/trace_review" && \
      DEV_MODE=true \
      LANGFUSE_HOST=http://remote.example \
      LANGFUSE_PUBLIC_KEY=pk \
      LANGFUSE_SECRET_KEY=sk \
      LANGFUSE_LOCAL_HOST=http://host.docker.internal:33330 \
      LANGFUSE_LOCAL_PUBLIC_KEY=pk-local \
      LANGFUSE_LOCAL_SECRET_KEY=sk-local \
      TRACE_REVIEW_FRONTEND_HOST_PORT=3901 \
      TRACE_REVIEW_BACKEND_HOST_PORT=8901 \
      TRACE_REVIEW_FRONTEND_URL=http://10.0.0.2:3901 \
      TRACE_REVIEW_PUBLIC_API_URL=http://host.docker.internal:8901 \
      docker compose -f docker-compose.yml config
  )"

  assert_output_contains 'DEV_MODE: "true"' "${output}"
  assert_output_contains 'LANGFUSE_HOST: http://remote.example' "${output}"
  assert_output_contains 'LANGFUSE_LOCAL_HOST: http://host.docker.internal:33330' "${output}"
  assert_output_contains 'LANGFUSE_LOCAL_PUBLIC_KEY: pk-local' "${output}"
  assert_output_contains 'LANGFUSE_LOCAL_SECRET_KEY: sk-local' "${output}"
}

test_prepare_dry_run_uses_stable_ports
test_repair_dry_run_reuses_persisted_ports
test_prepare_dry_run_reuses_persisted_ports
test_env_overrides_replace_default_tunnel_ports
test_prepare_dry_run_rejects_overlapping_trace_review_ports
test_prepare_dry_run_auto_shifts_trace_review_ports_after_main_override
test_repair_fails_when_git_safety_tools_are_unavailable
test_trace_review_compose_accepts_dev_and_langfuse_env

echo "symphony_main_sandbox tests passed"
