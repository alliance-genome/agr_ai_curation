#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

json_field() {
  local payload="$1"
  local field_path="$2"
  python3 - "${payload}" "${field_path}" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
parts = sys.argv[2].split(".")
value = payload
for part in parts:
    value = value[part]
print(value)
PY
}

test_local_transformers_target_check_passes_for_local_compose_url() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"
    ensure_python_bin

    PASS_COUNT=0
    FAIL_COUNT=0
    LAST_DERIVED_JSON=""

    assert_local_reranker_target \
      "local_transformers" \
      '{"services":{"reranker":{"url":"http://reranker-transformers:8080"}}}'

    [[ "$(json_field "${LAST_DERIVED_JSON}" "result")" == "pass" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.reason")" == "local_transformers reranker URL matches configured target 'http://reranker-transformers:8080'" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.expected_url")" == "http://reranker-transformers:8080" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.actual_url")" == "http://reranker-transformers:8080" ]]
    [[ "${PASS_COUNT}" -eq 1 ]]
    [[ "${FAIL_COUNT}" -eq 0 ]]
  )
}

test_local_transformers_target_check_passes_for_custom_override_url() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"
    ensure_python_bin

    export RERANKER_URL="http://custom-reranker:9090"
    PASS_COUNT=0
    FAIL_COUNT=0
    LAST_DERIVED_JSON=""

    assert_local_reranker_target \
      "local_transformers" \
      '{"services":{"reranker":{"url":"http://custom-reranker:9090"}}}'

    [[ "$(json_field "${LAST_DERIVED_JSON}" "result")" == "pass" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.reason")" == "local_transformers reranker URL matches configured target 'http://custom-reranker:9090'" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.expected_url")" == "http://custom-reranker:9090" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.actual_url")" == "http://custom-reranker:9090" ]]
    [[ "${PASS_COUNT}" -eq 1 ]]
    [[ "${FAIL_COUNT}" -eq 0 ]]
  )
}

test_local_transformers_target_check_passes_for_repo_env_url() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"
    ensure_python_bin

    local temp_env
    temp_env="$(mktemp)"
    trap 'rm -f "${temp_env}"' EXIT
    printf 'RERANKER_URL=http://repo-env-reranker:7070\n' > "${temp_env}"

    unset RERANKER_URL
    REPO_ENV_FILE="${temp_env}"
    PASS_COUNT=0
    FAIL_COUNT=0
    LAST_DERIVED_JSON=""

    assert_local_reranker_target \
      "local_transformers" \
      '{"services":{"reranker":{"url":"http://repo-env-reranker:7070"}}}'

    [[ "$(json_field "${LAST_DERIVED_JSON}" "result")" == "pass" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.reason")" == "local_transformers reranker URL matches configured target 'http://repo-env-reranker:7070'" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.expected_url")" == "http://repo-env-reranker:7070" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.actual_url")" == "http://repo-env-reranker:7070" ]]
    [[ "${PASS_COUNT}" -eq 1 ]]
    [[ "${FAIL_COUNT}" -eq 0 ]]
  )
}

test_local_transformers_target_check_fails_for_mismatched_configured_url() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"
    ensure_python_bin

    export RERANKER_URL="http://custom-reranker:9090"
    PASS_COUNT=0
    FAIL_COUNT=0
    LAST_DERIVED_JSON=""

    assert_local_reranker_target \
      "local_transformers" \
      '{"services":{"reranker":{"url":"http://remote-reranker:8080"}}}'

    [[ "$(json_field "${LAST_DERIVED_JSON}" "result")" == "fail" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.expected_url")" == "http://custom-reranker:9090" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.actual_url")" == "http://remote-reranker:8080" ]]
    [[ "${PASS_COUNT}" -eq 0 ]]
    [[ "${FAIL_COUNT}" -eq 1 ]]
  )
}

test_compose_override_file_is_skipped_when_repo_env_sets_reranker_url() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"

    local temp_env
    temp_env="$(mktemp)"
    trap 'rm -f "${temp_env}"' EXIT
    printf 'RERANKER_URL=http://repo-env-reranker:7070\n' > "${temp_env}"

    unset RERANKER_URL ADMIN_EMAILS
    REPO_ENV_FILE="${temp_env}"
    cleanup_smoke_compose_override
    ensure_smoke_compose_override_file

    [[ -z "${SMOKE_COMPOSE_OVERRIDE_FILE}" ]]
  )
}

test_compose_override_file_is_created_for_custom_reranker_url() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"
    ensure_python_bin

    export RERANKER_URL="http://custom-reranker:9090"
    unset ADMIN_EMAILS
    cleanup_smoke_compose_override
    ensure_smoke_compose_override_file

    [[ -n "${SMOKE_COMPOSE_OVERRIDE_FILE}" ]]
    [[ -f "${SMOKE_COMPOSE_OVERRIDE_FILE}" ]]
    grep -F 'RERANKER_URL: "http://custom-reranker:9090"' "${SMOKE_COMPOSE_OVERRIDE_FILE}" >/dev/null

    local -a compose_args=()
    append_smoke_compose_args compose_args
    [[ "${#compose_args[@]}" -eq 4 ]]
    [[ "${compose_args[0]}" == "-f" ]]
    [[ "${compose_args[1]}" == "docker-compose.yml" ]]
    [[ "${compose_args[2]}" == "-f" ]]
    [[ "${compose_args[3]}" == "${SMOKE_COMPOSE_OVERRIDE_FILE}" ]]

    cleanup_smoke_compose_override
    [[ -z "${SMOKE_COMPOSE_OVERRIDE_FILE}" ]]
  )
}

