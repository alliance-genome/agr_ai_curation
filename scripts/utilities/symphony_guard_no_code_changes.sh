#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_guard_no_code_changes.sh [check] [options]
  symphony_guard_no_code_changes.sh snapshot [options]
  symphony_guard_no_code_changes.sh verify [options]

Purpose:
  Guard Symphony lanes that must not leave repository code changes behind.

Default write-allowed states:
  - In Progress

Options:
  --workspace-dir DIR           Git workspace to inspect (default: current dir).
  --state VALUE                 Current Linear state/lane name.
  --issue-identifier VALUE      Issue key for artifact naming.
  --artifact-dir DIR            Directory for violation artifacts.
  --snapshot-file PATH          Snapshot file for snapshot/verify.
  --check-head                  In verify/check mode, also fail if HEAD changed
                                from the snapshot. Requires --snapshot-file.
  --allowed-write-state VALUE   Add/override a write-allowed state. Repeatable.
  --no-default-allowed-states   Start with no write-allowed states.
  -h, --help                    Show this help.

Machine-readable output:
  NO_CODE_GUARD_STATUS=ok|skipped_allowed_state|snapshot|dirty|head_changed|error
  NO_CODE_GUARD_APPLIES=true|false
  NO_CODE_GUARD_STATE=<state>
  NO_CODE_GUARD_WORKSPACE=<path>
  NO_CODE_GUARD_ARTIFACT_DIR=<path, on violations>

Exit codes:
  0   Clean or skipped because the state is allowed to write.
  2   Invalid arguments or missing git workspace/snapshot.
  20  Worktree/index has non-runtime changes.
  21  --check-head detected a HEAD change from the snapshot.
EOF
}

subcommand="check"
case "${1:-}" in
  check|snapshot|verify)
    subcommand="$1"
    shift
    ;;
  -h|--help)
    usage
    exit 0
    ;;
esac

workspace_dir="${PWD}"
state_name=""
issue_identifier=""
artifact_dir=""
snapshot_file=""
check_head=0
use_default_allowed_states=1
allowed_write_states=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-dir)
      workspace_dir="${2:-}"
      shift 2
      ;;
    --state)
      state_name="${2:-}"
      shift 2
      ;;
    --issue-identifier)
      issue_identifier="${2:-}"
      shift 2
      ;;
    --artifact-dir)
      artifact_dir="${2:-}"
      shift 2
      ;;
    --snapshot-file)
      snapshot_file="${2:-}"
      shift 2
      ;;
    --check-head)
      check_head=1
      shift
      ;;
    --allowed-write-state)
      allowed_write_states+=("${2:-}")
      shift 2
      ;;
    --no-default-allowed-states)
      use_default_allowed_states=0
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

if [[ "${use_default_allowed_states}" -eq 1 ]]; then
  allowed_write_states=("In Progress" "${allowed_write_states[@]}")
fi

