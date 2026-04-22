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

test_compose_override_file_is_created_for_custom_reranker_url() {
  (
    cd "${REPO_ROOT}"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/testing/rerank_provider_smoke_local.sh"
    ensure_python_bin

    export RERANKER_URL="http://custom-reranker:9090"
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

test_local_transformers_target_check_passes_for_local_compose_url
test_local_transformers_target_check_passes_for_custom_override_url
test_local_transformers_target_check_fails_for_mismatched_configured_url
test_compose_override_file_is_created_for_custom_reranker_url

echo "rerank_provider_smoke_local tests passed"
