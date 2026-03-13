#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
core_config_script="${repo_root}/scripts/install/02_core_config.sh"
auth_setup_script="${repo_root}/scripts/install/03_auth_setup.sh"
start_verify_script="${repo_root}/scripts/install/06_start_verify.sh"

export NO_COLOR=1

assert_contains() {
  local needle="$1"
  local file_path="$2"
  if ! grep -q "$needle" "$file_path"; then
    echo "Expected to find '$needle' in $file_path" >&2
    cat "$file_path" >&2
    exit 1
  fi
}

assert_not_exists() {
  local file_path="$1"
  if [[ -e "$file_path" ]]; then
    echo "Did not expect file to exist: $file_path" >&2
    exit 1
  fi
}

run_core_config() {
  local home_dir="$1"
  local input_text="$2"
  HOME="$home_dir" bash "$core_config_script" <<<"$input_text"
}

run_auth_setup() {
  local home_dir="$1"
  local input_text="$2"
  HOME="$home_dir" bash "$auth_setup_script" <<<"$input_text"
}

make_stub_tools() {
  local stub_dir="$1"

  # This stub includes Langfuse and PDFX endpoints because Stage 6 verifies them.
  cat >"${stub_dir}/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

state_dir="${INSTALL_TEST_STATE_DIR:?}"

if [[ " $* " == *" compose "* && " $* " == *" up "* && " $* " == *" -d "* ]]; then
  if [[ "$PWD" == *"pdfx-service"* ]]; then
    touch "${state_dir}/pdfx_up"
  else
    touch "${state_dir}/main_up"
  fi
  exit 0
fi

if [[ "${1:-}" == "compose" && "${2:-}" == "config" && "${3:-}" == "--services" ]]; then
  printf '%s\n' backend frontend langfuse postgres trace_review_backend
  exit 0
fi

if [[ "${1:-}" == "compose" && "${2:-}" == "ps" && "${3:-}" == "-q" ]]; then
  service="${4:-}"
  printf 'cid-%s\n' "$service"
  exit 0
fi

if [[ "${1:-}" == "inspect" && "${2:-}" == "-f" ]]; then
  printf '%s\n' "healthy"
  exit 0
fi

echo "unexpected docker args: $*" >&2
exit 2
EOF

  cat >"${stub_dir}/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

url="${@: -1}"
case "$url" in
  http://localhost:3002|http://localhost:8000/health|http://localhost:3000/api/public/health|http://localhost:8001/health|http://localhost:8501/api/v1/health)
    exit 0
    ;;
esac

echo "unexpected curl url: $url" >&2
exit 22
EOF

  chmod +x "${stub_dir}/docker" "${stub_dir}/curl"
}

run_start_verify() {
  local home_dir="$1"
  local stub_dir="$2"
  local state_dir="$3"
  local output_path="$4"
  local rc=0

  set +e
  HOME="$home_dir" \
  PATH="${stub_dir}:${PATH}" \
  INSTALL_DOCKER_CMD="docker" \
  INSTALL_CURL_CMD="curl" \
  INSTALL_START_VERIFY_TIMEOUT_SECONDS="1" \
  INSTALL_START_VERIFY_POLL_INTERVAL_SECONDS="1" \
  INSTALL_TEST_STATE_DIR="$state_dir" \
  bash "$start_verify_script" >"$output_path" 2>&1
  rc=$?
  set -e

  if [[ "$rc" -ne 0 ]]; then
    echo "Expected Stage 6 to succeed, got rc=$rc" >&2
    cat "$output_path" >&2
    exit 1
  fi
}

run_start_verify_expect_fail() {
  local home_dir="$1"
  local stub_dir="$2"
  local state_dir="$3"
  local output_path="$4"
  local rc=0

  set +e
  HOME="$home_dir" \
  PATH="${stub_dir}:${PATH}" \
  INSTALL_DOCKER_CMD="docker" \
  INSTALL_CURL_CMD="curl" \
  INSTALL_START_VERIFY_TIMEOUT_SECONDS="1" \
  INSTALL_START_VERIFY_POLL_INTERVAL_SECONDS="1" \
  INSTALL_TEST_STATE_DIR="$state_dir" \
  bash "$start_verify_script" >"$output_path" 2>&1
  rc=$?
  set -e

  if [[ "$rc" -eq 0 ]]; then
    echo "Expected Stage 6 to fail but it succeeded" >&2
    cat "$output_path" >&2
    exit 1
  fi
}

