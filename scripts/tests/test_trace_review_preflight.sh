#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
preflight_script="${REPO_ROOT}/scripts/testing/trace_review_preflight.sh"

assert_contains() {
  local pattern="$1"
  local file="$2"
  if ! rg -n --fixed-strings "$pattern" "$file" >/dev/null 2>&1; then
    echo "Expected to find '$pattern' in $file" >&2
    cat "$file" >&2
    exit 1
  fi
}

make_stub_tools() {
  local stub_dir="$1"
  mkdir -p "$stub_dir"

  cat >"${stub_dir}/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

url="${@: -1}"
printf 'curl_args=%s\n' "$*" >> "${TRACE_REVIEW_STUB_LOG:?}"

if [[ "${TRACE_REVIEW_CURL_STUB_MODE:-healthy}" == "down" ]]; then
  echo "connection failed" >&2
  exit 7
fi

case "$url" in
  */health/preflight*)
    if [[ "${TRACE_REVIEW_CURL_STUB_MODE:-healthy}" == "missing-preflight" ]]; then
      echo "not found" >&2
      exit 22
    fi
    printf '{"status":"ok","service":"trace_review_backend"}\n'
    ;;
  */api/public/health)
    printf '{"status":"ok"}\n'
    ;;
  */health)
    if [[ "${TRACE_REVIEW_CURL_STUB_MODE:-healthy}" == "proxy" ]]; then
      printf '{"status":"ok","message":"Symphony review proxy"}\n'
    else
      printf '{"status":"ok","message":"Trace Review API is running","cache_stats":{"size":0,"ttl_hours":1}}\n'
    fi
    ;;
  *)
    echo "unexpected curl URL: $url" >&2
    exit 22
    ;;
esac
EOF

  cat >"${stub_dir}/lsof" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF

  cat >"${stub_dir}/ss" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [[ "$*" == *"sport = :8001"* ]]; then
  echo "State Recv-Q Send-Q Local Address:Port Peer Address:Port Process"
  echo 'LISTEN 0 4096 0.0.0.0:8001 0.0.0.0:* users:(("node",pid=2222,fd=11))'
  exit 0
fi

exit 1
EOF

  cat >"${stub_dir}/getent" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "hosts" && "${2:-}" == "remote.example" ]]; then
  echo "10.8.0.20 remote.example"
  exit 0
fi

exit 2
EOF

  cat >"${stub_dir}/ip" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "10.8.0.20 via 10.8.0.1 dev tun0 src 10.8.0.2"
EOF

  cat >"${stub_dir}/nc" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'nc_args=%s\n' "$*" >> "${TRACE_REVIEW_STUB_LOG:?}"
exit "${TRACE_REVIEW_NC_STUB_RC:-0}"
EOF

  chmod +x "${stub_dir}/curl" "${stub_dir}/lsof" "${stub_dir}/ss" "${stub_dir}/getent" "${stub_dir}/ip" "${stub_dir}/nc"
}

run_preflight() {
  local temp_home="$1"
  local stub_dir="$2"
  local output_file="$3"
  shift 3

  set +e
  env \
    HOME="$temp_home" \
    PATH="${stub_dir}:${PATH}" \
    NO_COLOR=1 \
    TRACE_REVIEW_PREFLIGHT_CURL_CMD="${TRACE_REVIEW_PREFLIGHT_CURL_CMD:-curl}" \
    TRACE_REVIEW_PREFLIGHT_SS_CMD="${TRACE_REVIEW_PREFLIGHT_SS_CMD:-ss}" \
    TRACE_REVIEW_PREFLIGHT_GETENT_CMD="${TRACE_REVIEW_PREFLIGHT_GETENT_CMD:-getent}" \
    TRACE_REVIEW_PREFLIGHT_IP_CMD="${TRACE_REVIEW_PREFLIGHT_IP_CMD:-ip}" \
    TRACE_REVIEW_PREFLIGHT_NC_CMD="${TRACE_REVIEW_PREFLIGHT_NC_CMD:-nc}" \
    TRACE_REVIEW_PREFLIGHT_TIMEOUT_SECONDS="1" \
    "$preflight_script" "$@" >"$output_file" 2>&1
  local rc=$?
  set -e

  echo "$rc"
}

