#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_TMP="$(mktemp -d)"
trap 'rm -rf "${TEST_TMP}"' EXIT

# Run a copy of the script in an empty tree so no other helper scripts run.
mkdir -p "${TEST_TMP}/tree/scripts/utilities" "${TEST_TMP}/bin"
cp "${REPO_ROOT}/scripts/utilities/collect_agent_evidence.sh" "${TEST_TMP}/tree/scripts/utilities/"

cat > "${TEST_TMP}/bin/docker" <<'MOCK_DOCKER'
#!/usr/bin/env bash
exit 0
MOCK_DOCKER

cat > "${TEST_TMP}/bin/curl" <<'MOCK_CURL'
#!/usr/bin/env bash
set -euo pipefail
url="${*: -1}"
if [[ "${url}" == */api/admin/health/llm-providers ]]; then
  printf '%s\n' "$*" > "${MOCK_RECORD_DIR}/provider_args"
  if [[ " $* " == *" -H @- "* ]]; then
    cat > "${MOCK_RECORD_DIR}/provider_stdin"
  fi
  if [[ "${MOCK_PROVIDER_STATUS:-200}" == "401" ]]; then
    echo "curl: (22) The requested URL returned error: 401" >&2
    exit 22
  fi
fi
printf '{}'
MOCK_CURL
chmod +x "${TEST_TMP}/bin/docker" "${TEST_TMP}/bin/curl"

# Usage: run_collect <case_dir> <bash options> [VAR=value ...]; returns the script's exit code.
run_collect() {
  local case_dir="$1"
  local bash_opts="$2"
  shift 2
  mkdir -p "${case_dir}/record"
  local rc=0
  env PATH="${TEST_TMP}/bin:${PATH}" MOCK_RECORD_DIR="${case_dir}/record" "$@" \
    bash ${bash_opts} "${TEST_TMP}/tree/scripts/utilities/collect_agent_evidence.sh" "${case_dir}/out" \
    >"${case_dir}/stdout" 2>"${case_dir}/stderr" || rc=$?
  return "${rc}"
}

# With TESTING_API_KEY set, the header goes over stdin, never on the command line.
fake_value="fake-$(date +%s)-$$"
run_collect "${TEST_TMP}/with_key" "" TESTING_API_KEY="${fake_value}"
args="$(cat "${TEST_TMP}/with_key/record/provider_args")"
[[ " ${args} " == *" -H @- "* ]]
[[ "${args}" != *"${fake_value}"* ]]
[[ "$(cat "${TEST_TMP}/with_key/record/provider_stdin")" == "X-API-Key: ${fake_value}" ]]
[[ "$(cat "${TEST_TMP}/with_key/out/capture_status.txt")" == "failures=0" ]]

# Without it (local dev mode), no header is sent.
run_collect "${TEST_TMP}/without_key" "" TESTING_API_KEY=
args="$(cat "${TEST_TMP}/without_key/record/provider_args")"
[[ "${args}" != *"-H"* ]]
[[ ! -e "${TEST_TMP}/without_key/record/provider_stdin" ]]
[[ "$(cat "${TEST_TMP}/without_key/out/capture_status.txt")" == "failures=0" ]]

# Under bash -x the key never reaches the trace or any other output.
run_collect "${TEST_TMP}/xtrace" "-x" TESTING_API_KEY="${fake_value}"
# The fetch function's trace lands in its .err file; make sure it was traced.
grep -q "TESTING_API_KEY:+set\|-H @-" "${TEST_TMP}/xtrace/out/llm_provider_health.err"
if grep -rqF "${fake_value}" "${TEST_TMP}/xtrace/stdout" "${TEST_TMP}/xtrace/stderr" "${TEST_TMP}/xtrace/out"; then
  echo "testing API key appeared in traced output" >&2
  exit 1
fi

# A refused provider report fails the capture visibly.
rc=0
run_collect "${TEST_TMP}/refused" "" TESTING_API_KEY="${fake_value}" MOCK_PROVIDER_STATUS=401 || rc=$?
[[ "${rc}" == "1" ]]
grep -q "Agent evidence capture is partial (1 failures)" "${TEST_TMP}/refused/stderr"
[[ "$(cat "${TEST_TMP}/refused/out/capture_status.txt")" == "failures=1" ]]
grep -q "401" "${TEST_TMP}/refused/out/llm_provider_health.err"
[[ ! -s "${TEST_TMP}/refused/out/llm_provider_health.json" ]]

echo "collect_agent_evidence tests passed"