test_start_verify_with_pdfx_enabled() {
  local temp_home
  local stub_dir
  local state_dir
  local output_path
  local env_file
  local pdfx_state
  local clone_path
  temp_home="$(mktemp -d)"
  stub_dir="$(mktemp -d)"
  state_dir="$(mktemp -d)"
  output_path="$(mktemp)"
  trap 'rm -rf "$temp_home" "$stub_dir" "$state_dir" "$output_path"' RETURN

  run_core_config "$temp_home" $'sk-openai-test\n\n\n\n'
  run_auth_setup "$temp_home" $'2\nhttps://issuer.example.org/realms/alliance\nalliance-web\nsecret-value\nhttps://app.example.org/auth/callback\nrealm_access.roles\n'
  make_stub_tools "$stub_dir"

  env_file="${temp_home}/.agr_ai_curation/.env"
  pdfx_state="${temp_home}/.agr_ai_curation/.install_pdfx.env"
  clone_path="${temp_home}/pdfx-service"
  mkdir -p "${clone_path}/deploy"

  cat >"$pdfx_state" <<EOF
INSTALL_PDFX_CLONE_PATH=${clone_path}
INSTALL_PDFX_PORT=8501
EOF

  cat >>"$env_file" <<'EOF'
PDF_EXTRACTION_SERVICE_URL=http://localhost:8501
EOF

  run_start_verify "$temp_home" "$stub_dir" "$state_dir" "$output_path"

  [[ -f "${state_dir}/main_up" ]] || {
    echo "Expected main stack startup to be recorded" >&2
    cat "$output_path" >&2
    exit 1
  }
  [[ -f "${state_dir}/pdfx_up" ]] || {
    echo "Expected PDFX stack startup to be recorded" >&2
    cat "$output_path" >&2
    exit 1
  }

  assert_contains 'Stage 6: Start & Verify' "$output_path"
  assert_contains 'pdf_extraction' "$output_path"
  assert_contains '8501' "$output_path"
  assert_contains 'Application: http://localhost:3002' "$output_path"
  assert_contains 'API Docs: http://localhost:8000/docs' "$output_path"
  assert_contains 'Langfuse: http://localhost:3000' "$output_path"
  assert_contains 'Health: http://localhost:8000/health' "$output_path"
  assert_contains 'trace_review_backend' "$output_path"
  assert_contains '8001' "$output_path"
  assert_contains 'Auth mode: oidc' "$output_path"
  assert_contains 'deploy_alliance.sh is Alliance-internal only.' "$output_path"
}

test_start_verify_marks_pdfx_skipped_when_not_configured() {
  local temp_home
  local stub_dir
  local state_dir
  local output_path
  temp_home="$(mktemp -d)"
  stub_dir="$(mktemp -d)"
  state_dir="$(mktemp -d)"
  output_path="$(mktemp)"
  trap 'rm -rf "$temp_home" "$stub_dir" "$state_dir" "$output_path"' RETURN

  run_core_config "$temp_home" $'sk-openai-test\n\n\n\n'
  run_auth_setup "$temp_home" $'1\n'
  make_stub_tools "$stub_dir"

  run_start_verify "$temp_home" "$stub_dir" "$state_dir" "$output_path"

  [[ -f "${state_dir}/main_up" ]] || {
    echo "Expected main stack startup to be recorded" >&2
    cat "$output_path" >&2
    exit 1
  }
  assert_not_exists "${state_dir}/pdfx_up"

  assert_contains 'pdf_extraction' "$output_path"
  assert_contains 'Skipped' "$output_path"
  assert_contains 'Auth mode: dev' "$output_path"
}

test_start_verify_fails_with_clear_pdfx_state_guidance() {
  local temp_home
  local stub_dir
  local state_dir
  local output_path
  local env_file
  temp_home="$(mktemp -d)"
  stub_dir="$(mktemp -d)"
  state_dir="$(mktemp -d)"
  output_path="$(mktemp)"
  trap 'rm -rf "$temp_home" "$stub_dir" "$state_dir" "$output_path"' RETURN

  run_core_config "$temp_home" $'sk-openai-test\n\n\n\n'
  run_auth_setup "$temp_home" $'2\nhttps://issuer.example.org/realms/alliance\nalliance-web\nsecret-value\nhttps://app.example.org/auth/callback\nrealm_access.roles\n'
  make_stub_tools "$stub_dir"

  env_file="${temp_home}/.agr_ai_curation/.env"
  cat >>"$env_file" <<'EOF'
PDF_EXTRACTION_SERVICE_URL=http://localhost:8501
EOF

  run_start_verify_expect_fail "$temp_home" "$stub_dir" "$state_dir" "$output_path"

  assert_contains 'PDFX state file not found:' "$output_path"
  assert_contains 'Re-run Stage 5 without skipping PDF extraction setup to regenerate it.' "$output_path"
}

test_start_verify_with_pdfx_enabled
test_start_verify_marks_pdfx_skipped_when_not_configured
test_start_verify_fails_with_clear_pdfx_state_guidance

echo "start/verify installer stage checks passed"
