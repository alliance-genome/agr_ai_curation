#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
HELPER="${REPO_ROOT}/scripts/utilities/symphony_curation_db_psql.sh"

assert_file_exists() {
  local path="$1"
  [[ -f "${path}" ]] || {
    echo "Expected file to exist: ${path}" >&2
    exit 1
  }
}

assert_file_missing() {
  local path="$1"
  [[ ! -e "${path}" ]] || {
    echo "Expected path to be missing: ${path}" >&2
    exit 1
  }
}

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

assert_equals() {
  local expected="$1"
  local actual="$2"
  if [[ "${expected}" != "${actual}" ]]; then
    echo "Expected '${expected}', got '${actual}'" >&2
    exit 1
  fi
}

write_fixture_workspace() {
  local workspace="$1"

  mkdir -p "${workspace}/scripts/utilities" "${workspace}/scripts"

  cat > "${workspace}/scripts/utilities/symphony_local_db_tunnel_start.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
workspace_dir=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      workspace_dir="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
: > "${workspace_dir}/start-called"
cat > "${workspace_dir}/scripts/local_db_tunnel_env.sh" <<'ENV'
export PERSISTENT_STORE_DB_HOST=localhost
export PERSISTENT_STORE_DB_PORT=6543
export PERSISTENT_STORE_DB_NAME=curation
export PERSISTENT_STORE_DB_USERNAME=readonly_user
export PERSISTENT_STORE_DB_PASSWORD='fixture pass!'
ENV
EOF
  chmod +x "${workspace}/scripts/utilities/symphony_local_db_tunnel_start.sh"

  cat > "${workspace}/scripts/utilities/symphony_local_db_tunnel_status.sh" <<'EOF'
#!/usr/bin/env bash
echo "local_listener=ready"
echo "docker_listener=ready"
EOF
  chmod +x "${workspace}/scripts/utilities/symphony_local_db_tunnel_status.sh"
}

write_stub_psql() {
  local stub_dir="$1"
  local capture_dir="$2"

  mkdir -p "${stub_dir}" "${capture_dir}"
  cat > "${stub_dir}/psql" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
{
  printf 'args='
  printf '[%s]' "$@"
  printf '\n'
  printf 'PGPASSWORD=%s\n' "${PGPASSWORD:-}"
  printf 'PGOPTIONS=%s\n' "${PGOPTIONS:-}"
  printf 'PGAPPNAME=%s\n' "${PGAPPNAME:-}"
} > "${CAPTURE_DIR}/psql-call.txt"
echo "psql-stub-ran"
EOF
  chmod +x "${stub_dir}/psql"
}

test_runs_real_psql_with_readonly_tunnel_env() {
  local temp_dir workspace stub_dir output capture
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  stub_dir="${temp_dir}/bin"
  capture="${temp_dir}/capture"
  write_fixture_workspace "${workspace}"
  write_stub_psql "${stub_dir}" "${capture}"

  output="$(
    CAPTURE_DIR="${capture}" PATH="${stub_dir}:${PATH}" bash "${HELPER}" \
      --workspace-dir "${workspace}" \
      --print-connection \
      -- \
      -c "select 1;" 2>&1
  )"

  assert_file_exists "${workspace}/start-called"
  assert_contains "curation_db_readonly=true" "${output}"
  assert_not_contains "fixture pass!" "${output}"
  assert_contains "psql-stub-ran" "${output}"

  local call
  call="$(cat "${capture}/psql-call.txt")"
  assert_contains "[-h][localhost][-p][6543][-U][readonly_user][-d][curation]" "${call}"
  assert_contains "[-v][ON_ERROR_STOP=1][-P][pager=off][-c][select 1;]" "${call}"
  assert_contains "PGPASSWORD=fixture pass!" "${call}"
  assert_contains "default_transaction_read_only=on" "${call}"
  assert_contains "statement_timeout=30000" "${call}"
  assert_contains "PGAPPNAME=symphony-codex-readonly" "${call}"
}