test_required_tool_failures_are_explicit() {
  local temp_root temp_home stub_dir output_file stub_log rc
  temp_root="$(mktemp -d)"
  temp_home="${temp_root}/home"
  stub_dir="${temp_root}/stubbin"
  output_file="${temp_root}/output.log"
  stub_log="${temp_root}/stub.log"
  mkdir -p "$temp_home"
  trap 'rm -rf "$temp_root"' RETURN

  make_stub_tools "$stub_dir"

  rc="$(
    TRACE_REVIEW_CURL_STUB_MODE="healthy" \
    TRACE_REVIEW_STUB_LOG="$stub_log" \
    LANGFUSE_HOST="http://remote.example:3000" \
    LANGFUSE_PUBLIC_KEY="pk-lf-test" \
    LANGFUSE_SECRET_KEY="sk-lf-test" \
    LANGFUSE_LOCAL_HOST="http://local.example:3000" \
    LANGFUSE_LOCAL_PUBLIC_KEY="pk-lf-local" \
    LANGFUSE_LOCAL_SECRET_KEY="sk-lf-local" \
    TRACE_REVIEW_PREFLIGHT_PYTHON_CMD="missing-python3" \
    TRACE_REVIEW_PREFLIGHT_SS_CMD="missing-ss" \
    TRACE_REVIEW_PREFLIGHT_NC_CMD="missing-nc" \
    run_preflight "$temp_home" "$stub_dir" "$output_file" --backend-url http://127.0.0.1:8001 --source remote
  )"

  if [[ "$rc" -ne 21 ]]; then
    echo "Expected missing required tools to exit 21, got $rc" >&2
    cat "$output_file" >&2
    exit 1
  fi

  assert_contains "Required command not available: missing-python3" "$output_file"
  assert_contains "Required command not available: missing-ss" "$output_file"
  assert_contains "Required command not available: missing-nc" "$output_file"
  assert_contains "TRACE_REVIEW_PREFLIGHT_RESULT exit_code=21" "$output_file"
}

test_default_backend_url_uses_trace_review_host_port_only() {
  local temp_root temp_home stub_dir output_file stub_log rc
  temp_root="$(mktemp -d)"
  temp_home="${temp_root}/home"
  stub_dir="${temp_root}/stubbin"
  output_file="${temp_root}/output.log"
  stub_log="${temp_root}/stub.log"
  mkdir -p "$temp_home"
  trap 'rm -rf "$temp_root"' RETURN

  make_stub_tools "$stub_dir"

  rc="$(
    TRACE_REVIEW_CURL_STUB_MODE="healthy" \
    TRACE_REVIEW_STUB_LOG="$stub_log" \
    BACKEND_HOST_PORT="9999" \
    TRACE_REVIEW_BACKEND_PORT="7777" \
    LANGFUSE_HOST="http://remote.example:3000" \
    LANGFUSE_PUBLIC_KEY="pk-lf-test" \
    LANGFUSE_SECRET_KEY="sk-lf-test" \
    LANGFUSE_LOCAL_HOST="http://local.example:3000" \
    LANGFUSE_LOCAL_PUBLIC_KEY="pk-lf-local" \
    LANGFUSE_LOCAL_SECRET_KEY="sk-lf-local" \
    run_preflight "$temp_home" "$stub_dir" "$output_file" --source remote --ssh-host prod.example
  )"

  if [[ "$rc" -ne 0 ]]; then
    echo "Expected default backend URL selection to pass, got $rc" >&2
    cat "$output_file" >&2
    exit 1
  fi

  assert_contains "Port 8001 listener: node/2222" "$output_file"
  assert_contains "curl_args=-fsS --max-time 1 http://127.0.0.1:8001/health" "$stub_log"
  assert_contains "TRACE_REVIEW_PREFLIGHT_RESULT exit_code=0" "$output_file"
}

