#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  symphony_guard_workspace_repo.sh --workspace-dir DIR --expected-repo URL [--expected-ref REF] [--source-root DIR]

Behavior:
  - Verifies the workspace Git origin matches the expected source repository.
  - If mismatched (or not a Git repo), reseeds the workspace from expected repo/ref.
  - Verifies required Symphony-managed runtime files can be materialized.
  - Emits machine-parsable summary lines:
      GUARD_REPO_STATUS=ok|reseeded|error
      GUARD_REPO_REASON=<match|missing_git|origin_mismatch|clone_failed|missing_expected_repo|invalid_workspace_path|runtime_missing_required|runtime_sync_failed|runtime_sync_missing|unknown>
      GUARD_REPO_EXPECTED=<normalized expected>
      GUARD_REPO_ACTUAL=<normalized actual or none>
USAGE
}

workspace_dir="${PWD}"
expected_repo="${SYMPHONY_SOURCE_REPO:-}"
expected_ref="${SYMPHONY_SOURCE_REF:-main}"
source_root="${SYMPHONY_LOCAL_SOURCE_ROOT:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      workspace_dir="${2:-}"
      shift 2
      ;;
    --expected-repo)
      expected_repo="${2:-}"
      shift 2
      ;;
    --expected-ref)
      expected_ref="${2:-main}"
      shift 2
      ;;
    --source-root)
      source_root="${2:-}"
      shift 2
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

status="error"
reason="unknown"
temp_files=()

cleanup_temp_files() {
  local temp_file=""
  for temp_file in "${temp_files[@]}"; do
    [[ -n "${temp_file}" ]] && rm -f "${temp_file}" >/dev/null 2>&1 || true
  done
}

trap cleanup_temp_files EXIT

normalize_repo() {
  local raw="$1"
  local normalized="${raw}"

  normalized="$(printf '%s' "${normalized}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  normalized="${normalized%.git}"
  normalized="${normalized%/}"
  normalized="${normalized#ssh://}"
  normalized="${normalized#https://}"
  normalized="${normalized#http://}"

  if [[ "${normalized}" =~ ^git@([^:]+):(.+)$ ]]; then
    normalized="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
  elif [[ "${normalized}" =~ ^git@([^/]+)/(.+)$ ]]; then
    normalized="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
  fi

  normalized="${normalized%.git}"
  normalized="${normalized%/}"
  printf '%s' "${normalized}"
}

print_summary() {
  local expected_norm="$1"
  local actual_norm="$2"
  echo "GUARD_REPO_STATUS=${status}"
  echo "GUARD_REPO_REASON=${reason}"
  echo "GUARD_REPO_EXPECTED=${expected_norm:-none}"
  echo "GUARD_REPO_ACTUAL=${actual_norm:-none}"
}

materialize_helper_from_origin_ref() {
  local repo_path="$1"
  local ref_name="$2"
  local file_path="$3"
  local helper_tmp=""
  local origin_object="refs/remotes/origin/${ref_name}:${file_path}"

  if ! git -C "${repo_path}" cat-file -e "${origin_object}" 2>/dev/null; then
    return 1
  fi

  helper_tmp="$(mktemp "${TMPDIR:-/tmp}/symphony-origin-helper.XXXXXX")"
  if ! git -C "${repo_path}" show "${origin_object}" > "${helper_tmp}" 2>/dev/null; then
    rm -f "${helper_tmp}" >/dev/null 2>&1 || true
    return 1
  fi

  chmod +x "${helper_tmp}" >/dev/null 2>&1 || true
  temp_files+=("${helper_tmp}")
  printf '%s\n' "${helper_tmp}"
}

run_runtime_sync() {
  local sync_mode="${1:-ensure}"
  local helper=""
  local helper_args=("--workspace-dir" "${workspace_dir}")

  if [[ "${sync_mode}" == "refresh" ]]; then
    helper_args+=("--refresh-managed")
  fi

  if helper="$(materialize_helper_from_origin_ref \
    "${workspace_dir}" \
    "${expected_ref}" \
    "scripts/utilities/symphony_ensure_workspace_runtime.sh" 2>/dev/null)"; then
    :
  elif [[ -f "${workspace_dir}/scripts/utilities/symphony_ensure_workspace_runtime.sh" ]]; then
    helper="${workspace_dir}/scripts/utilities/symphony_ensure_workspace_runtime.sh"
  elif [[ -n "${source_root}" && -f "${source_root}/scripts/utilities/symphony_ensure_workspace_runtime.sh" ]]; then
    helper="${source_root}/scripts/utilities/symphony_ensure_workspace_runtime.sh"
  fi

  if [[ -z "${helper}" ]]; then
    status="error"
    reason="runtime_sync_missing"
    echo "SYNC_ENV_STATUS=missing_required"
    echo "SYNC_ENV_MISSING_REQUIRED=scripts/utilities/symphony_ensure_workspace_runtime.sh"
    return 5
  fi

  local output rc
  set +e
  output="$(bash "${helper}" "${helper_args[@]}" 2>&1)"
  rc=$?
  set -e

  if [[ -n "${output}" ]]; then
    printf '%s\n' "${output}"
  fi

  if [[ "${rc}" -ne 0 ]]; then
    status="error"
    if [[ "${output}" == *"SYNC_ENV_STATUS=missing_required"* ]]; then
      reason="runtime_missing_required"
    else
      reason="runtime_sync_failed"
    fi
    return "${rc}"
  fi

  return 0
}

