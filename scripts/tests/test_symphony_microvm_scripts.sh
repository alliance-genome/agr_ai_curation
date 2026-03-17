#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CREATE_SCRIPT="${REPO_ROOT}/scripts/utilities/symphony_microvm_worker_create.sh"
RUN_SCRIPT="${REPO_ROOT}/scripts/utilities/symphony_microvm_worker_run.sh"
DESTROY_SCRIPT="${REPO_ROOT}/scripts/utilities/symphony_microvm_worker_destroy.sh"
PREPARE_SCRIPT="${REPO_ROOT}/scripts/utilities/symphony_microvm_prepare_assets.sh"
APPLY_CHANGES_HELPER="${REPO_ROOT}/scripts/utilities/symphony_microvm_apply_guest_changes.py"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

test_prepare_assets_dry_run_outputs_resolved_urls() {
  local temp_dir output
  temp_dir="$(mktemp -d)"
  output="$(bash "${PREPARE_SCRIPT}" --assets-root "${temp_dir}" --dry-run)"
  assert_contains "MICROVM_ASSETS_STATUS=dry_run" "${output}"
  assert_contains "MICROVM_ASSETS_KERNEL_URL=https://s3.amazonaws.com/spec.ccfc.min/" "${output}"
  assert_contains "MICROVM_ASSETS_ROOTFS_URL=https://s3.amazonaws.com/spec.ccfc.min/" "${output}"
}

test_create_run_destroy_dry_run() {
  local temp_dir manifest output
  temp_dir="$(mktemp -d)"
  manifest="${temp_dir}/run.json"
  printf '{"run_id":"run-1","issue":{"identifier":"MT-FC-1"}}\n' > "${manifest}"

  output="$(bash "${CREATE_SCRIPT}" --worker-id MT-FC-1 --worker-dir "${temp_dir}/worker" --dry-run)"
  assert_contains "MICROVM_CREATE_STATUS=dry_run" "${output}"
  assert_contains "MICROVM_CREATE_TAP=fc" "${output}"
  assert_contains "MICROVM_CREATE_REVIEW_FRONTEND_URL=http://" "${output}"
  assert_contains "MICROVM_CREATE_REVIEW_BACKEND_URL=http://" "${output}"

  output="$(bash "${RUN_SCRIPT}" --worker-id MT-FC-1 --worker-dir "${temp_dir}/worker" --manifest "${manifest}" --result "${temp_dir}/result.json" --dry-run)"
  assert_contains "MICROVM_RUN_STATUS=dry_run" "${output}"
  assert_contains "MICROVM_RUN_GUEST_IP=172.19." "${output}"

  output="$(bash "${DESTROY_SCRIPT}" --worker-id MT-FC-1 --worker-dir "${temp_dir}/worker" --dry-run)"
  assert_contains "MICROVM_DESTROY_STATUS=dry_run" "${output}"
}

test_apply_guest_changes_rejects_symlink_entries() {
  local temp_dir source_root archive_path deleted_json status output
  temp_dir="$(mktemp -d)"
  source_root="${temp_dir}/source"
  archive_path="${temp_dir}/changes.tgz"
  deleted_json="${temp_dir}/deleted.json"
  mkdir -p "${source_root}"
  printf '[]\n' > "${deleted_json}"

  python3 - "${archive_path}" <<'PY'
import io
import pathlib
import tarfile
import sys

archive_path = pathlib.Path(sys.argv[1])
with tarfile.open(archive_path, "w:gz") as archive:
    info = tarfile.TarInfo("README-link")
    info.type = tarfile.SYMTYPE
    info.linkname = "/etc/passwd"
    archive.addfile(info)
PY

  set +e
  output="$(python3 "${APPLY_CHANGES_HELPER}" "${archive_path}" "${deleted_json}" "${source_root}" 2>&1)"
  status=$?
  set -e

  if [[ "${status}" -eq 0 ]]; then
    echo "Expected guest-change helper to reject symlink archive entries" >&2
    exit 1
  fi

  assert_contains "Refusing link entry from guest archive" "${output}"
}

test_apply_guest_changes_applies_files_and_deletions() {
  local temp_dir source_root archive_path deleted_json output
  temp_dir="$(mktemp -d)"
  source_root="${temp_dir}/source"
  archive_path="${temp_dir}/changes.tgz"
  deleted_json="${temp_dir}/deleted.json"
  mkdir -p "${source_root}/scripts"
  printf 'old\n' > "${source_root}/obsolete.txt"

  python3 - "${archive_path}" "${deleted_json}" <<'PY'
import io
import json
import pathlib
import tarfile
import sys

archive_path = pathlib.Path(sys.argv[1])
deleted_json = pathlib.Path(sys.argv[2])

payload = b"#!/usr/bin/env bash\necho updated\n"

with tarfile.open(archive_path, "w:gz") as archive:
    info = tarfile.TarInfo("scripts/test.sh")
    info.size = len(payload)
    info.mode = 0o755
    archive.addfile(info, io.BytesIO(payload))

with deleted_json.open("w", encoding="utf-8") as handle:
    json.dump(["obsolete.txt"], handle)
PY

  output="$(python3 "${APPLY_CHANGES_HELPER}" "${archive_path}" "${deleted_json}" "${source_root}")"

  if [[ -n "${output}" ]]; then
    echo "Expected no output from successful guest-change helper run" >&2
    exit 1
  fi

  assert_contains "#!/usr/bin/env bash" "$(cat "${source_root}/scripts/test.sh")"
  if [[ ! -x "${source_root}/scripts/test.sh" ]]; then
    echo "Expected applied file to preserve executable bit" >&2
    exit 1
  fi
  if [[ -e "${source_root}/obsolete.txt" ]]; then
    echo "Expected deleted file to be removed" >&2
    exit 1
  fi
}

test_prepare_assets_dry_run_outputs_resolved_urls
test_create_run_destroy_dry_run
test_apply_guest_changes_rejects_symlink_entries
test_apply_guest_changes_applies_files_and_deletions

echo "symphony_microvm script tests passed"
