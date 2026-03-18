#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

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

assert_command_fails_with() {
  local pattern="$1"
  shift

  local temp_output
  temp_output="$(mktemp)"

  if "$@" >"${temp_output}" 2>&1; then
    echo "Expected command to fail: $*" >&2
    cat "${temp_output}" >&2
    rm -f "${temp_output}"
    exit 1
  fi

  assert_contains "$pattern" "${temp_output}"
  rm -f "${temp_output}"
}

assert_archive_has_entry() {
  local archive_path="$1"
  local entry_path="$2"
  local archive_listing
  archive_listing="$(tar -tzf "$archive_path")"

  if ! grep -qx "$entry_path" <<<"${archive_listing}"; then
    echo "Expected archive ${archive_path} to contain ${entry_path}" >&2
    printf '%s\n' "${archive_listing}" >&2
    exit 1
  fi
}

assert_archive_lacks_entry() {
  local archive_path="$1"
  local entry_path="$2"
  local archive_listing
  archive_listing="$(tar -tzf "$archive_path")"

  if grep -qx "$entry_path" <<<"${archive_listing}"; then
    echo "Did not expect archive ${archive_path} to contain ${entry_path}" >&2
    printf '%s\n' "${archive_listing}" >&2
    exit 1
  fi
}

make_sandbox_repo() {
  local sandbox_repo="$1"

  mkdir -p "${sandbox_repo}"
  tar --exclude=.git -C "${repo_root}" -cf - . | tar -C "${sandbox_repo}" -xf -
  chmod -R u+w "${sandbox_repo}"

  git -C "${sandbox_repo}" init -q
  git -C "${sandbox_repo}" config user.name "Codex"
  git -C "${sandbox_repo}" config user.email "codex@example.com"
  git -C "${sandbox_repo}" add -A
  git -C "${sandbox_repo}" commit --allow-empty -q -m "baseline for prepare_publish_artifacts regression"
}

test_release_lane_outputs_reproducible_assets() {
  local temp_dir
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN

  local sandbox_repo="${temp_dir}/repo"
  local first_dir="${temp_dir}/first"
  local second_dir="${temp_dir}/second"
  make_sandbox_repo "${sandbox_repo}"
  local helper_script="${sandbox_repo}/scripts/release/prepare_publish_artifacts.sh"

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
  assert_file_exists "${first_dir}/alliance-v9.9.9.tar.gz"
  assert_file_exists "${first_dir}/env.standalone-v9.9.9"
  assert_file_exists "${first_dir}/publish-artifacts-metadata-v9.9.9.json"

  assert_contains '^BACKEND_IMAGE_TAG=v9.9.9$' "${first_dir}/env.standalone-v9.9.9"
  assert_contains '^FRONTEND_IMAGE_TAG=v9.9.9$' "${first_dir}/env.standalone-v9.9.9"
  assert_contains '^TRACE_REVIEW_BACKEND_IMAGE_TAG=v9.9.9$' "${first_dir}/env.standalone-v9.9.9"
  assert_contains '"image_tag": "v9.9.9"' "${first_dir}/publish-artifacts-metadata-v9.9.9.json"
  assert_contains '"core_artifact": {' "${first_dir}/publish-artifacts-metadata-v9.9.9.json"
  assert_contains '"alliance_artifact": {' "${first_dir}/publish-artifacts-metadata-v9.9.9.json"
  assert_contains '"name": "core-v9.9.9.tar.gz"' "${first_dir}/publish-artifacts-metadata-v9.9.9.json"
  assert_contains '"name": "alliance-v9.9.9.tar.gz"' "${first_dir}/publish-artifacts-metadata-v9.9.9.json"

  assert_archive_has_entry "${first_dir}/core-v9.9.9.tar.gz" 'core/package.yaml'
  assert_archive_lacks_entry "${first_dir}/core-v9.9.9.tar.gz" 'alliance/package.yaml'
  assert_archive_has_entry "${first_dir}/alliance-v9.9.9.tar.gz" 'alliance/package.yaml'
  assert_archive_lacks_entry "${first_dir}/alliance-v9.9.9.tar.gz" 'core/package.yaml'

  local first_sha
  local second_sha
  first_sha="$(sha256sum "${first_dir}/core-v9.9.9.tar.gz" | awk '{print $1}')"
  second_sha="$(sha256sum "${second_dir}/core-v9.9.9.tar.gz" | awk '{print $1}')"
  assert_equals "$first_sha" "$second_sha"

  local first_alliance_sha
  local second_alliance_sha
  first_alliance_sha="$(sha256sum "${first_dir}/alliance-v9.9.9.tar.gz" | awk '{print $1}')"
  second_alliance_sha="$(sha256sum "${second_dir}/alliance-v9.9.9.tar.gz" | awk '{print $1}')"
  assert_equals "$first_alliance_sha" "$second_alliance_sha"
}