ensure_outside_workspace() {
  local cwd
  cwd="$(pwd -P 2>/dev/null || pwd)"

  case "${cwd}" in
    "${workspace_dir}"|${workspace_dir}/*)
      cd "$(dirname "${workspace_dir}")" || true
      ;;
  esac
}

if [[ -z "${expected_repo}" ]]; then
  reason="missing_expected_repo"
  print_summary "" ""
  exit 3
fi

if [[ -z "${workspace_dir}" ]]; then
  reason="invalid_workspace_path"
  print_summary "$(normalize_repo "${expected_repo}")" ""
  exit 3
fi

workspace_dir="$(cd "$(dirname "${workspace_dir}")" && pwd -P)/$(basename "${workspace_dir}")"
expected_repo_norm="$(normalize_repo "${expected_repo}")"

case "${workspace_dir}" in
  "${HOME}/.symphony/workspaces/"*)
    ;;
  *)
    reason="invalid_workspace_path"
    print_summary "${expected_repo_norm}" ""
    exit 3
    ;;
esac

current_origin=""
current_repo_norm=""

if [[ -d "${workspace_dir}/.git" ]]; then
  current_origin="$(git -C "${workspace_dir}" remote get-url origin 2>/dev/null || true)"
  if [[ -n "${current_origin}" ]]; then
    current_repo_norm="$(normalize_repo "${current_origin}")"
  fi
fi

if [[ -n "${current_repo_norm}" && "${current_repo_norm}" == "${expected_repo_norm}" ]]; then
  # Refresh the tracked main/ref outside the Codex turn sandbox so agents can
  # compare against origin/<ref> without needing to write FETCH_HEAD themselves.
  git -C "${workspace_dir}" fetch --quiet origin \
    "${expected_ref}:refs/remotes/origin/${expected_ref}" >/dev/null 2>&1 || true

  if ! run_runtime_sync refresh; then
    print_summary "${expected_repo_norm}" "${current_repo_norm}"
    exit 5
  fi

  status="ok"
  reason="match"
  print_summary "${expected_repo_norm}" "${current_repo_norm}"
  exit 0
fi

if [[ -z "${current_origin}" || ! -d "${workspace_dir}/.git" ]]; then
  reason="missing_git"
else
  reason="origin_mismatch"
fi

cleanup_helper=""
if [[ -n "${source_root}" && -f "${source_root}/scripts/utilities/symphony_pre_merge_cleanup.sh" ]]; then
  cleanup_helper="${source_root}/scripts/utilities/symphony_pre_merge_cleanup.sh"
elif [[ -f "${workspace_dir}/scripts/utilities/symphony_pre_merge_cleanup.sh" ]]; then
  cleanup_helper="${workspace_dir}/scripts/utilities/symphony_pre_merge_cleanup.sh"
fi

compose_project="$(basename "${workspace_dir}" | tr '[:upper:]' '[:lower:]')"
ensure_outside_workspace

if [[ -n "${cleanup_helper}" ]]; then
  bash "${cleanup_helper}" --workspace-dir "${workspace_dir}" --compose-project "${compose_project}" --remove-workspace --max-attempts 2 >/dev/null 2>&1 || true
else
  rm -rf "${workspace_dir}" >/dev/null 2>&1 || true
fi

if [[ -d "${workspace_dir}" ]]; then
  rm -rf "${workspace_dir}" >/dev/null 2>&1 || true
fi

mkdir -p "$(dirname "${workspace_dir}")"
ensure_outside_workspace

clone_output="$(git clone --depth 1 --branch "${expected_ref}" "${expected_repo}" "${workspace_dir}" 2>&1)" || {
  status="error"
  reason="clone_failed"
  printf '%s\n' "${clone_output}" | sed -n '/./{p;q;}' >&2
  print_summary "${expected_repo_norm}" "${current_repo_norm}"
  exit 4
}

if ! run_runtime_sync refresh; then
  print_summary "${expected_repo_norm}" "${current_repo_norm}"
  exit 5
fi

status="reseeded"
print_summary "${expected_repo_norm}" "${current_repo_norm}"
exit 0