test_run_http_check_sends_api_key_via_stdin_only_for_admin_paths() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"
    ensure_python_bin

    local temp_dir
    temp_dir="$(mktemp -d)"
    trap 'rm -rf "${temp_dir}"' EXIT

    cat > "${temp_dir}/curl" <<'EOF'
#!/usr/bin/env bash
out_file=""
stdin_header=""
printf '%s\n' "$@" > "${CURL_LOG_DIR}/args.txt"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o)
      out_file="$2"
      shift 2
      ;;
    -H)
      if [[ "$2" == "@-" ]]; then
        stdin_header="$(cat)"
      fi
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
printf '%s' "${stdin_header}" > "${CURL_LOG_DIR}/stdin_header.txt"
printf '{"status":"healthy"}' > "${out_file}"
printf '200'
EOF
    chmod +x "${temp_dir}/curl"

    export PATH="${temp_dir}:${PATH}"
    export CURL_LOG_DIR="${temp_dir}"
    export TESTING_API_KEY="smoke-secret-value"
    PASS_COUNT=0
    FAIL_COUNT=0

    run_http_check "none" "NONE_CONNECTIONS" "/api/admin/health/connections" "200" >/dev/null
    [[ "$(cat "${temp_dir}/stdin_header.txt")" == "X-API-Key: smoke-secret-value" ]]
    if grep -F "smoke-secret-value" "${temp_dir}/args.txt" >/dev/null; then
      echo "TESTING_API_KEY leaked into curl argv for admin path" >&2
      exit 1
    fi

    run_http_check "none" "NONE_HEALTH" "/health" "200" >/dev/null
    [[ -z "$(cat "${temp_dir}/stdin_header.txt")" ]]
    if grep -F "smoke-secret-value" "${temp_dir}/args.txt" >/dev/null; then
      echo "TESTING_API_KEY leaked into curl argv for non-admin path" >&2
      exit 1
    fi
    [[ "${PASS_COUNT}" -eq 2 ]]
    [[ "${FAIL_COUNT}" -eq 0 ]]
  )
}

test_load_smoke_environment_preserves_caller_api_key_over_blank_home_env() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"

    local temp_dir
    temp_dir="$(mktemp -d)"
    trap 'rm -rf "${temp_dir}"' EXIT

    local home_env="${temp_dir}/home.env"
    cat > "${home_env}" <<'EOF'
TESTING_API_KEY=
ADMIN_EMAILS=
OTHER_HOME_VAR=loaded-from-home
EOF

    export TEST_SECRETS_ENV_FILE="${home_env}"
    export TESTING_API_KEY="caller-forwarded-key"
    export ADMIN_EMAILS="admin@example.org"
    unset SMOKE_EXTRA_ENV_FILE SMOKE_COMPOSE_ENV_FILE AGR_DEV_ENV_FILE

    load_smoke_environment >/dev/null

    [[ "${TESTING_API_KEY}" == "caller-forwarded-key" ]]
    [[ "${ADMIN_EMAILS}" == "admin@example.org" ]]
    [[ "${OTHER_HOME_VAR}" == "loaded-from-home" ]]
  )
}

test_load_smoke_environment_reads_testing_api_key_from_compose_env_file_for_direct_run() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"

    local temp_dir
    temp_dir="$(mktemp -d)"
    trap 'rm -rf "${temp_dir}"' EXIT

    local home_env="${temp_dir}/home.env"
    printf 'TESTING_API_KEY=\n' > "${home_env}"

    local compose_env="${temp_dir}/compose.env"
    cat > "${compose_env}" <<'EOF'
TESTING_API_KEY="compose-file-key"
UNRELATED_COMPOSE_VAR=should-not-mutate-shell
EOF

    export TEST_SECRETS_ENV_FILE="${home_env}"
    export SMOKE_COMPOSE_ENV_FILE="${compose_env}"
    unset TESTING_API_KEY UNRELATED_COMPOSE_VAR SMOKE_EXTRA_ENV_FILE

    load_smoke_environment >/dev/null

    [[ "${TESTING_API_KEY}" == "compose-file-key" ]]
    [[ -z "${UNRELATED_COMPOSE_VAR:-}" ]]
  )
}

test_read_env_file_value_uses_last_assignment_quotes_and_inline_comments() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"

    local temp_dir
    temp_dir="$(mktemp -d)"
    trap 'rm -rf "${temp_dir}"' EXIT

    local env_file="${temp_dir}/dotenv.env"
    cat > "${env_file}" <<'EOF'