test_default_backend_url_uses_canonical_trace_review_host_port() {
  local temp_root temp_home stub_dir output_file stub_log rc
  temp_root="$(mktemp -d)"
  temp_home="${temp_root}/home"
  stub_dir="${temp_root}/stubbin"
  output_file="${temp_root}/output.log"
  stub_log="${temp_root}/stub.log"
  mkdir -p "$temp_home"
  trap 'rm -rf "$temp_root"' RETURN

  make_stub_tools "$stub_dir"

  rc="$(
    TRACE_REVIEW_CURL_STUB_MODE="healthy" \
    TRACE_REVIEW_STUB_LOG="$stub_log" \
    TRACE_REVIEW_BACKEND_HOST_PORT="8901" \
    LANGFUSE_HOST="http://remote.example:3000" \
    LANGFUSE_PUBLIC_KEY="pk-lf-test" \
    LANGFUSE_SECRET_KEY="sk-lf-test" \
    LANGFUSE_LOCAL_HOST="http://local.example:3000" \
    LANGFUSE_LOCAL_PUBLIC_KEY="pk-lf-local" \
    LANGFUSE_LOCAL_SECRET_KEY="sk-lf-local" \
    run_preflight "$temp_home" "$stub_dir" "$output_file" --source remote --ssh-host prod.example
  )"

  if [[ "$rc" -ne 0 ]]; then
    echo "Expected canonical TraceReview host port selection to pass, got $rc" >&2
    cat "$output_file" >&2
    exit 1
  fi

  assert_contains "Port 8901 listener: none detected before HTTP probe" "$output_file"
  assert_contains "curl_args=-fsS --max-time 1 http://127.0.0.1:8901/health" "$stub_log"
  assert_contains "TRACE_REVIEW_PREFLIGHT_RESULT exit_code=0" "$output_file"
}

test_distinguishes_backend_down_and_missing_langfuse_config() {
  local temp_root temp_home stub_dir output_file stub_log rc
  temp_root="$(mktemp -d)"
  temp_home="${temp_root}/home"
  stub_dir="${temp_root}/stubbin"
  output_file="${temp_root}/output.log"
  stub_log="${temp_root}/stub.log"
  mkdir -p "$temp_home"
  trap 'rm -rf "$temp_root"' RETURN

  make_stub_tools "$stub_dir"

  rc="$(
    TRACE_REVIEW_CURL_STUB_MODE="down" \
    TRACE_REVIEW_STUB_LOG="$stub_log" \
    LANGFUSE_HOST="" \
    LANGFUSE_PUBLIC_KEY="" \
    LANGFUSE_SECRET_KEY="" \
    LANGFUSE_LOCAL_HOST="" \
    LANGFUSE_LOCAL_PUBLIC_KEY="" \
    LANGFUSE_LOCAL_SECRET_KEY="" \
    run_preflight "$temp_home" "$stub_dir" "$output_file" --backend-url http://127.0.0.1:8001 --source remote
  )"

  if [[ "$rc" -ne 22 ]]; then
    echo "Expected backend-down preflight to exit 22, got $rc" >&2
    cat "$output_file" >&2
    exit 1
  fi

  assert_contains "TraceReview backend health unreachable at http://127.0.0.1:8001/health" "$output_file"
  assert_contains "Selected source 'remote' is missing Langfuse configuration" "$output_file"
  assert_contains "TRACE_REVIEW_PREFLIGHT_RESULT exit_code=22" "$output_file"
  assert_contains "No production changes were attempted." "$output_file"
}

test_detects_port_proxy_confusion() {
  local temp_root temp_home stub_dir output_file stub_log rc
  temp_root="$(mktemp -d)"
  temp_home="${temp_root}/home"
  stub_dir="${temp_root}/stubbin"
  output_file="${temp_root}/output.log"
  stub_log="${temp_root}/stub.log"
  mkdir -p "$temp_home"
  trap 'rm -rf "$temp_root"' RETURN

  make_stub_tools "$stub_dir"

  rc="$(
    TRACE_REVIEW_CURL_STUB_MODE="proxy" \
    TRACE_REVIEW_STUB_LOG="$stub_log" \
    LANGFUSE_HOST="http://remote.example:3000" \
    LANGFUSE_PUBLIC_KEY="pk-lf-test" \
    LANGFUSE_SECRET_KEY="sk-lf-test" \
    LANGFUSE_LOCAL_HOST="http://local.example:3000" \
    LANGFUSE_LOCAL_PUBLIC_KEY="pk-lf-local" \
    LANGFUSE_LOCAL_SECRET_KEY="sk-lf-local" \
    run_preflight "$temp_home" "$stub_dir" "$output_file" --backend-url http://127.0.0.1:8001 --source remote
  )"

  if [[ "$rc" -ne 20 ]]; then
    echo "Expected proxy-confusion preflight to exit 20, got $rc" >&2
    cat "$output_file" >&2
    exit 1
  fi

  assert_contains "Port 8001 listener: node/2222" "$output_file"
  assert_contains "does not look like TraceReview API" "$output_file"
  assert_contains "Symphony review proxy or another local service" "$output_file"
  assert_contains "TRACE_REVIEW_PREFLIGHT_RESULT exit_code=20" "$output_file"
}

