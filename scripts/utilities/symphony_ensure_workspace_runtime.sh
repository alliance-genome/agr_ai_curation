#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  symphony_ensure_workspace_runtime.sh [--workspace-dir DIR] [--refresh-managed]

Behavior:
  - Ensures required Symphony runtime helper files exist in a workspace.
  - Default mode is conservative: only copies runtime overlay files when the
    workspace copy is missing, and verifies required Git-owned repo files are
    present in the workspace checkout.
  - `--refresh-managed` overwrites only managed runtime overlay files from the
    local source root so stale workspace workflow/PAT helpers are updated.
  - Tracked repo files are never overwritten from the local source root.
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

resolve_git_common_dir() {
  local repo_path="$1"
  local git_common_dir=""

  if ! git_common_dir="$(git -C "${repo_path}" rev-parse --git-common-dir 2>/dev/null)"; then
    return 1
  fi

  if [[ "${git_common_dir}" != /* ]]; then
    git_common_dir="${repo_path}/${git_common_dir}"
  fi

  (
    cd "${git_common_dir}" && pwd -P
  )
}

resolve_hooks_source_dir() {
  local repo_path="$1"
  local git_common_dir=""

  if ! git_common_dir="$(resolve_git_common_dir "${repo_path}")"; then
    return 1
  fi

  printf '%s/hooks\n' "${git_common_dir}"
}

workspace_git_common_dir=""
if git_common_dir="$(git -C "${workspace_dir}" rev-parse --git-common-dir 2>/dev/null)"; then
  if [[ "${git_common_dir}" == /* ]]; then
    workspace_git_common_dir="${git_common_dir}"
  else
    workspace_git_common_dir="${workspace_dir}/${git_common_dir}"
  fi
fi

local_source_root="${SYMPHONY_LOCAL_SOURCE_ROOT:-${repo_root}}"
hooks_source="${SYMPHONY_HOOKS_SOURCE:-}"
if [[ -z "${hooks_source}" || ! -f "${hooks_source}/pre-commit" || ! -f "${hooks_source}/pre-push" ]]; then
  if resolved_hooks_source="$(resolve_hooks_source_dir "${local_source_root}" 2>/dev/null)"; then
    hooks_source="${resolved_hooks_source}"
  elif resolved_hooks_source="$(resolve_hooks_source_dir "${repo_root}" 2>/dev/null)"; then
    hooks_source="${resolved_hooks_source}"
  else
    hooks_source="${repo_root}/.git/hooks"
  fi
fi
workspace_hooks_dir="${workspace_git_common_dir:-${workspace_dir}/.git}/hooks"

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

  local dest
  if [[ "${dest_rel}" == /* ]]; then
    dest="${dest_rel}"
  else
    dest="${workspace_dir}/${dest_rel}"
  fi

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

verify_one() {
  local dest_rel="$1"
  local required="$2"

  local dest="${workspace_dir}/${dest_rel}"
  if [[ -f "${dest}" ]]; then
    return 0
  fi

  if [[ "${required}" == "required" ]]; then
    missing_required+=("${dest_rel}")
  else
    missing_optional+=("${dest_rel}")
  fi
}

# Runtime overlay files come from the local Symphony runtime source.
ensure_one "${hooks_source}/pre-commit" "${workspace_hooks_dir}/pre-commit" "0755" "required"
ensure_one "${hooks_source}/pre-push" "${workspace_hooks_dir}/pre-push" "0755" "required"
ensure_one "${local_source_root}/.symphony/WORKFLOW.md" ".symphony/WORKFLOW.md" "0644" "required"
ensure_one "${local_source_root}/.symphony/with_github_pat.sh" ".symphony/with_github_pat.sh" "0755" "optional"
ensure_one "${local_source_root}/.symphony/github_pat_env.sh" ".symphony/github_pat_env.sh" "0644" "optional"
ensure_one "${local_source_root}/.symphony/configure_github_pat_git.sh" ".symphony/configure_github_pat_git.sh" "0755" "optional"

# Git-owned repo files must already exist in the workspace checkout.
verify_one "scripts/requirements/python-tools.txt" "required"
verify_one "scripts/utilities/ensure_python_tools_venv.sh" "required"
verify_one "scripts/utilities/symphony_pre_merge_cleanup.sh" "required"
verify_one "scripts/utilities/symphony_prepare_docker_config.sh" "required"
verify_one "scripts/utilities/symphony_guard_workspace_repo.sh" "required"
# Existing workspaces may be on branches created before this helper was added.
# AgentRunner can execute it from SYMPHONY_LOCAL_SOURCE_ROOT when the workspace
# checkout lacks the tracked copy, so do not block pre-existing workspaces here.
verify_one "scripts/utilities/symphony_guard_no_code_changes.sh" "optional"
verify_one "scripts/utilities/symphony_human_review_prep.sh" "required"
verify_one "scripts/utilities/symphony_main_sandbox.sh" "required"
verify_one "scripts/utilities/symphony_ready_for_pr.sh" "required"
verify_one "scripts/utilities/symphony_claude_review_loop.sh" "required"
verify_one "scripts/utilities/symphony_in_review.sh" "required"
verify_one "scripts/utilities/symphony_in_progress.sh" "required"
verify_one "scripts/utilities/symphony_issue_branch.sh" "required"
verify_one "scripts/utilities/symphony_linear_issue_context.sh" "required"
verify_one "scripts/utilities/symphony_linear_workpad.sh" "required"
verify_one "scripts/utilities/symphony_linear_issue_state.sh" "required"
verify_one "scripts/utilities/symphony_finalize_issue.sh" "required"
# Legacy scripts kept for backward compatibility until all workspaces update.
verify_one "scripts/utilities/symphony_request_claude_rereview.sh" "optional"
verify_one "scripts/utilities/symphony_wait_for_claude_review.sh" "optional"
verify_one "scripts/utilities/symphony_claude_review_rounds.sh" "optional"
verify_one "scripts/utilities/symphony_local_db_tunnel_start.sh" "required"
verify_one "scripts/utilities/symphony_local_db_tunnel_status.sh" "required"
verify_one "scripts/utilities/symphony_local_db_tunnel_stop.sh" "required"
# Existing workspaces may be on branches created before this helper was added.
# Agents can run the source-root copy with --workspace-dir when needed, so do
# not block those workspaces at guard time.
verify_one "scripts/utilities/symphony_curation_db_psql.sh" "optional"
verify_one "scripts/utilities/symphony_microvm_worker_run.sh" "required"
verify_one "scripts/lib/local_db_tunnel_common.sh" "required"
verify_one "scripts/lib/symphony_linear_common.sh" "required"

# Helpful but not strictly required for every lane.
verify_one "docker-compose.yml" "optional"
verify_one "scripts/utilities/check_services.sh" "optional"
verify_one "scripts/utilities/ensure_postgres_db_exists.sh" "optional"

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
