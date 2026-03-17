#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
source "${REPO_ROOT}/scripts/lib/symphony_microvm_common.sh"
APPLY_CHANGES_HELPER="${SCRIPT_DIR}/symphony_microvm_apply_guest_changes.py"

usage() {
  cat <<'EOF'
Usage:
  symphony_microvm_worker_run.sh [--worker-dir DIR] [--worker-id ID] [--manifest PATH] [--result PATH] [--dry-run]

Behavior:
  - Copies the run manifest into the guest over SSH.
  - Installs or updates the guest runner helper.
  - Executes the guest runner and copies the result back to the host.
EOF
}

worker_id="${SYMPHONY_WORKER_ID:-${SYMPHONY_ISSUE_IDENTIFIER:-issue}}"
worker_dir="${SYMPHONY_WORKER_DIR:-$PWD}"
manifest_path="${SYMPHONY_RUN_MANIFEST_PATH:-}"
result_path="${SYMPHONY_RUN_RESULT_PATH:-}"
assets_root="$(symphony_microvm_assets_root)"
dry_run=0
source_root="${SYMPHONY_LOCAL_SOURCE_ROOT:-$(symphony_microvm_repo_root)}"
source_ref="${SYMPHONY_SOURCE_REF:-main}"
source_remote="${SYMPHONY_SOURCE_REMOTE:-origin}"
source_fetch_remote="${SYMPHONY_SOURCE_FETCH_REMOTE:-1}"
host_branch=""
host_head_sha=""
origin_branch_sha=""
origin_main_sha=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --worker-id)
      worker_id="${2:-}"
      shift 2
      ;;
    --worker-dir)
      worker_dir="${2:-}"
      shift 2
      ;;
    --manifest)
      manifest_path="${2:-}"
      shift 2
      ;;
    --result)
      result_path="${2:-}"
      shift 2
      ;;
    --assets-root)
      assets_root="${2:-}"
      shift 2
      ;;
    --source-root)
      source_root="${2:-}"
      shift 2
      ;;
    --source-ref)
      source_ref="${2:-}"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "${manifest_path}" || -z "${result_path}" ]]; then
  echo "Missing manifest/result path" >&2
  usage >&2
  exit 2
fi

if [[ ! -f "${manifest_path}" ]]; then
  echo "Manifest file does not exist: ${manifest_path}" >&2
  exit 2
fi

ssh_key_path="${assets_root}/ssh/id_rsa"
network_json="$(symphony_microvm_network_json "${worker_id}")"
guest_ip="$(symphony_microvm_json_get "${network_json}" guest_ip)"
run_id="${SYMPHONY_RUN_ID:-$(basename "${manifest_path}" .json)}"
guest_manifest="/root/symphony/runs/${run_id}.json"
guest_result="/root/symphony/runs/${run_id}.result.json"
guest_changes="${guest_result%.json}.changes.tgz"
guest_deleted="${guest_result%.json}.deleted.json"
guest_runner="/usr/local/bin/symphony_microvm_guest_run.sh"
guest_bundle="/root/symphony/bundles/${run_id}.bundle"
guest_snapshot="/root/symphony/bundles/${run_id}.workspace.tgz"
guest_snapshot_manifest="/root/symphony/bundles/${run_id}.workspace-files.txt"
guest_codex_dir="/root/symphony/runs/${run_id}.codex"
host_bundle="${worker_dir}/${run_id}.bundle"
host_snapshot="${worker_dir}/${run_id}.workspace.tgz"
host_snapshot_manifest="${worker_dir}/${run_id}.workspace-files.txt"
host_changes="${result_path%.json}.changes.tgz"
host_deleted="${result_path%.json}.deleted.json"
host_codex_config="${HOME}/.codex/config.toml"
host_codex_auth="${HOME}/.codex/auth.json"
apply_changes_to_host="${SYMPHONY_MICROVM_APPLY_CHANGES_TO_HOST:-0}"

