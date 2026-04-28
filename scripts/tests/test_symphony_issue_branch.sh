#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_issue_branch.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

make_repo() {
  local repo_dir="$1"

  git init -b main "${repo_dir}" >/dev/null
  git -C "${repo_dir}" config user.name "Symphony Test"
  git -C "${repo_dir}" config user.email "symphony@example.com"
  printf 'seed\n' > "${repo_dir}/README.md"
  git -C "${repo_dir}" add README.md
  git -C "${repo_dir}" commit -m "seed" >/dev/null
}

make_bare_remote() {
  local remote_dir="$1"
  git init --bare "${remote_dir}" >/dev/null
}

test_creates_issue_branch_from_main() {
  local temp_dir repo_dir output current_branch
  temp_dir="$(mktemp -d)"
  repo_dir="${temp_dir}/repo"
  make_repo "${repo_dir}"

  output="$(
    cd "${repo_dir}" &&
      bash "${SCRIPT_PATH}" --issue-identifier ALL-126
  )"

  current_branch="$(git -C "${repo_dir}" branch --show-current)"

  assert_contains "ISSUE_BRANCH_STATUS=created" "${output}"
  assert_contains "ISSUE_BRANCH_NAME=all-126" "${output}"
  [[ "${current_branch}" == "all-126" ]] || {
    echo "Expected current branch to be all-126, got ${current_branch}" >&2
    exit 1
  }
}

test_switches_to_existing_issue_branch() {
  local temp_dir repo_dir output current_branch
  temp_dir="$(mktemp -d)"
  repo_dir="${temp_dir}/repo"
  make_repo "${repo_dir}"

  git -C "${repo_dir}" branch all-126
  git -C "${repo_dir}" switch -c custom-work >/dev/null

  output="$(
    cd "${repo_dir}" &&
      bash "${SCRIPT_PATH}" --issue-identifier ALL-126
  )"

  current_branch="$(git -C "${repo_dir}" branch --show-current)"

  assert_contains "ISSUE_BRANCH_STATUS=switched" "${output}"
  assert_contains "ISSUE_BRANCH_NAME=all-126" "${output}"
  [[ "${current_branch}" == "all-126" ]] || {
    echo "Expected current branch to be all-126, got ${current_branch}" >&2
    exit 1
  }
}

test_unexpected_existing_branch_is_blocked() {
  local temp_dir repo_dir output_file rc output current_branch
  temp_dir="$(mktemp -d)"
  repo_dir="${temp_dir}/repo"
  make_repo "${repo_dir}"

  git -C "${repo_dir}" switch -c custom-work >/dev/null
  output_file="${temp_dir}/out.txt"

  set +e
  (
    cd "${repo_dir}" &&
      bash "${SCRIPT_PATH}" --issue-identifier ALL-126 > "${output_file}"
  )
  rc=$?
  set -e

  output="$(cat "${output_file}")"

  current_branch="$(git -C "${repo_dir}" branch --show-current)"

  [[ "${rc}" == "21" ]] || {
    echo "Expected exit code 21, got ${rc}" >&2
    exit 1
  }

  assert_contains "ISSUE_BRANCH_STATUS=blocked_unexpected_branch" "${output}"
  assert_contains "ISSUE_BRANCH_NAME=all-126" "${output}"
  [[ "${current_branch}" == "custom-work" ]] || {
    echo "Expected current branch to remain custom-work, got ${current_branch}" >&2
    exit 1
  }
}

test_dirty_base_branch_is_blocked() {
  local temp_dir repo_dir output_file rc output current_branch
  temp_dir="$(mktemp -d)"
  repo_dir="${temp_dir}/repo"
  make_repo "${repo_dir}"
  output_file="${temp_dir}/out.txt"

  printf 'dirty\n' >> "${repo_dir}/README.md"

  set +e
  (
    cd "${repo_dir}" &&
      bash "${SCRIPT_PATH}" --issue-identifier ALL-126 > "${output_file}"
  )
  rc=$?
  set -e

  output="$(cat "${output_file}")"
  current_branch="$(git -C "${repo_dir}" branch --show-current)"

  [[ "${rc}" == "20" ]] || {
    echo "Expected exit code 20, got ${rc}" >&2
    exit 1
  }

  assert_contains "ISSUE_BRANCH_STATUS=blocked_dirty_worktree" "${output}"
  [[ "${current_branch}" == "main" ]] || {
    echo "Expected current branch to remain main, got ${current_branch}" >&2
    exit 1
  }
}