test_no_start_tunnel_uses_existing_env_file() {
  local temp_dir workspace stub_dir capture
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  stub_dir="${temp_dir}/bin"
  capture="${temp_dir}/capture"
  write_fixture_workspace "${workspace}"
  bash "${workspace}/scripts/utilities/symphony_local_db_tunnel_start.sh" --workspace-dir "${workspace}"
  rm -f "${workspace}/start-called"
  write_stub_psql "${stub_dir}" "${capture}"

  CAPTURE_DIR="${capture}" PATH="${stub_dir}:${PATH}" bash "${HELPER}" \
    --workspace-dir "${workspace}" \
    --no-start-tunnel \
    -- \
    -c "select 2;" >/dev/null

  assert_file_missing "${workspace}/start-called"
  assert_contains "select 2;" "$(cat "${capture}/psql-call.txt")"
}

test_status_delegates_to_tunnel_status_helper() {
  local temp_dir workspace output
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  write_fixture_workspace "${workspace}"

  output="$(bash "${HELPER}" --workspace-dir "${workspace}" --status)"

  assert_contains "local_listener=ready" "${output}"
  assert_contains "docker_listener=ready" "${output}"
}

test_missing_env_file_fails_without_starting() {
  local temp_dir workspace output status stub_dir capture
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  stub_dir="${temp_dir}/bin"
  capture="${temp_dir}/capture"
  mkdir -p "${workspace}/scripts"
  write_stub_psql "${stub_dir}" "${capture}"

  set +e
  output="$(CAPTURE_DIR="${capture}" PATH="${stub_dir}:${PATH}" bash "${HELPER}" --workspace-dir "${workspace}" --no-start-tunnel -- -c "select 1;" 2>&1)"
  status=$?
  set -e

  assert_equals "3" "${status}"
  assert_contains "Missing tunnel env file" "${output}"
}

test_missing_psql_fails_before_starting_tunnel() {
  local temp_dir workspace limited_bin output status
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  limited_bin="${temp_dir}/limited-bin"
  mkdir -p "${limited_bin}"
  ln -s /usr/bin/basename "${limited_bin}/basename"
  ln -s /usr/bin/dirname "${limited_bin}/dirname"
  write_fixture_workspace "${workspace}"

  set +e
  output="$(PATH="${limited_bin}" /bin/bash "${HELPER}" --workspace-dir "${workspace}" -- -c "select 1;" 2>&1)"
  status=$?
  set -e

  assert_equals "2" "${status}"
  assert_contains "psql is required" "${output}"
  assert_file_missing "${workspace}/start-called"
}

test_no_psql_args_non_tty_runs_probe() {
  local temp_dir workspace stub_dir capture
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  stub_dir="${temp_dir}/bin"
  capture="${temp_dir}/capture"
  write_fixture_workspace "${workspace}"
  write_stub_psql "${stub_dir}" "${capture}"

  CAPTURE_DIR="${capture}" PATH="${stub_dir}:${PATH}" bash "${HELPER}" \
    --workspace-dir "${workspace}" >/dev/null

  assert_contains "select current_database(), current_user;" "$(cat "${capture}/psql-call.txt")"
}

test_existing_pgoptions_are_preserved_with_readonly_settings_appended() {
  local temp_dir workspace stub_dir capture
  temp_dir="$(mktemp -d)"
  workspace="${temp_dir}/workspace"
  stub_dir="${temp_dir}/bin"
  capture="${temp_dir}/capture"
  write_fixture_workspace "${workspace}"
  write_stub_psql "${stub_dir}" "${capture}"

  CAPTURE_DIR="${capture}" PATH="${stub_dir}:${PATH}" PGOPTIONS="-c search_path=public" bash "${HELPER}" \
    --workspace-dir "${workspace}" \
    -- \
    -c "select 3;" >/dev/null

  local call
  call="$(cat "${capture}/psql-call.txt")"
  assert_contains "PGOPTIONS=-c search_path=public -c default_transaction_read_only=on" "${call}"
  assert_contains "statement_timeout=30000" "${call}"
  assert_contains "lock_timeout=5000" "${call}"
}

test_runs_real_psql_with_readonly_tunnel_env
test_no_start_tunnel_uses_existing_env_file
test_status_delegates_to_tunnel_status_helper
test_missing_env_file_fails_without_starting
test_missing_psql_fails_before_starting_tunnel
test_no_psql_args_non_tty_runs_probe
test_existing_pgoptions_are_preserved_with_readonly_settings_appended

echo "symphony_curation_db_psql tests passed"