test_main_lane_outputs_sha_pinned_assets() {
  local temp_dir
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN

  local sandbox_repo="${temp_dir}/repo"
  make_sandbox_repo "${sandbox_repo}"
  local helper_script="${sandbox_repo}/scripts/release/prepare_publish_artifacts.sh"

  bash "$helper_script" \
    --lane main \
    --output-dir "$temp_dir" \
    --ref-name main \
    --short-sha abc1234 \
    --source-date-epoch 1700000000 >/dev/null

  assert_file_exists "${temp_dir}/core-main-sha-abc1234.tar.gz"
  assert_file_exists "${temp_dir}/alliance-main-sha-abc1234.tar.gz"
  assert_file_exists "${temp_dir}/env.standalone-main-sha-abc1234"
  assert_file_exists "${temp_dir}/publish-artifacts-metadata-main-sha-abc1234.json"

  assert_contains '^BACKEND_IMAGE_TAG=sha-abc1234$' "${temp_dir}/env.standalone-main-sha-abc1234"
  assert_contains '^FRONTEND_IMAGE_TAG=sha-abc1234$' "${temp_dir}/env.standalone-main-sha-abc1234"
  assert_contains '^TRACE_REVIEW_BACKEND_IMAGE_TAG=sha-abc1234$' "${temp_dir}/env.standalone-main-sha-abc1234"
  assert_contains '"image_tag": "sha-abc1234"' "${temp_dir}/publish-artifacts-metadata-main-sha-abc1234.json"
  assert_contains '"name": "core-main-sha-abc1234.tar.gz"' "${temp_dir}/publish-artifacts-metadata-main-sha-abc1234.json"
  assert_contains '"name": "alliance-main-sha-abc1234.tar.gz"' "${temp_dir}/publish-artifacts-metadata-main-sha-abc1234.json"
  assert_archive_has_entry "${temp_dir}/core-main-sha-abc1234.tar.gz" 'core/package.yaml'
  assert_archive_lacks_entry "${temp_dir}/core-main-sha-abc1234.tar.gz" 'alliance/package.yaml'
  assert_archive_has_entry "${temp_dir}/alliance-main-sha-abc1234.tar.gz" 'alliance/package.yaml'
  assert_archive_lacks_entry "${temp_dir}/alliance-main-sha-abc1234.tar.gz" 'core/package.yaml'
}

test_rejects_missing_required_flags() {
  local temp_dir
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN

  local sandbox_repo="${temp_dir}/repo"
  make_sandbox_repo "${sandbox_repo}"
  local helper_script="${sandbox_repo}/scripts/release/prepare_publish_artifacts.sh"

  assert_command_fails_with 'Missing required value for --lane' \
    bash "$helper_script" \
    --output-dir "${temp_dir}/missing-lane" \
    --ref-name main

  assert_command_fails_with 'Missing required value for --output-dir' \
    bash "$helper_script" \
    --lane main \
    --ref-name main \
    --short-sha abc1234
}

test_rejects_unsupported_lane() {
  local temp_dir
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN

  local sandbox_repo="${temp_dir}/repo"
  make_sandbox_repo "${sandbox_repo}"
  local helper_script="${sandbox_repo}/scripts/release/prepare_publish_artifacts.sh"

  assert_command_fails_with 'Unsupported lane: preview' \
    bash "$helper_script" \
    --lane preview \
    --output-dir "${temp_dir}/unsupported-lane" \
    --ref-name main \
    --short-sha abc1234
}

test_release_lane_outputs_reproducible_assets
test_main_lane_outputs_sha_pinned_assets
test_rejects_missing_required_flags
test_rejects_unsupported_lane

echo "prepare_publish_artifacts.sh checks passed"