test_healthy_preflight_reports_selected_source_and_ssh_tcp() {
  local temp_root temp_home stub_dir output_file stub_log key_file rc
  temp_root="$(mktemp -d)"
  temp_home="${temp_root}/home"
  stub_dir="${temp_root}/stubbin"
  output_file="${temp_root}/output.log"
  stub_log="${temp_root}/stub.log"
  key_file="${temp_root}/prod.key"
  mkdir -p "$temp_home"
  touch "$key_file"
  trap 'rm -rf "$temp_root"' RETURN

  make_stub_tools "$stub_dir"

  rc="$(
    TRACE_REVIEW_CURL_STUB_MODE="healthy" \
    TRACE_REVIEW_STUB_LOG="$stub_log" \
    TRACE_REVIEW_PRODUCTION_SSH_KEY_FILE="$key_file" \
    LANGFUSE_HOST="http://remote.example:3000" \
    LANGFUSE_PUBLIC_KEY="pk-lf-test" \
    LANGFUSE_SECRET_KEY="sk-lf-test" \
    LANGFUSE_LOCAL_HOST="http://local.example:3000" \
    LANGFUSE_LOCAL_PUBLIC_KEY="pk-lf-local" \
    LANGFUSE_LOCAL_SECRET_KEY="sk-lf-local" \
    run_preflight "$temp_home" "$stub_dir" "$output_file" --backend-url http://127.0.0.1:8901 --source local --ssh-host prod.example
  )"

  if [[ "$rc" -ne 0 ]]; then
    echo "Expected healthy preflight to exit 0, got $rc" >&2
    cat "$output_file" >&2
    exit 1
  fi

  assert_contains "Selected trace source: local" "$output_file"
  assert_contains "TraceReview backend health OK at http://127.0.0.1:8901/health" "$output_file"
  assert_contains "Langfuse local health OK" "$output_file"
  assert_contains "Production SSH TCP reachable at prod.example:22." "$output_file"
  assert_contains "TraceReview preflight passed with 0 warning" "$output_file"
  assert_contains "nc_args=-z -w 1 prod.example 22" "$stub_log"
}

test_rejects_invalid_source() {
  local temp_root temp_home stub_dir output_file stub_log rc
  temp_root="$(mktemp -d)"
  temp_home="${temp_root}/home"
  stub_dir="${temp_root}/stubbin"
  output_file="${temp_root}/output.log"
  stub_log="${temp_root}/stub.log"
  mkdir -p "$temp_home"
  trap 'rm -rf "$temp_root"' RETURN

  make_stub_tools "$stub_dir"

  rc="$(
    TRACE_REVIEW_CURL_STUB_MODE="healthy" \
    TRACE_REVIEW_STUB_LOG="$stub_log" \
    LANGFUSE_HOST="http://remote.example:3000" \
    LANGFUSE_PUBLIC_KEY="pk-lf-test" \
    LANGFUSE_SECRET_KEY="sk-lf-test" \
    LANGFUSE_LOCAL_HOST="http://local.example:3000" \
    LANGFUSE_LOCAL_PUBLIC_KEY="pk-lf-local" \
    LANGFUSE_LOCAL_SECRET_KEY="sk-lf-local" \
    run_preflight "$temp_home" "$stub_dir" "$output_file" --backend-url http://127.0.0.1:8901 --source stale
  )"

  if [[ "$rc" -ne 21 ]]; then
    echo "Expected invalid-source preflight to exit 21, got $rc" >&2
    cat "$output_file" >&2
    exit 1
  fi

  assert_contains "Unsupported trace source 'stale'. Expected 'remote' or 'local'." "$output_file"
  assert_contains "TRACE_REVIEW_PREFLIGHT_RESULT exit_code=21" "$output_file"
}

test_required_tool_failures_are_explicit
test_default_backend_url_uses_trace_review_host_port_only
test_default_backend_url_uses_canonical_trace_review_host_port
test_distinguishes_backend_down_and_missing_langfuse_config
test_detects_port_proxy_confusion
test_healthy_preflight_reports_selected_source_and_ssh_tcp
test_rejects_invalid_source

echo "trace_review_preflight tests passed"
