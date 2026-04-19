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
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.expected_url")" == "http://reranker-transformers:8080" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.actual_url")" == "http://reranker-transformers:8080" ]]
    [[ "${PASS_COUNT}" -eq 1 ]]
    [[ "${FAIL_COUNT}" -eq 0 ]]
  )
}

test_local_transformers_target_check_fails_for_external_url() {
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
      '{"services":{"reranker":{"url":"http://remote-reranker:8080"}}}'

    [[ "$(json_field "${LAST_DERIVED_JSON}" "result")" == "fail" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.expected_url")" == "http://reranker-transformers:8080" ]]
    [[ "$(json_field "${LAST_DERIVED_JSON}" "details.actual_url")" == "http://remote-reranker:8080" ]]
    [[ "${PASS_COUNT}" -eq 0 ]]
    [[ "${FAIL_COUNT}" -eq 1 ]]
  )
}

test_local_transformers_target_check_passes_for_local_compose_url
test_local_transformers_target_check_fails_for_external_url

echo "rerank_provider_smoke_local tests passed"
