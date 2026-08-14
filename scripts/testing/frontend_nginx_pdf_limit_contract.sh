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

assert_rendered_limit 524288000
assert_rendered_limit 629145600 --env PDF_MAX_FILE_SIZE_BYTES=629145600
assert_rejected_value malformed "must be a positive integer byte count"
assert_rejected_value 0 "must be greater than zero"
assert_nginx_variables_survive_rendering

echo "Frontend Nginx PDF limit contract tests passed"
