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

assert_not_contains() {
  local needle="$1"
  local file_path="$2"
  if grep -q "$needle" "$file_path"; then
    echo "Did not expect to find '$needle' in $file_path" >&2
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
  printf '\n%s' "$input_text" | HOME="$home_dir" INSTALL_IMAGE_TAG="sha-test-release" bash "$core_config_script"
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
    if [[ " $* " == *"docker-compose.production.yml"* ]]; then
      touch "${state_dir}/main_compose_is_production"
    fi
  fi
  exit 0
fi

if [[ " $* " == *" compose "* && " $* " == *" config "* && " $* " == *" --services "* ]]; then
  if [[ " $* " == *"docker-compose.production.yml"* ]]; then
    touch "${state_dir}/main_compose_is_production"
  fi
  printf '%s\n' backend frontend langfuse postgres trace_review_backend
  exit 0
fi

if [[ " $* " == *" compose "* && " $* " == *" config "* && " $* " == *" --format json "* ]]; then
  cat <<'JSON'
{"services":{"backend":{"image":"example/backend:sha-test-release","environment":{"AUTH_PROVIDER":"oidc","OIDC_ISSUER_URL":"https://issuer.example.org","OIDC_CLIENT_ID":"curation-production","OIDC_REDIRECT_URI":"https://curation.example.org/auth/callback","DEBUG":"false","DEV_MODE":"false","HEALTH_CHECK_REQUIRE_EXTERNAL_VALIDATION_DEPS":"true","HEALTH_CHECK_REQUIRE_LITERATURE_DB":"true","HEALTH_CHECK_STRICT_MODE":"true","SECURE_COOKIES":"true","SENTRY_AI_CONTENT_PREVIEW_MAX_CHARS":"2000","SENTRY_TRANSACTION_RETAINED_SPANS_MAX":"50"}},"frontend":{"image":"example/frontend:sha-test-release","environment":{"VITE_DEV_MODE":"false"}},"trace_review_backend":{"image":"example/trace-review:sha-test-release","environment":{"DEV_MODE":"false","SECURE_COOKIES":"true"}},"weaviate":{"image":"example/weaviate@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","environment":{"AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED":"false","AUTHENTICATION_APIKEY_ENABLED":"true","AUTHENTICATION_APIKEY_ALLOWED_KEYS":"test-key","AUTHORIZATION_ADMINLIST_USERS":"curation-backend"}},"postgres":{"image":"example/postgres@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"redis":{"image":"example/redis@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"clickhouse":{"image":"example/clickhouse@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"minio":{"image":"example/minio@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"loki":{"image":"example/loki@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"promtail":{"image":"example/promtail@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"langfuse":{"image":"example/langfuse@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"langfuse-worker":{"image":"example/langfuse-worker@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}}
JSON
  exit 0
fi

if [[ " $* " == *" compose "* && " $* " == *" ps "* && " $* " == *" -q "* ]]; then
  service="${*: -1}"
  if [[ " $* " == *"docker-compose.production.yml"* ]]; then
    touch "${state_dir}/main_compose_is_production"
  fi
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

  run_core_config "$temp_home" $'sk-openai-test\n\n\n\n\n\n'
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
  [[ -f "${state_dir}/main_compose_is_production" ]] || {
    echo "Expected Stage 6 to use docker-compose.production.yml" >&2
    cat "$output_path" >&2
    exit 1
  }
  [[ -f "${state_dir}/pdfx_up" ]] || {
    echo "Expected PDFX stack startup to be recorded" >&2
    cat "$output_path" >&2
    exit 1
  }

  assert_contains 'Stage 6 of 6: Start & Verify' "$output_path"
  assert_contains 'Compose file:' "$output_path"
  assert_contains "${repo_root}/docker-compose.production.yml" "$output_path"
  assert_contains "${temp_home}/.agr_ai_curation/runtime/config" "$output_path"
  assert_contains "${temp_home}/.agr_ai_curation/runtime/packages" "$output_path"
  assert_contains "${temp_home}/.agr_ai_curation/runtime/state" "$output_path"
  assert_contains 'pdf_extraction' "$output_path"
  assert_contains '8501' "$output_path"
  assert_contains 'Application: http://localhost:3002' "$output_path"
  assert_contains 'API Docs: http://localhost:8000/docs' "$output_path"
  assert_contains 'Langfuse: http://localhost:3000' "$output_path"
  assert_contains 'TraceReview API: http://localhost:8001' "$output_path"
  assert_contains 'TraceReview health: http://localhost:8001/health' "$output_path"
  assert_contains 'Health: http://localhost:8000/health' "$output_path"
  assert_contains 'Restart command: docker compose --env-file' "$output_path"
  assert_contains 'trace_review_backend' "$output_path"
  assert_contains '8001' "$output_path"
  assert_contains 'Auth mode: oidc' "$output_path"
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

  run_core_config "$temp_home" $'sk-openai-test\n\n\n\n\n\n'
  run_auth_setup "$temp_home" $'2\nhttps://issuer.example.org/realms/alliance\nalliance-web\nsecret-value\nhttps://app.example.org/auth/callback\nrealm_access.roles\n'
  make_stub_tools "$stub_dir"

  run_start_verify "$temp_home" "$stub_dir" "$state_dir" "$output_path"

  [[ -f "${state_dir}/main_up" ]] || {
    echo "Expected main stack startup to be recorded" >&2
    cat "$output_path" >&2
    exit 1
  }
  [[ -f "${state_dir}/main_compose_is_production" ]] || {
    echo "Expected Stage 6 to use docker-compose.production.yml" >&2
    cat "$output_path" >&2
    exit 1
  }
  assert_not_exists "${state_dir}/pdfx_up"

  assert_contains "${temp_home}/.agr_ai_curation/runtime/config" "$output_path"
  assert_contains 'pdf_extraction' "$output_path"
  assert_contains 'Skipped' "$output_path"
  assert_contains 'Auth mode: oidc' "$output_path"
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

  run_core_config "$temp_home" $'sk-openai-test\n\n\n\n\n\n'
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

test_start_verify_fails_with_runtime_layout_guidance() {
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

  run_core_config "$temp_home" $'sk-openai-test\n\n\n\n\n\n'
  run_auth_setup "$temp_home" $'1\n'
  make_stub_tools "$stub_dir"

  env_file="${temp_home}/.agr_ai_curation/.env"
  python3 - "$env_file" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
updated = []
for line in env_path.read_text().splitlines():
    if line.startswith("AGR_RUNTIME_PACKAGES_HOST_DIR="):
        updated.append("AGR_RUNTIME_PACKAGES_HOST_DIR=")
    else:
        updated.append(line)
env_path.write_text("\n".join(updated) + "\n")
PY

  run_start_verify_expect_fail "$temp_home" "$stub_dir" "$state_dir" "$output_path"

  assert_contains 'AGR_RUNTIME_PACKAGES_HOST_DIR must not be empty.' "$output_path"
  assert_contains 'Re-run Stage 2: Core Configuration to regenerate the installed runtime layout.' "$output_path"
  assert_not_contains 'Required directory not found:' "$output_path"
}

test_start_verify_with_pdfx_enabled
test_start_verify_marks_pdfx_skipped_when_not_configured
test_start_verify_fails_with_clear_pdfx_state_guidance
test_start_verify_fails_with_runtime_layout_guidance

echo "start/verify installer stage checks passed"
