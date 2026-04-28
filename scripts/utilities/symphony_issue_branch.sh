#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF' >&2
Usage:
  symphony_issue_branch.sh --issue-identifier ISSUE [options]

Options:
  --issue-identifier VALUE   Required: Linear issue key such as ALL-126
  --branch VALUE             Explicit branch name to ensure (default: normalized issue key)
  --base-branch VALUE        Branch treated as the workspace base (default: main)
  --dry-run                  Report what would happen without changing branches
EOF
}

issue_identifier=""
target_branch=""
base_branch="main"
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue-identifier)
      issue_identifier="${2:-}"
      shift 2
      ;;
    --branch)
      target_branch="${2:-}"
      shift 2
      ;;
    --base-branch)
      base_branch="${2:-}"
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

if [[ -z "${issue_identifier}" ]]; then
  usage
  exit 2
fi

derive_branch_name() {
  local value="$1"

  value="$(printf '%s' "${value}" | tr '[:upper:]' '[:lower:]')"
  value="$(printf '%s' "${value}" | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')"

  if [[ -z "${value}" ]]; then
    return 1
  fi

  printf '%s' "${value}"
}

is_base_like_branch() {
  local branch_name="$1"

  case "${branch_name}" in
    ""|main|master|trunk|develop|development)
      return 0
      ;;
    "${base_branch}")
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

emit_result() {
  local status="$1"
  local branch_name="$2"
  local previous_branch="$3"
  local message="$4"

  echo "ISSUE_BRANCH_STATUS=${status}"
  echo "ISSUE_BRANCH_NAME=${branch_name}"
  echo "ISSUE_BRANCH_PREVIOUS_BRANCH=${previous_branch}"
  echo "ISSUE_BRANCH_BASE_BRANCH=${base_branch}"
  echo "ISSUE_BRANCH_MESSAGE=${message}"
}

worktree_dirty() {
  [[ -n "$(git status --porcelain --untracked-files=normal 2>/dev/null || true)" ]]
}

if [[ -z "${target_branch}" ]]; then
  if ! target_branch="$(derive_branch_name "${issue_identifier}")"; then
    echo "Could not derive a valid branch name from issue identifier: ${issue_identifier}" >&2
    exit 2
  fi
fi

current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"

if [[ -z "${current_branch}" ]]; then
  echo "Unable to determine current git branch." >&2
  exit 2
fi

if [[ "${current_branch}" == "${target_branch}" ]]; then
  emit_result "already_on_target" "${target_branch}" "${current_branch}" \
    "Already on issue branch ${target_branch}."
  exit 0
fi

if worktree_dirty; then
  emit_result "blocked_dirty_worktree" "${target_branch}" "${current_branch}" \
    "Workspace has uncommitted changes on ${current_branch}. Clean or stash them before switching or creating the issue branch."
  exit 20
fi

if git show-ref --verify --quiet "refs/heads/${target_branch}"; then
  if [[ "${dry_run}" -eq 1 ]]; then
    emit_result "dry_run_switch" "${target_branch}" "${current_branch}" \
      "Would switch from ${current_branch} to existing issue branch ${target_branch}."
    exit 0
  fi

  git switch "${target_branch}" >/dev/null 2>&1
  emit_result "switched" "${target_branch}" "${current_branch}" \
    "Switched from ${current_branch} to existing issue branch ${target_branch}."
  exit 0
fi

if git show-ref --verify --quiet "refs/remotes/origin/${target_branch}"; then
  if [[ "${dry_run}" -eq 1 ]]; then
    emit_result "dry_run_switch_remote" "${target_branch}" "${current_branch}" \
      "Would create local branch ${target_branch} from origin/${target_branch}."
    exit 0
  fi

  git switch --track -c "${target_branch}" "origin/${target_branch}" >/dev/null 2>&1
  emit_result "switched_remote" "${target_branch}" "${current_branch}" \
    "Created local issue branch ${target_branch} from origin/${target_branch}."
  exit 0
fi

if git ls-remote --exit-code --heads origin "${target_branch}" >/dev/null 2>&1; then
  if [[ "${dry_run}" -eq 1 ]]; then
    emit_result "dry_run_fetch_remote" "${target_branch}" "${current_branch}" \
      "Would fetch origin/${target_branch} and create a local tracking branch."
    exit 0
  fi

  git fetch origin "refs/heads/${target_branch}:refs/remotes/origin/${target_branch}" >/dev/null 2>&1
  git switch -c "${target_branch}" "origin/${target_branch}" >/dev/null 2>&1
  git branch --set-upstream-to="origin/${target_branch}" "${target_branch}" >/dev/null 2>&1 || true
  emit_result "switched_remote" "${target_branch}" "${current_branch}" \
    "Fetched origin/${target_branch} and created a local issue branch from it."
  exit 0
fi

if ! is_base_like_branch "${current_branch}"; then
  emit_result "blocked_unexpected_branch" "${target_branch}" "${current_branch}" \
    "Workspace is on unexpected branch ${current_branch} and issue branch ${target_branch} does not exist yet. Return to ${base_branch} or clean up the workspace before continuing."
  exit 21
fi

if [[ "${dry_run}" -eq 1 ]]; then
  emit_result "dry_run_create" "${target_branch}" "${current_branch}" \
    "Would create issue branch ${target_branch} from ${current_branch}."
  exit 0
fi

git switch -c "${target_branch}" >/dev/null 2>&1
emit_result "created" "${target_branch}" "${current_branch}" \
  "Created issue branch ${target_branch} from ${current_branch}."
