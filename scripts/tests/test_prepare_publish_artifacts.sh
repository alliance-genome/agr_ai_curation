#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
helper_script="${repo_root}/scripts/release/prepare_publish_artifacts.sh"

assert_contains() {
  local pattern="$1"
  local file_path="$2"

  if ! grep -Eq "$pattern" "$file_path"; then
    echo "Expected to find pattern '$pattern' in $file_path" >&2
    cat "$file_path" >&2
    exit 1
  fi
}

assert_file_exists() {
  local file_path="$1"

  if [[ ! -f "$file_path" ]]; then
    echo "Expected file to exist: $file_path" >&2
    exit 1
  fi
}

assert_equals() {
  local expected="$1"
  local actual="$2"

  if [[ "$expected" != "$actual" ]]; then
    echo "Expected '$expected', got '$actual'" >&2
    exit 1
  fi
}

test_release_lane_outputs_reproducible_assets() {
  local temp_dir
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN

  local first_dir="${temp_dir}/first"
  local second_dir="${temp_dir}/second"

  bash "$helper_script" \
    --lane release \
    --output-dir "$first_dir" \
    --ref-name v9.9.9 \
    --short-sha deadbee \
    --source-date-epoch 1700000000 >/dev/null

  bash "$helper_script" \
    --lane release \
    --output-dir "$second_dir" \
    --ref-name v9.9.9 \
    --short-sha deadbee \
    --source-date-epoch 1700000000 >/dev/null

  assert_file_exists "${first_dir}/core-v9.9.9.tar.gz"
  assert_file_exists "${first_dir}/env.standalone-v9.9.9"
  assert_file_exists "${first_dir}/publish-artifacts-metadata-v9.9.9.json"

  assert_contains '^BACKEND_IMAGE_TAG=v9.9.9$' "${first_dir}/env.standalone-v9.9.9"
  assert_contains '^FRONTEND_IMAGE_TAG=v9.9.9$' "${first_dir}/env.standalone-v9.9.9"
  assert_contains '^TRACE_REVIEW_BACKEND_IMAGE_TAG=v9.9.9$' "${first_dir}/env.standalone-v9.9.9"
  assert_contains '"image_tag": "v9.9.9"' "${first_dir}/publish-artifacts-metadata-v9.9.9.json"

  local archive_listing
  archive_listing="$(tar -tzf "${first_dir}/core-v9.9.9.tar.gz")"
  if ! grep -qx 'core/package.yaml' <<<"${archive_listing}"; then
    echo "Expected bundled core artifact to contain core/package.yaml" >&2
    exit 1
  fi

  local first_sha
  local second_sha
  first_sha="$(sha256sum "${first_dir}/core-v9.9.9.tar.gz" | awk '{print $1}')"
  second_sha="$(sha256sum "${second_dir}/core-v9.9.9.tar.gz" | awk '{print $1}')"
  assert_equals "$first_sha" "$second_sha"
}

test_main_lane_outputs_sha_pinned_assets() {
  local temp_dir
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN

  bash "$helper_script" \
    --lane main \
    --output-dir "$temp_dir" \
    --ref-name main \
    --short-sha abc1234 \
    --source-date-epoch 1700000000 >/dev/null

  assert_file_exists "${temp_dir}/core-main-sha-abc1234.tar.gz"
  assert_file_exists "${temp_dir}/env.standalone-main-sha-abc1234"
  assert_file_exists "${temp_dir}/publish-artifacts-metadata-main-sha-abc1234.json"

  assert_contains '^BACKEND_IMAGE_TAG=sha-abc1234$' "${temp_dir}/env.standalone-main-sha-abc1234"
  assert_contains '^FRONTEND_IMAGE_TAG=sha-abc1234$' "${temp_dir}/env.standalone-main-sha-abc1234"
  assert_contains '^TRACE_REVIEW_BACKEND_IMAGE_TAG=sha-abc1234$' "${temp_dir}/env.standalone-main-sha-abc1234"
  assert_contains '"image_tag": "sha-abc1234"' "${temp_dir}/publish-artifacts-metadata-main-sha-abc1234.json"
}

test_release_lane_outputs_reproducible_assets
test_main_lane_outputs_sha_pinned_assets

echo "prepare_publish_artifacts.sh checks passed"
