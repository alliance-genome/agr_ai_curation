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
fi
printf '{}'
MOCK_CURL
chmod +x "${TEST_TMP}/bin/docker" "${TEST_TMP}/bin/curl"

run_collect() {
  local case_dir="$1"
  shift
  mkdir -p "${case_dir}/record"
  env PATH="${TEST_TMP}/bin:${PATH}" MOCK_RECORD_DIR="${case_dir}/record" "$@" \
    bash "${TEST_TMP}/tree/scripts/utilities/collect_agent_evidence.sh" "${case_dir}/out" \
    >/dev/null 2>"${case_dir}/stderr"
}

# With TESTING_API_KEY set, the header goes over stdin, never on the command line.
fake_value="fake-$(date +%s)-$$"
run_collect "${TEST_TMP}/with_key" TESTING_API_KEY="${fake_value}"
args="$(cat "${TEST_TMP}/with_key/record/provider_args")"
[[ " ${args} " == *" -H @- "* ]]
[[ "${args}" != *"${fake_value}"* ]]
[[ "$(cat "${TEST_TMP}/with_key/record/provider_stdin")" == "X-API-Key: ${fake_value}" ]]
[[ "$(cat "${TEST_TMP}/with_key/out/capture_status.txt")" == "failures=0" ]]

# Without it (local dev mode), no header is sent.
run_collect "${TEST_TMP}/without_key" TESTING_API_KEY=
args="$(cat "${TEST_TMP}/without_key/record/provider_args")"
[[ "${args}" != *"-H"* ]]
[[ ! -e "${TEST_TMP}/without_key/record/provider_stdin" ]]
[[ "$(cat "${TEST_TMP}/without_key/out/capture_status.txt")" == "failures=0" ]]

echo "collect_agent_evidence tests passed"
