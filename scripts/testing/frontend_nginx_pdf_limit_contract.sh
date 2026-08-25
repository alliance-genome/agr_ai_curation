#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
image_tag="agr-ai-curation-frontend-nginx-contract:${GITHUB_RUN_ID:-local-$$}"

cleanup() {
  docker image rm -f "${image_tag}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build \
  --target nginx-runtime-base \
  --tag "${image_tag}" \
  "${repo_root}/frontend"

assert_rendered_limit() {
  local expected_bytes="$1"
  shift
  local rendered
  rendered="$(docker run --rm "$@" "${image_tag}" nginx -T 2>&1)"
  grep -Fq "client_max_body_size ${expected_bytes};" <<<"${rendered}"
}

assert_rejected_value() {
  local value="$1"
  local expected_message="$2"
  local output
  if output="$(
    docker run --rm \
      --env "PDF_MAX_FILE_SIZE_BYTES=${value}" \
      "${image_tag}" nginx -t 2>&1
  )"; then
    echo "Expected PDF_MAX_FILE_SIZE_BYTES=${value} to be rejected" >&2
    return 1
  fi
  grep -Fq "${expected_message}" <<<"${output}"
}

assert_nginx_variables_survive_rendering() {
  local rendered
  rendered="$(docker run --rm --env host=must-not-render "${image_tag}" nginx -T 2>&1)"
  # These assertions intentionally match literal Nginx variables after envsubst.
  # shellcheck disable=SC2016
  grep -Fq 'proxy_set_header Host $host;' <<<"${rendered}"
  # shellcheck disable=SC2016
  grep -Fq 'try_files $uri $uri/ /index.html;' <<<"${rendered}"
}

assert_runtime_config_override() {
  local expected_value='quoted "value" C:\path\'
  local runtime_config round_trip_value
  runtime_config="$(docker run --rm \
    --env VITE_CHAT_STREAM_RECOVERY_MAX_ATTEMPTS=7 \
    --env VITE_CHAT_STREAM_RECOVERY_DELAY_MS=2500 \
    --env VITE_DEV_MODE=true \
    --env FRONTEND_RUNTIME_CONFIG_KEYS='VITE_CHAT_STREAM_RECOVERY_MAX_ATTEMPTS VITE_CHAT_STREAM_RECOVERY_DELAY_MS VITE_REUSABLE_BOUNDARY_CHECK' \
    --env "VITE_REUSABLE_BOUNDARY_CHECK=${expected_value}" \
    --entrypoint /bin/sh \
    "${image_tag}" \
    -c '/docker-entrypoint.d/10-generate-runtime-config.sh && cat /usr/share/nginx/html/runtime-config.js')"
  grep -Fq '"VITE_CHAT_STREAM_RECOVERY_MAX_ATTEMPTS": "7"' <<<"${runtime_config}"
  grep -Fq '"VITE_CHAT_STREAM_RECOVERY_DELAY_MS": "2500"' <<<"${runtime_config}"
  ! grep -Fq 'VITE_DEV_MODE' <<<"${runtime_config}"
  grep -Fq 'window.__APP_RUNTIME_CONFIG__ = Object.freeze({' <<<"${runtime_config}"
  node --check <<<"${runtime_config}"
  round_trip_value="$(node -e '
    global.window = {};
    eval(require("fs").readFileSync(0, "utf8"));
    process.stdout.write(window.__APP_RUNTIME_CONFIG__.VITE_REUSABLE_BOUNDARY_CHECK);
  ' <<<"${runtime_config}")"
  [[ "${round_trip_value}" == "${expected_value}" ]]
}

assert_baked_runtime_config_allowlist() {
  local runtime_config
  runtime_config="$(docker run --rm \
    --env VITE_DEV_MODE=true \
    --entrypoint /bin/sh \
    "${image_tag}" \
    -c '/docker-entrypoint.d/10-generate-runtime-config.sh && cat /usr/share/nginx/html/runtime-config.js')"
  grep -Fq '"VITE_CHAT_STREAM_RECOVERY_MAX_ATTEMPTS": "3"' <<<"${runtime_config}"
  grep -Fq '"VITE_CHAT_STREAM_RECOVERY_DELAY_MS": "1000"' <<<"${runtime_config}"
  ! grep -Fq 'VITE_DEV_MODE' <<<"${runtime_config}"
}

assert_runtime_config_rejected() {
  local allowlist="$1"
  local expected_message="$2"
  local output
  if output="$(docker run --rm \
    --env "FRONTEND_RUNTIME_CONFIG_KEYS=${allowlist}" \
    --entrypoint /bin/sh \
    "${image_tag}" \
    -c '/docker-entrypoint.d/10-generate-runtime-config.sh' 2>&1)"; then
    echo "Expected frontend runtime configuration to be rejected" >&2
    return 1
  fi
  grep -Fq "${expected_message}" <<<"${output}"
}

assert_runtime_config_no_store() {
  local rendered runtime_location
  rendered="$(docker run --rm "${image_tag}" nginx -T 2>&1)"
  runtime_location="$(awk '
    /location = \/runtime-config\.js \{/ { found = 1 }
    found { print }
    found && /^    }$/ { exit }
  ' <<<"${rendered}")"
  grep -Fq 'location = /runtime-config.js {' <<<"${runtime_location}"
  grep -Fq 'add_header Cache-Control "no-store" always;' <<<"${runtime_location}"
  grep -Fq 'add_header X-Frame-Options "SAMEORIGIN" always;' <<<"${runtime_location}"
  grep -Fq 'add_header X-XSS-Protection "1; mode=block" always;' <<<"${runtime_location}"
  grep -Fq 'add_header X-Content-Type-Options "nosniff" always;' <<<"${runtime_location}"
  grep -Fq 'add_header Referrer-Policy "no-referrer-when-downgrade" always;' <<<"${runtime_location}"
}

assert_rendered_limit 524288000
assert_rendered_limit 629145600 --env PDF_MAX_FILE_SIZE_BYTES=629145600
assert_rendered_limit 2147483647 --env PDF_MAX_FILE_SIZE_BYTES=2147483647
assert_rendered_limit 0000000005 --env PDF_MAX_FILE_SIZE_BYTES=0000000005
assert_rejected_value malformed "must be a positive integer byte count"
assert_rejected_value 0 "must be greater than zero"
assert_rejected_value 2147483648 "must not exceed the persisted file-size capacity of 2147483647 bytes"
assert_rejected_value 999999999999999999999999999999999999 "must not exceed the persisted file-size capacity of 2147483647 bytes"
assert_nginx_variables_survive_rendering
assert_runtime_config_override
assert_baked_runtime_config_allowlist
assert_runtime_config_rejected '' 'frontend runtime config allowlist is required'
assert_runtime_config_rejected 'VITE_MISSING' 'Missing frontend runtime configuration value: VITE_MISSING'
assert_runtime_config_no_store

echo "Frontend Nginx runtime contract tests passed"