cleanup_worker_run() {
  if [[ -n "${guest_ip:-}" ]]; then
    ssh ${ssh_opts:-} "root@${guest_ip}" "rm -rf ${guest_codex_dir}" >/dev/null 2>&1 || true
  fi
  rm -f "${host_bundle}" "${host_snapshot}" "${host_snapshot_manifest}"
}

trap cleanup_worker_run EXIT

if [[ "${dry_run}" -eq 1 ]]; then
  symphony_microvm_output_kv "MICROVM_RUN_STATUS" "dry_run"
  symphony_microvm_output_kv "MICROVM_RUN_WORKER_ID" "${worker_id}"
  symphony_microvm_output_kv "MICROVM_RUN_GUEST_IP" "${guest_ip}"
  symphony_microvm_output_kv "MICROVM_RUN_MANIFEST" "${manifest_path}"
  symphony_microvm_output_kv "MICROVM_RUN_SOURCE_ROOT" "${source_root}"
  symphony_microvm_output_kv "MICROVM_RUN_SOURCE_REF" "${source_ref}"
  symphony_microvm_output_kv "MICROVM_RUN_SOURCE_REMOTE" "${source_remote}"
  exit 0
fi

ssh_opts="$(symphony_microvm_ssh_opts "${ssh_key_path}")"

rm -f "${host_bundle}" "${host_snapshot}" "${host_snapshot_manifest}"

if git -C "${source_root}" rev-parse --git-dir >/dev/null 2>&1 && [[ "${source_fetch_remote}" != "0" && "${source_fetch_remote}" != "false" ]]; then
  if git -C "${source_root}" remote get-url "${source_remote}" >/dev/null 2>&1; then
    git -C "${source_root}" fetch --prune "${source_remote}" >/dev/null 2>&1 || true
  fi
fi

if git -C "${source_root}" rev-parse --git-dir >/dev/null 2>&1; then
  host_branch="$(git -C "${source_root}" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  host_head_sha="$(git -C "${source_root}" rev-parse HEAD 2>/dev/null || true)"

  if [[ -n "${host_branch}" && "${host_branch}" != "HEAD" ]]; then
    if [[ "${host_branch}" != "main" ]]; then
      source_ref="${host_branch}"
    fi
    origin_branch_sha="$(git -C "${source_root}" rev-parse "refs/remotes/${source_remote}/${host_branch}" 2>/dev/null || true)"
  fi

  origin_main_sha="$(git -C "${source_root}" rev-parse "refs/remotes/${source_remote}/main" 2>/dev/null || true)"
fi

bundle_ref="${source_ref}"
if [[ "${host_branch}" == "main" ]] && git -C "${source_root}" rev-parse --verify "refs/remotes/${source_remote}/main" >/dev/null 2>&1; then
  bundle_ref="refs/remotes/${source_remote}/main"
elif git -C "${source_root}" rev-parse --verify HEAD >/dev/null 2>&1; then
  bundle_ref="HEAD"
elif ! git -C "${source_root}" rev-parse --verify "${bundle_ref}" >/dev/null 2>&1; then
  if git -C "${source_root}" rev-parse --verify "refs/remotes/${source_remote}/${source_ref}" >/dev/null 2>&1; then
    bundle_ref="refs/remotes/${source_remote}/${source_ref}"
  else
    bundle_ref="HEAD"
  fi
fi

git -C "${source_root}" bundle create "${host_bundle}" "${bundle_ref}" >/dev/null 2>&1 || true
(
  cd "${source_root}"

  git rev-parse --git-dir >/dev/null 2>&1
  git ls-files -z --cached --others --exclude-standard > "${host_snapshot_manifest}"

  python3 - "${host_snapshot_manifest}" <<'PY'
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
entries = [entry for entry in manifest_path.read_bytes().split(b"\0") if entry]
filtered = []

for entry in entries:
    path = entry.decode("utf-8", "surrogateescape")
    name = path.rsplit("/", 1)[-1]

    if name == ".env":
        continue
    if name.startswith(".env."):
        continue
    if name == "CLAUDE.local.md":
        continue
    if name.startswith("PROMPT_IMPROVEMENT_PLAN") and name.endswith(".md"):
        continue

    filtered.append(entry)

manifest_path.write_bytes(b"\0".join(filtered) + (b"\0" if filtered else b""))
PY

  if [[ -s "${host_snapshot_manifest}" ]]; then
    tar --null -T "${host_snapshot_manifest}" -czf "${host_snapshot}"
  else
    tar -czf "${host_snapshot}" --files-from /dev/null
  fi
)