normalize_state() {
  printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

safe_path_component() {
  local value="${1:-issue}"
  value="$(printf '%s' "${value}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+//; s/-+$//')"
  if [[ -z "${value}" ]]; then
    value="issue"
  fi
  printf '%s' "${value}"
}

state_allows_writes() {
  local normalized_state
  local allowed
  normalized_state="$(normalize_state "${state_name}")"

  for allowed in "${allowed_write_states[@]}"; do
    if [[ "${normalized_state}" == "$(normalize_state "${allowed}")" ]]; then
      return 0
    fi
  done

  return 1
}

resolve_workspace() {
  if [[ ! -d "${workspace_dir}" ]]; then
    echo "Workspace directory does not exist: ${workspace_dir}" >&2
    return 1
  fi

  workspace_dir="$(cd "${workspace_dir}" && pwd -P)"
  if ! git -C "${workspace_dir}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Workspace is not a git worktree: ${workspace_dir}" >&2
    return 1
  fi
}

git_dir() {
  local dir
  dir="$(git -C "${workspace_dir}" rev-parse --git-dir)"
  case "${dir}" in
    /*) printf '%s' "${dir}" ;;
    *) printf '%s/%s' "${workspace_dir}" "${dir}" ;;
  esac
}

default_artifact_dir() {
  local safe_issue
  safe_issue="$(safe_path_component "${issue_identifier:-$(basename "${workspace_dir}")}")"
  printf '%s/symphony-no-code-guard/%s/%s' \
    "$(git_dir)" \
    "${safe_issue}" \
    "$(date -u +%Y%m%dT%H%M%SZ)"
}

default_snapshot_file() {
  local safe_issue
  safe_issue="$(safe_path_component "${issue_identifier:-$(basename "${workspace_dir}")}")"
  printf '%s/symphony-no-code-guard/%s/snapshot.env' "$(git_dir)" "${safe_issue}"
}

filtered_porcelain_status() {
  local line

  git -C "${workspace_dir}" status \
    --porcelain=v1 \
    --untracked-files=all \
    --ignore-submodules=dirty 2>/dev/null |
    while IFS= read -r line; do
      case "${line}" in
        "?? .symphony/"*|\
        "?? .symphony-docker-config"*|\
        "?? scripts/local_db_tunnel_env.sh"|\
        "?? scripts/utilities/symphony_main_sandbox.sh")
          continue
          ;;
      esac

      printf '%s\n' "${line}"
    done
}

write_artifacts() {
  local status_output="$1"
  local reason="$2"

  if [[ -z "${artifact_dir}" ]]; then
    artifact_dir="$(default_artifact_dir)"
  fi
  mkdir -p "${artifact_dir}"

  printf '%s\n' "${status_output}" > "${artifact_dir}/status.txt"
  git -C "${workspace_dir}" status --short --branch --untracked-files=all > "${artifact_dir}/git-status-short.txt" 2>&1 || true
  git -C "${workspace_dir}" rev-parse --abbrev-ref HEAD > "${artifact_dir}/branch.txt" 2>&1 || true
  git -C "${workspace_dir}" rev-parse HEAD > "${artifact_dir}/head.txt" 2>&1 || true
  git -C "${workspace_dir}" diff --no-ext-diff > "${artifact_dir}/diff.patch" 2>&1 || true
  git -C "${workspace_dir}" diff --cached --no-ext-diff > "${artifact_dir}/diff-cached.patch" 2>&1 || true
  {
    echo "reason=${reason}"
    echo "state=${state_name}"
    echo "issue_identifier=${issue_identifier}"
    echo "workspace=${workspace_dir}"
    date -u +"created_at=%Y-%m-%dT%H:%M:%SZ"
  } > "${artifact_dir}/summary.env"
}

emit_common() {
  local status="$1"
  local applies="$2"

  echo "NO_CODE_GUARD_STATUS=${status}"
  echo "NO_CODE_GUARD_APPLIES=${applies}"
  echo "NO_CODE_GUARD_STATE=${state_name}"
  echo "NO_CODE_GUARD_WORKSPACE=${workspace_dir}"
}

skip_if_allowed_state() {
  if state_allows_writes; then
    emit_common "skipped_allowed_state" "false"
    echo "NO_CODE_GUARD_MESSAGE=State ${state_name} is allowed to leave code changes."
    return 0
  fi

  return 1
}

read_snapshot_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "${snapshot_file}" | tail -n 1
}

snapshot() {
  local status_output head branch

  if skip_if_allowed_state; then
    return 0
  fi

  status_output="$(filtered_porcelain_status)"
  if [[ -n "${status_output}" ]]; then
    write_artifacts "${status_output}" "dirty_at_snapshot"
    emit_common "dirty" "true"
    echo "NO_CODE_GUARD_ARTIFACT_DIR=${artifact_dir}"
    echo "NO_CODE_GUARD_MESSAGE=Workspace already has code changes before the no-code lane can be guarded."
    return 20
  fi

  if [[ -z "${snapshot_file}" ]]; then
    snapshot_file="$(default_snapshot_file)"
  fi
  mkdir -p "$(dirname "${snapshot_file}")"

  head="$(git -C "${workspace_dir}" rev-parse HEAD)"
  branch="$(git -C "${workspace_dir}" rev-parse --abbrev-ref HEAD)"
  {
    echo "NO_CODE_GUARD_SNAPSHOT_HEAD=${head}"
    echo "NO_CODE_GUARD_SNAPSHOT_BRANCH=${branch}"
    echo "NO_CODE_GUARD_SNAPSHOT_STATE=${state_name}"
    echo "NO_CODE_GUARD_SNAPSHOT_WORKSPACE=${workspace_dir}"
    date -u +"NO_CODE_GUARD_SNAPSHOT_CREATED_AT=%Y-%m-%dT%H:%M:%SZ"
  } > "${snapshot_file}"

  emit_common "snapshot" "true"
  echo "NO_CODE_GUARD_SNAPSHOT_FILE=${snapshot_file}"
  echo "NO_CODE_GUARD_HEAD=${head}"
  echo "NO_CODE_GUARD_BRANCH=${branch}"
}

verify() {
  local status_output snapshot_head current_head head_status

  if skip_if_allowed_state; then
    return 0
  fi

  status_output="$(filtered_porcelain_status)"
  if [[ -n "${status_output}" ]]; then
    write_artifacts "${status_output}" "dirty"
    emit_common "dirty" "true"
    echo "NO_CODE_GUARD_ARTIFACT_DIR=${artifact_dir}"
    echo "NO_CODE_GUARD_MESSAGE=No-code lane left repository changes behind. Preserve artifacts and move the issue to Blocked."
    return 20
  fi

  if [[ "${check_head}" -eq 1 ]]; then
    if [[ -z "${snapshot_file}" ]]; then
      snapshot_file="$(default_snapshot_file)"
    fi
    if [[ ! -f "${snapshot_file}" ]]; then
      emit_common "error" "true"
      echo "NO_CODE_GUARD_ERROR=--check-head requires a snapshot file."
      return 2
    fi

    snapshot_head="$(read_snapshot_value "NO_CODE_GUARD_SNAPSHOT_HEAD")"
    current_head="$(git -C "${workspace_dir}" rev-parse HEAD)"
    if [[ -z "${snapshot_head}" || "${snapshot_head}" != "${current_head}" ]]; then
      head_status="snapshot_head=${snapshot_head}"$'\n'"current_head=${current_head}"
      write_artifacts "${head_status}" "head_changed"
      emit_common "head_changed" "true"
      echo "NO_CODE_GUARD_ARTIFACT_DIR=${artifact_dir}"
      echo "NO_CODE_GUARD_SNAPSHOT_FILE=${snapshot_file}"
      echo "NO_CODE_GUARD_MESSAGE=No-code lane changed HEAD. Preserve artifacts and inspect before continuing."
      return 21
    fi
  fi

  emit_common "ok" "true"
  echo "NO_CODE_GUARD_MESSAGE=No repository code changes detected."
}

if ! resolve_workspace; then
  echo "NO_CODE_GUARD_STATUS=error"
  echo "NO_CODE_GUARD_APPLIES=true"
  echo "NO_CODE_GUARD_STATE=${state_name}"
  echo "NO_CODE_GUARD_WORKSPACE=${workspace_dir}"
  echo "NO_CODE_GUARD_ERROR=Workspace is unavailable or not a git worktree."
  exit 2
fi

case "${subcommand}" in
  snapshot)
    snapshot
    ;;
  check|verify)
    verify
    ;;
  *)
    echo "Unknown subcommand: ${subcommand}" >&2
    usage
    exit 2
    ;;
esac
