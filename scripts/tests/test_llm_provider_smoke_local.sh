#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_TMP="$(mktemp -d)"
trap 'rm -rf "${TEST_TMP}"' EXIT

mkdir -p "${TEST_TMP}/bin"
cat > "${TEST_TMP}/bin/curl" <<'MOCK_CURL'
#!/usr/bin/env bash
set -euo pipefail

output_file=""
url=""
while (($#)); do
  case "$1" in
    -o)
      output_file="$2"
      shift 2
      ;;
    -w|--max-time|-H)
      shift 2
      ;;
    -sS)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done

body="{}"
if [[ "${url}" == */api/admin/health/llm-providers ]]; then
  body="${MOCK_PROVIDER_HEALTH_BODY:?}"
fi
if [[ -n "${output_file}" && "${output_file}" != "/dev/null" ]]; then
  printf '%s' "${body}" > "${output_file}"
fi
printf '200'
MOCK_CURL
chmod +x "${TEST_TMP}/bin/curl"

run_smoke() {
  local case_dir="$1"
  shift
  mkdir -p "${case_dir}"
  (
    cd "${case_dir}"
    env PATH="${TEST_TMP}/bin:${PATH}" "$@" \
      bash "${REPO_ROOT}/scripts/testing/llm_provider_smoke_local.sh"
  )
}

generic_health='{"errors":[],"providers":[{"provider_id":"custom","readiness":"ready","route_available":true,"mapped_model_ids":["custom/model"],"optional_for_runtime":false}]}'
generic_output="$(run_smoke "${TEST_TMP}/generic" MOCK_PROVIDER_HEALTH_BODY="${generic_health}")"
[[ "${generic_output}" == *"Result: pass"* ]]

optional_health='{"errors":[],"providers":[{"provider_id":"openrouter","readiness":"missing_api_key","route_available":false,"mapped_model_ids":["deepseek/deepseek-v4-pro-0813"],"optional_for_runtime":true}]}'
optional_output="$(run_smoke \
  "${TEST_TMP}/optional" \
  MOCK_PROVIDER_HEALTH_BODY="${optional_health}" \
  SMOKE_OPTIONAL_PROVIDER_ID="openrouter" \
  SMOKE_OPTIONAL_MODEL_ID="deepseek/deepseek-v4-pro-0813")"
[[ "${optional_output}" == *"Result: pass"* ]]

inconsistent_health='{"errors":[],"providers":[{"provider_id":"custom","readiness":"missing_api_key","route_available":true,"mapped_model_ids":["custom/model"],"optional_for_runtime":true}]}'
inconsistent_output="$(run_smoke "${TEST_TMP}/inconsistent" MOCK_PROVIDER_HEALTH_BODY="${inconsistent_health}")"
[[ "${inconsistent_output}" == *"Result: fail"* ]]

echo "llm_provider_smoke_local tests passed"