ssh ${ssh_opts} "root@${guest_ip}" "mkdir -p /root/symphony/runs /root/symphony/bundles /usr/local/bin ${guest_codex_dir}"
scp ${ssh_opts} "${manifest_path}" "root@${guest_ip}:${guest_manifest}" >/dev/null
if [[ -f "${host_bundle}" ]]; then
  scp ${ssh_opts} "${host_bundle}" "root@${guest_ip}:${guest_bundle}" >/dev/null
fi
scp ${ssh_opts} "${host_snapshot}" "root@${guest_ip}:${guest_snapshot}" >/dev/null
scp ${ssh_opts} "${host_snapshot_manifest}" "root@${guest_ip}:${guest_snapshot_manifest}" >/dev/null
scp ${ssh_opts} "${SCRIPT_DIR}/symphony_microvm_guest_run.sh" "root@${guest_ip}:${guest_runner}" >/dev/null
if [[ -f "${host_codex_auth}" ]]; then
  scp ${ssh_opts} "${host_codex_auth}" "root@${guest_ip}:${guest_codex_dir}/auth.json" >/dev/null
fi
if [[ -f "${host_codex_config}" ]]; then
  scp ${ssh_opts} "${host_codex_config}" "root@${guest_ip}:${guest_codex_dir}/config.toml" >/dev/null
fi
ssh ${ssh_opts} "root@${guest_ip}" "chmod +x ${guest_runner}"

set +e
ssh \
  ${ssh_opts} \
  "root@${guest_ip}" \
  "SYMPHONY_HOST_BRANCH='${host_branch}' SYMPHONY_HOST_HEAD_SHA='${host_head_sha}' SYMPHONY_ORIGIN_BRANCH_SHA='${origin_branch_sha}' SYMPHONY_ORIGIN_MAIN_SHA='${origin_main_sha}' ${guest_runner} ${guest_manifest} ${guest_result} ${guest_bundle} ${source_ref} ${guest_snapshot} ${guest_snapshot_manifest} ${guest_codex_dir}"
guest_rc=$?
set -e

scp ${ssh_opts} "root@${guest_ip}:${guest_result}" "${result_path}" >/dev/null 2>&1 || true
scp ${ssh_opts} "root@${guest_ip}:${guest_changes}" "${host_changes}" >/dev/null 2>&1 || true
scp ${ssh_opts} "root@${guest_ip}:${guest_deleted}" "${host_deleted}" >/dev/null 2>&1 || true

if [[ "${guest_rc}" -ne 0 ]]; then
  symphony_microvm_output_kv "MICROVM_RUN_STATUS" "guest_failed"
  symphony_microvm_output_kv "MICROVM_RUN_WORKER_ID" "${worker_id}"
  symphony_microvm_output_kv "MICROVM_RUN_GUEST_IP" "${guest_ip}"
  symphony_microvm_output_kv "MICROVM_RUN_EXIT_CODE" "${guest_rc}"
  exit "${guest_rc}"
fi

if [[ "${apply_changes_to_host}" == "1" || "${apply_changes_to_host}" == "true" ]]; then
  python3 "${APPLY_CHANGES_HELPER}" "${host_changes}" "${host_deleted}" "${source_root}"
fi

symphony_microvm_output_kv "MICROVM_RUN_STATUS" "completed"
symphony_microvm_output_kv "MICROVM_RUN_WORKER_ID" "${worker_id}"
symphony_microvm_output_kv "MICROVM_RUN_GUEST_IP" "${guest_ip}"
symphony_microvm_output_kv "MICROVM_RUN_RESULT" "${result_path}"
symphony_microvm_output_kv "MICROVM_RUN_SYNCED_BACK" "${apply_changes_to_host}"
trap - EXIT
cleanup_worker_run
