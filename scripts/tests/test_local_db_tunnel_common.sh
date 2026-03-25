#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=scripts/lib/local_db_tunnel_common.sh
source "${REPO_ROOT}/scripts/lib/local_db_tunnel_common.sh"

assert_contains() {
  local pattern="$1"
  local file="$2"
  if ! rg -n --fixed-strings "$pattern" "$file" >/dev/null 2>&1; then
    echo "Expected to find '$pattern' in $file" >&2
    exit 1
  fi
}

assert_equals() {
  local expected="$1"
  local actual="$2"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "Expected '${expected}', got '${actual}'" >&2
    exit 1
  fi
}

assert_sourced_env_value() {
  local env_file="$1"
  local key="$2"
  local expected="$3"
  local actual
  actual="$(bash -c "set -euo pipefail; source \"$env_file\"; printf '%s' \"\${$key}\"")"
  assert_equals "${expected}" "${actual}"
}

assert_sourced_env_url_components() {
  local env_file="$1"
  bash -c "set -euo pipefail; source \"$env_file\"; python3 - <<'PY'
import os
from urllib.parse import urlparse, unquote

url = os.environ['CURATION_DB_URL']
parsed = urlparse(url)

assert parsed.scheme == 'postgresql', parsed
assert parsed.username == 'readonly_user', parsed
assert unquote(parsed.password) == 'fixture pass!', parsed
assert parsed.hostname == 'host.docker.internal', parsed
assert parsed.path == '/curation', parsed
assert parsed.port == 22813, parsed
PY"
}

assert_file_exists() {
  local path="$1"
  [[ -f "${path}" ]] || {
    echo "Expected file to exist: ${path}" >&2
    exit 1
  }
}

assert_output_contains() {
  local pattern="$1"
  local output="$2"
  if ! printf '%s\n' "${output}" | rg -n --fixed-strings "${pattern}" >/dev/null 2>&1; then
    echo "Expected to find '${pattern}' in output:" >&2
    printf '%s\n' "${output}" >&2
    exit 1
  fi
}

test_forward_host_defaults_to_host_docker_internal() {
  unset CURATION_DB_TUNNEL_FORWARD_HOST || true
  assert_equals "host.docker.internal" "$(local_db_tunnel_forward_host)"
}

test_bind_ip_override_wins() {
  CURATION_DB_TUNNEL_BIND_IP="10.20.30.40"
  assert_equals "10.20.30.40" "$(local_db_tunnel_resolve_docker_gateway_ip)"
  unset CURATION_DB_TUNNEL_BIND_IP
}

test_docker_gateway_ip_uses_docker_bridge_when_available() {
  local stub_dir
  stub_dir="$(mktemp -d)"
  cat > "${stub_dir}/docker" <<'EOF'
#!/usr/bin/env bash
echo "172.31.0.1"
EOF
  chmod +x "${stub_dir}/docker"

  assert_equals "172.31.0.1" "$(PATH="${stub_dir}:${PATH}" local_db_tunnel_resolve_docker_gateway_ip)"
}

test_write_env_file_uses_container_forward_host() {
  local temp_dir env_file
  temp_dir="$(mktemp -d)"
  env_file="${temp_dir}/local_db_tunnel_env.sh"

  local_db_tunnel_write_env_file \
    "${env_file}" \
    "curation" \
    "readonly_user" \
    "fixture pass!" \
    "22812" \
    "22813" \
    "172.17.0.1" \
    "host.docker.internal"

  assert_contains "export CURATION_DB_TUNNEL_FORWARD_HOST=host.docker.internal" "${env_file}"
  assert_contains "export PERSISTENT_STORE_DB_PASSWORD=fixture\\ pass\\!" "${env_file}"
  assert_contains "Host-shell tools should continue using localhost:22812 directly." "${env_file}"
  assert_sourced_env_value "${env_file}" "PERSISTENT_STORE_DB_PASSWORD" "fixture pass!"
  assert_sourced_env_value "${env_file}" "CURATION_DB_TUNNEL_FORWARD_HOST" "host.docker.internal"
  assert_sourced_env_url_components "${env_file}"
}