test_switches_to_remote_only_issue_branch() {
  local temp_dir remote_dir source_dir repo_dir output current_branch
  temp_dir="$(mktemp -d)"
  remote_dir="${temp_dir}/remote.git"
  source_dir="${temp_dir}/source"
  repo_dir="${temp_dir}/repo"

  make_bare_remote "${remote_dir}"
  make_repo "${source_dir}"
  git -C "${source_dir}" remote add origin "${remote_dir}"
  git -C "${source_dir}" push -u origin main >/dev/null
  git clone --branch main "${remote_dir}" "${repo_dir}" >/dev/null
  git -C "${source_dir}" switch -c all-126 >/dev/null
  printf 'branch work\n' >> "${source_dir}/README.md"
  git -C "${source_dir}" commit -am "issue branch" >/dev/null
  git -C "${source_dir}" push -u origin all-126 >/dev/null

  output="$(
    cd "${repo_dir}" &&
      bash "${SCRIPT_PATH}" --issue-identifier ALL-126
  )"

  current_branch="$(git -C "${repo_dir}" branch --show-current)"

  assert_contains "ISSUE_BRANCH_STATUS=switched_remote" "${output}"
  assert_contains "ISSUE_BRANCH_NAME=all-126" "${output}"
  [[ "${current_branch}" == "all-126" ]] || {
    echo "Expected current branch to be all-126, got ${current_branch}" >&2
    exit 1
  }
}

test_switches_to_remote_only_issue_branch_from_depth_one_main_clone() {
  local temp_dir remote_dir source_dir repo_dir output current_branch
  temp_dir="$(mktemp -d)"
  remote_dir="${temp_dir}/remote.git"
  source_dir="${temp_dir}/source"
  repo_dir="${temp_dir}/repo"

  make_bare_remote "${remote_dir}"
  make_repo "${source_dir}"
  git -C "${source_dir}" remote add origin "${remote_dir}"
  git -C "${source_dir}" push -u origin main >/dev/null
  git -C "${source_dir}" switch -c all-126 >/dev/null
  printf 'branch work\n' >> "${source_dir}/README.md"
  git -C "${source_dir}" commit -am "issue branch" >/dev/null
  git -C "${source_dir}" push -u origin all-126 >/dev/null
  git clone --depth 1 --branch main "file://${remote_dir}" "${repo_dir}" >/dev/null

  output="$(
    cd "${repo_dir}" &&
      bash "${SCRIPT_PATH}" --issue-identifier ALL-126
  )"

  current_branch="$(git -C "${repo_dir}" branch --show-current)"

  assert_contains "ISSUE_BRANCH_STATUS=switched_remote" "${output}"
  assert_contains "ISSUE_BRANCH_NAME=all-126" "${output}"
  [[ "${current_branch}" == "all-126" ]] || {
    echo "Expected current branch to be all-126, got ${current_branch}" >&2
    exit 1
  }
}

test_detached_head_is_blocked_when_issue_branch_missing() {
  local temp_dir repo_dir output_file rc output current_branch
  temp_dir="$(mktemp -d)"
  repo_dir="${temp_dir}/repo"
  make_repo "${repo_dir}"
  output_file="${temp_dir}/out.txt"

  git -C "${repo_dir}" checkout --detach >/dev/null 2>&1

  set +e
  (
    cd "${repo_dir}" &&
      bash "${SCRIPT_PATH}" --issue-identifier ALL-126 > "${output_file}"
  )
  rc=$?
  set -e

  output="$(cat "${output_file}")"
  current_branch="$(git -C "${repo_dir}" rev-parse --abbrev-ref HEAD)"

  [[ "${rc}" == "21" ]] || {
    echo "Expected exit code 21, got ${rc}" >&2
    exit 1
  }

  assert_contains "ISSUE_BRANCH_STATUS=blocked_unexpected_branch" "${output}"
  [[ "${current_branch}" == "HEAD" ]] || {
    echo "Expected detached HEAD to remain in place, got ${current_branch}" >&2
    exit 1
  }
}

test_creates_issue_branch_from_main
test_switches_to_existing_issue_branch
test_unexpected_existing_branch_is_blocked
test_dirty_base_branch_is_blocked
test_switches_to_remote_only_issue_branch
test_switches_to_remote_only_issue_branch_from_depth_one_main_clone
test_detached_head_is_blocked_when_issue_branch_missing

echo "symphony_issue_branch tests passed"
