#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  symphony_ensure_workspace_runtime.sh [--workspace-dir DIR] [--refresh-managed]

Behavior:
  - Ensures required Symphony runtime helper files exist in a workspace.
  - Default mode is conservative: only copies when destination is missing.
  - `--refresh-managed` overwrites existing managed runtime files from the
    local source root so stale workspace helpers/compose files are updated.
  - Emits machine-parsable summary lines:
      SYNC_ENV_STATUS=ready|missing_required
      SYNC_ENV_COPIED=<n>
      SYNC_ENV_REFRESHED=<n>
      SYNC_ENV_SKIPPED_EXISTING=<n>
      SYNC_ENV_MISSING_REQUIRED=<comma-separated or none>
      SYNC_ENV_MISSING_OPTIONAL=<comma-separated or none>
USAGE
}

workspace_dir="${PWD}"
refresh_managed=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      workspace_dir="${2:-}"
      shift 2
      ;;
    --refresh-managed)
      refresh_managed=1
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

if [[ ! -d "${workspace_dir}" ]]; then
  echo "Workspace directory does not exist: ${workspace_dir}" >&2
  exit 2
fi

workspace_dir="$(cd "${workspace_dir}" && pwd -P)"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd "${script_dir}/../.." && pwd -P)"

hooks_source="${SYMPHONY_HOOKS_SOURCE:-${repo_root}/.git/hooks}"
local_dev_source="${SYMPHONY_LOCAL_DEV_SCRIPT_SOURCE:-${repo_root}/scripts}"
local_source_root="${SYMPHONY_LOCAL_SOURCE_ROOT:-${repo_root}}"

copied=0
refreshed=0
skipped_existing=0
missing_required=()
missing_optional=()

ensure_one() {
  local src="$1"
  local dest_rel="$2"
  local mode="$3"
  local required="$4"

  local dest="${workspace_dir}/${dest_rel}"

  if [[ ! -f "${src}" ]]; then
    if [[ "${required}" == "required" ]]; then
      missing_required+=("${dest_rel}")
    else
      missing_optional+=("${dest_rel}")
    fi
    return 0
  fi

  if [[ -e "${dest}" ]]; then
    if [[ "${refresh_managed}" -eq 1 ]]; then
      if cmp -s "${src}" "${dest}" && [[ "${mode}" != "0755" || -x "${dest}" ]]; then
        skipped_existing=$((skipped_existing + 1))
      else
        install -m "${mode}" "${src}" "${dest}"
        refreshed=$((refreshed + 1))
      fi
      return 0
    fi

    skipped_existing=$((skipped_existing + 1))
    if [[ "${mode}" == "0755" ]]; then
      chmod +x "${dest}" >/dev/null 2>&1 || true
    fi
    return 0
  fi

  mkdir -p "$(dirname "${dest}")"
  install -m "${mode}" "${src}" "${dest}"
  copied=$((copied + 1))
}

# Required files for core Symphony runtime lanes in this repo.
ensure_one "${hooks_source}/pre-commit" ".git/hooks/pre-commit" "0755" "required"
ensure_one "${hooks_source}/pre-push" ".git/hooks/pre-push" "0755" "required"
ensure_one "${local_source_root}/.symphony/WORKFLOW.md" ".symphony/WORKFLOW.md" "0644" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_pre_merge_cleanup.sh" "scripts/utilities/symphony_pre_merge_cleanup.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_prepare_docker_config.sh" "scripts/utilities/symphony_prepare_docker_config.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_guard_workspace_repo.sh" "scripts/utilities/symphony_guard_workspace_repo.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_human_review_prep.sh" "scripts/utilities/symphony_human_review_prep.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_ready_for_pr.sh" "scripts/utilities/symphony_ready_for_pr.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_request_claude_rereview.sh" "scripts/utilities/symphony_request_claude_rereview.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_wait_for_claude_review.sh" "scripts/utilities/symphony_wait_for_claude_review.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_claude_review_rounds.sh" "scripts/utilities/symphony_claude_review_rounds.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_local_db_tunnel_start.sh" "scripts/utilities/symphony_local_db_tunnel_start.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_local_db_tunnel_status.sh" "scripts/utilities/symphony_local_db_tunnel_status.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_local_db_tunnel_stop.sh" "scripts/utilities/symphony_local_db_tunnel_stop.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/utilities/symphony_microvm_worker_run.sh" "scripts/utilities/symphony_microvm_worker_run.sh" "0755" "required"
ensure_one "${local_source_root}/scripts/lib/local_db_tunnel_common.sh" "scripts/lib/local_db_tunnel_common.sh" "0644" "required"

# Helpful but not strictly required for every lane.
ensure_one "${local_source_root}/docker-compose.yml" "docker-compose.yml" "0644" "optional"
ensure_one "${local_source_root}/.symphony/with_github_pat.sh" ".symphony/with_github_pat.sh" "0755" "optional"
ensure_one "${local_source_root}/.symphony/github_pat_env.sh" ".symphony/github_pat_env.sh" "0644" "optional"
ensure_one "${local_source_root}/.symphony/configure_github_pat_git.sh" ".symphony/configure_github_pat_git.sh" "0755" "optional"
ensure_one "${local_source_root}/scripts/utilities/check_services.sh" "scripts/utilities/check_services.sh" "0755" "optional"
ensure_one "${local_source_root}/scripts/utilities/ensure_postgres_db_exists.sh" "scripts/utilities/ensure_postgres_db_exists.sh" "0755" "optional"

missing_required_joined="none"
if [[ ${#missing_required[@]} -gt 0 ]]; then
  missing_required_joined="$(IFS=,; echo "${missing_required[*]}")"
fi

missing_optional_joined="none"
if [[ ${#missing_optional[@]} -gt 0 ]]; then
  missing_optional_joined="$(IFS=,; echo "${missing_optional[*]}")"
fi

status="ready"
if [[ ${#missing_required[@]} -gt 0 ]]; then
  status="missing_required"
fi

echo "SYNC_ENV_STATUS=${status}"
echo "SYNC_ENV_COPIED=${copied}"
echo "SYNC_ENV_REFRESHED=${refreshed}"
echo "SYNC_ENV_SKIPPED_EXISTING=${skipped_existing}"
echo "SYNC_ENV_MISSING_REQUIRED=${missing_required_joined}"
echo "SYNC_ENV_MISSING_OPTIONAL=${missing_optional_joined}"

if [[ "${status}" == "ready" ]]; then
  exit 0
fi

exit 3