# Commented assignment should be ignored
# TESTING_API_KEY=commented-out
TESTING_API_KEY=first-value
export TESTING_API_KEY=second-value # trailing inline comment
QUOTED_DOUBLE="val # not a comment" # trailing comment
QUOTED_SINGLE='single # not a comment'   # trailing comment
CLEARED_KEY=initial
CLEARED_KEY= # cleared by last assignment
EOF

    [[ "$(read_env_file_value "${env_file}" "TESTING_API_KEY")" == "second-value" ]]
    [[ "$(read_env_file_value "${env_file}" "QUOTED_DOUBLE")" == "val # not a comment" ]]
    [[ "$(read_env_file_value "${env_file}" "QUOTED_SINGLE")" == "single # not a comment" ]]
    [[ "$(read_env_file_value "${env_file}" "CLEARED_KEY")" == "" ]]
  )
}

test_admin_emails_compose_override_handles_unset_nonempty_and_explicit_empty() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"
    ensure_python_bin

    local temp_dir
    temp_dir="$(mktemp -d)"
    trap 'rm -rf "${temp_dir}"' EXIT

    local blank_home_env="${temp_dir}/blank_home.env"
    cat > "${blank_home_env}" <<'EOF'
ADMIN_EMAILS=
TESTING_API_KEY=
EOF

    local populated_home_env="${temp_dir}/populated_home.env"
    cat > "${populated_home_env}" <<'EOF'
ADMIN_EMAILS=home@example.org
EOF

    # 1) Unset caller + blank home env -> ADMIN_EMAILS stays unset and is omitted from override YAML
    unset ADMIN_EMAILS RERANKER_URL SMOKE_EXTRA_ENV_FILE SMOKE_COMPOSE_ENV_FILE AGR_DEV_ENV_FILE
    export TEST_SECRETS_ENV_FILE="${blank_home_env}"
    load_smoke_environment >/dev/null
    [[ -z "${ADMIN_EMAILS+set}" ]]
    cleanup_smoke_compose_override
    ensure_smoke_compose_override_file
    [[ -z "${SMOKE_COMPOSE_OVERRIDE_FILE}" ]]

    # 1b) Unset ADMIN_EMAILS stays omitted even when RERANKER_URL creates an override YAML
    export RERANKER_URL="http://custom-reranker:9090"
    ensure_smoke_compose_override_file
    [[ -n "${SMOKE_COMPOSE_OVERRIDE_FILE}" ]]
    if grep -F 'ADMIN_EMAILS:' "${SMOKE_COMPOSE_OVERRIDE_FILE}" >/dev/null; then
      echo "Unset ADMIN_EMAILS unexpectedly written to compose override file" >&2
      exit 1
    fi
    cleanup_smoke_compose_override
    unset RERANKER_URL

    # 2) Explicit non-empty caller ADMIN_EMAILS -> written to override YAML
    export ADMIN_EMAILS="a@example.org"
    export TEST_SECRETS_ENV_FILE="${populated_home_env}"
    load_smoke_environment >/dev/null
    [[ "${ADMIN_EMAILS}" == "a@example.org" ]]
    cleanup_smoke_compose_override
    ensure_smoke_compose_override_file
    [[ -n "${SMOKE_COMPOSE_OVERRIDE_FILE}" ]]
    grep -F 'ADMIN_EMAILS: "a@example.org"' "${SMOKE_COMPOSE_OVERRIDE_FILE}" >/dev/null
    cleanup_smoke_compose_override

    # 3) Explicit empty caller ADMIN_EMAILS="" -> preserved as empty string rather than falling back to home env
    export ADMIN_EMAILS=""
    export TEST_SECRETS_ENV_FILE="${populated_home_env}"
    load_smoke_environment >/dev/null
    [[ -n "${ADMIN_EMAILS+set}" ]]
    [[ "${ADMIN_EMAILS}" == "" ]]
    cleanup_smoke_compose_override
    ensure_smoke_compose_override_file
    [[ -n "${SMOKE_COMPOSE_OVERRIDE_FILE}" ]]
    grep -F 'ADMIN_EMAILS: ""' "${SMOKE_COMPOSE_OVERRIDE_FILE}" >/dev/null
    cleanup_smoke_compose_override
  )
}

test_local_transformers_target_check_passes_for_local_compose_url
test_local_transformers_target_check_passes_for_custom_override_url
test_local_transformers_target_check_passes_for_repo_env_url
test_local_transformers_target_check_fails_for_mismatched_configured_url
test_compose_override_file_is_skipped_when_repo_env_sets_reranker_url
test_compose_override_file_is_created_for_custom_reranker_url
test_run_http_check_sends_api_key_via_stdin_only_for_admin_paths
test_load_smoke_environment_preserves_caller_api_key_over_blank_home_env
test_load_smoke_environment_reads_testing_api_key_from_compose_env_file_for_direct_run
test_read_env_file_value_uses_last_assignment_quotes_and_inline_comments
test_admin_emails_compose_override_handles_unset_nonempty_and_explicit_empty

echo "rerank_provider_smoke_local tests passed"