test_state_dir_is_workspace_specific() {
  local root one two
  root="$(mktemp -d)"
  SYMPHONY_LOCAL_DB_TUNNEL_STATE_ROOT="${root}"
  one="$(local_db_tunnel_state_dir "/tmp/workspaces/ALL-28")"
  two="$(local_db_tunnel_state_dir "/tmp/workspaces/ALL-29")"

  [[ "${one}" == "${root}"/* ]] || {
    echo "Expected state dir to be rooted under ${root}" >&2
    exit 1
  }
  [[ "${one}" != "${two}" ]] || {
    echo "Expected different workspaces to get different state dirs" >&2
    exit 1
  }
  unset SYMPHONY_LOCAL_DB_TUNNEL_STATE_ROOT
}

test_state_root_falls_back_when_xdg_runtime_dir_is_unusable() {
  local temp_root blocked home_dir state_dir
  temp_root="$(mktemp -d)"
  blocked="${temp_root}/xdg-runtime-file"
  home_dir="${temp_root}/home"
  mkdir -p "${home_dir}"
  : > "${blocked}"

  XDG_RUNTIME_DIR="${blocked}"
  HOME="${home_dir}"
  state_dir="$(local_db_tunnel_state_dir "/tmp/workspaces/ALL-30")"

  [[ "${state_dir}" == "${home_dir}/.local/state/agr_ai_curation_symphony_db_tunnels/"* ]] || {
    echo "Expected fallback under ${home_dir}/.local/state, got ${state_dir}" >&2
    exit 1
  }

  unset XDG_RUNTIME_DIR
}

test_spawn_detached_creates_missing_parent_dirs() {
  local temp_root pid_file log_file worker_pid
  temp_root="$(mktemp -d)"
  pid_file="${temp_root}/missing/state/worker.pid"
  log_file="${temp_root}/missing/state/worker.log"

  worker_pid="$(
    local_db_tunnel_spawn_detached \
      "${pid_file}" \
      "${log_file}" \
      bash -lc 'echo worker-started; sleep 5'
  )"

  sleep 0.2
  assert_file_exists "${pid_file}"
  assert_file_exists "${log_file}"
  assert_contains "worker-started" "${log_file}"

  kill "${worker_pid}" 2>/dev/null || true
}

test_load_private_env_tolerates_forward_references_under_nounset() {
  local temp_home env_file output
  temp_home="$(mktemp -d)"
  mkdir -p "${temp_home}/.agr_ai_curation"
  env_file="${temp_home}/.agr_ai_curation/.env"

  cat > "${env_file}" <<'EOF'
LANGFUSE_DATABASE_URL=postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/postgres
POSTGRES_PASSWORD=from-later-line
AWS_PROFILE=test-profile
EOF

  output="$(
    HOME="${temp_home}" bash -c "
      set -euo pipefail
      source '${REPO_ROOT}/scripts/lib/local_db_tunnel_common.sh'
      local_db_tunnel_load_private_env >/dev/null
      printf 'POSTGRES_PASSWORD=%s\n' \"\${POSTGRES_PASSWORD}\"
      printf 'AWS_PROFILE=%s\n' \"\${AWS_PROFILE}\"
      printf 'LANGFUSE_DATABASE_URL=%s\n' \"\${LANGFUSE_DATABASE_URL}\"
    "
  )"

  assert_output_contains "POSTGRES_PASSWORD=from-later-line" "${output}"
  assert_output_contains "AWS_PROFILE=test-profile" "${output}"
  assert_output_contains 'LANGFUSE_DATABASE_URL=postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/postgres' "${output}"
}

test_load_private_env_expands_known_earlier_variables() {
  local temp_home env_file output
  temp_home="$(mktemp -d)"
  mkdir -p "${temp_home}/.agr_ai_curation"
  env_file="${temp_home}/.agr_ai_curation/.env"

  cat > "${env_file}" <<'EOF'
BASE_PROFILE=team-dev
AWS_PROFILE=${BASE_PROFILE}
DOUBLE_QUOTED="prefix-${BASE_PROFILE}"
SINGLE_QUOTED='literal-${BASE_PROFILE}'
EOF

  output="$(
    HOME="${temp_home}" bash -c "
      set -euo pipefail
      source '${REPO_ROOT}/scripts/lib/local_db_tunnel_common.sh'
      local_db_tunnel_load_private_env >/dev/null
      printf 'AWS_PROFILE=%s\n' \"\${AWS_PROFILE}\"
      printf 'DOUBLE_QUOTED=%s\n' \"\${DOUBLE_QUOTED}\"
      printf 'SINGLE_QUOTED=%s\n' \"\${SINGLE_QUOTED}\"
    "
  )"

  assert_output_contains "AWS_PROFILE=team-dev" "${output}"
  assert_output_contains "DOUBLE_QUOTED=prefix-team-dev" "${output}"
  assert_output_contains 'SINGLE_QUOTED=literal-${BASE_PROFILE}' "${output}"
}

test_forward_host_defaults_to_host_docker_internal
test_bind_ip_override_wins
test_docker_gateway_ip_uses_docker_bridge_when_available
test_write_env_file_uses_container_forward_host
test_state_dir_is_workspace_specific
test_state_root_falls_back_when_xdg_runtime_dir_is_unusable
test_spawn_detached_creates_missing_parent_dirs
test_load_private_env_tolerates_forward_references_under_nounset
test_load_private_env_expands_known_earlier_variables

echo "local_db_tunnel_common tests passed"
