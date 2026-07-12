#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_in_progress_complete.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "FAIL: Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

assert_not_contains() {
  local unexpected="$1"
  local actual="$2"
  if [[ "${actual}" == *"${unexpected}"* ]]; then
    echo "FAIL: Expected output not to contain '${unexpected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

make_pushed_workspace() {
  local temp_dir="$1"
  local workspace="${temp_dir}/workspace"
  local remote="${temp_dir}/remote.git"

  git init -q --bare "${remote}"
  git clone -q "${remote}" "${workspace}" 2>/dev/null
  git -C "${workspace}" checkout -q -b all-123
  git -C "${workspace}" config user.name "Test User"
  git -C "${workspace}" config user.email "test@example.com"
  printf '# test\n' > "${workspace}/README.md"
  git -C "${workspace}" add README.md
  git -C "${workspace}" commit -q -m "initial"
  git -C "${workspace}" push -q -u origin all-123

  printf '#!/usr/bin/env bash\nexit 0\n' > "${workspace}/.git/hooks/pre-commit"
  printf '#!/usr/bin/env bash\nexit 0\n' > "${workspace}/.git/hooks/pre-push"
  chmod +x "${workspace}/.git/hooks/pre-commit" "${workspace}/.git/hooks/pre-push"
}

write_helper_stubs() {
  local temp_dir="$1"
  local workpad_helper="${temp_dir}/workpad-helper.sh"
  local state_helper="${temp_dir}/state-helper.sh"

  cat > "${workpad_helper}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
section_title=""
section_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --section-title)
      section_title="${2:-}"
      shift 2
      ;;
    --section-file)
      section_file="${2:-}"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
printf '%s\n' "${section_title}" > "${WORKPAD_STUB_TITLE_LOG}"
cp "${section_file}" "${WORKPAD_STUB_SECTION_LOG}"
echo "WORKPAD_STATUS=updated"
EOF

  cat > "${state_helper}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" > "${STATE_STUB_ARGS_LOG}"
echo "LINEAR_STATE_STATUS=ok"
EOF

  chmod +x "${workpad_helper}" "${state_helper}"
}

test_help_describes_completion_guard() {
  local output
  output="$(bash "${SCRIPT_PATH}" --help)"
  assert_contains "completion guard" "${output}"
  assert_contains "Review Handoff" "${output}"
}

test_clean_pushed_workspace_moves_to_needs_review() {
  local temp_dir workspace workpad_helper state_helper handoff output section state_args
  temp_dir="$(mktemp -d)"
  make_pushed_workspace "${temp_dir}"
  write_helper_stubs "${temp_dir}"
  workspace="${temp_dir}/workspace"
  workpad_helper="${temp_dir}/workpad-helper.sh"
  state_helper="${temp_dir}/state-helper.sh"
  handoff="${temp_dir}/handoff.md"

  cat > "${handoff}" <<'EOF'
- Outcome: Implemented the guard.
- Files changed: completion helper and tests.
- Validation: shell tests.
- Reviewer focus: Verify state transition safety.

### Test Plan

- Behavior proved: The completion guard accepts a clean and pushed implementation.
- Focused tests: `scripts/tests/test_symphony_in_progress_complete.sh`
- Broader local coverage: Not needed; GitHub Actions supplies the broad clean-checkout gate.
EOF

  output="$(
    WORKPAD_STUB_TITLE_LOG="${temp_dir}/workpad-title.log" \
    WORKPAD_STUB_SECTION_LOG="${temp_dir}/workpad-section.md" \
    STATE_STUB_ARGS_LOG="${temp_dir}/state-args.log" \
    bash "${SCRIPT_PATH}" \
      --issue-identifier ALL-123 \
      --workspace-dir "${workspace}" \
      --workpad-helper "${workpad_helper}" \
      --state-helper "${state_helper}" \
      --section-file "${handoff}"
  )"

  section="$(cat "${temp_dir}/workpad-section.md")"
  state_args="$(cat "${temp_dir}/state-args.log")"
  assert_contains "IN_PROGRESS_COMPLETE_STATUS=completed" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_TO_STATE=Needs Review" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_WORKSPACE_STATUS=clean" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_HOOKS_STATUS=ok" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_PUSH_STATUS=synced" "${output}"
  assert_contains "Review Handoff" "$(cat "${temp_dir}/workpad-title.log")"
  assert_contains "Completion guard: passed" "${section}"
  assert_contains "Implemented the guard" "${section}"
  assert_contains "--state Needs Review --from-state In Progress" "${state_args}"

  rm -rf "${temp_dir}"
}

test_dirty_workspace_blocks_and_keeps_in_progress() {
  local temp_dir workspace workpad_helper state_helper handoff output section
  temp_dir="$(mktemp -d)"
  make_pushed_workspace "${temp_dir}"
  write_helper_stubs "${temp_dir}"
  workspace="${temp_dir}/workspace"
  workpad_helper="${temp_dir}/workpad-helper.sh"
  state_helper="${temp_dir}/state-helper.sh"
  handoff="${temp_dir}/handoff.md"
  printf '%s\n' "- Outcome: Ready, except local dirt." > "${handoff}"
  printf 'dirty\n' >> "${workspace}/README.md"

  output="$(
    WORKPAD_STUB_TITLE_LOG="${temp_dir}/workpad-title.log" \
    WORKPAD_STUB_SECTION_LOG="${temp_dir}/workpad-section.md" \
    STATE_STUB_ARGS_LOG="${temp_dir}/state-args.log" \
    bash "${SCRIPT_PATH}" \
      --issue-identifier ALL-123 \
      --workspace-dir "${workspace}" \
      --workpad-helper "${workpad_helper}" \
      --state-helper "${state_helper}" \
      --section-file "${handoff}"
  )"

  section="$(cat "${temp_dir}/workpad-section.md")"
  assert_contains "IN_PROGRESS_COMPLETE_STATUS=blocked" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_TO_STATE=In Progress" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_REASON=workspace_dirty" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_WORKSPACE_STATUS=dirty" "${output}"
  assert_contains "Completion guard: blocked" "${section}"
  assert_contains "Dirty workspace entries" "${section}"
  assert_contains "M README.md" "${section}"
  [[ ! -e "${temp_dir}/state-args.log" ]] || {
    echo "Expected dirty workspace not to call the state helper" >&2
    exit 1
  }

  rm -rf "${temp_dir}"
}

test_missing_hooks_blocks_and_reports_hook_paths() {
  local temp_dir workspace workpad_helper state_helper handoff output section
  temp_dir="$(mktemp -d)"
  make_pushed_workspace "${temp_dir}"
  write_helper_stubs "${temp_dir}"
  workspace="${temp_dir}/workspace"
  workpad_helper="${temp_dir}/workpad-helper.sh"
  state_helper="${temp_dir}/state-helper.sh"
  handoff="${temp_dir}/handoff.md"
  printf '%s\n' "- Outcome: Ready except hooks." > "${handoff}"
  rm -f "${workspace}/.git/hooks/pre-push"

  output="$(
    WORKPAD_STUB_TITLE_LOG="${temp_dir}/workpad-title.log" \
    WORKPAD_STUB_SECTION_LOG="${temp_dir}/workpad-section.md" \
    STATE_STUB_ARGS_LOG="${temp_dir}/state-args.log" \
    bash "${SCRIPT_PATH}" \
      --issue-identifier ALL-123 \
      --workspace-dir "${workspace}" \
      --workpad-helper "${workpad_helper}" \
      --state-helper "${state_helper}" \
      --section-file "${handoff}"
  )"

  section="$(cat "${temp_dir}/workpad-section.md")"
  assert_contains "IN_PROGRESS_COMPLETE_STATUS=blocked" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_REASON=required_hooks_missing_required_hooks" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_HOOKS_STATUS=missing_required_hooks" "${output}"
  assert_contains "Hook issues" "${section}"
  assert_contains "pre-push" "${section}"
  [[ ! -e "${temp_dir}/state-args.log" ]] || {
    echo "Expected missing hooks not to call the state helper" >&2
    exit 1
  }

  rm -rf "${temp_dir}"
}

test_unpushed_commit_blocks_and_reports_ahead_count() {
  local temp_dir workspace workpad_helper state_helper handoff output section
  temp_dir="$(mktemp -d)"
  make_pushed_workspace "${temp_dir}"
  write_helper_stubs "${temp_dir}"
  workspace="${temp_dir}/workspace"
  workpad_helper="${temp_dir}/workpad-helper.sh"
  state_helper="${temp_dir}/state-helper.sh"
  handoff="${temp_dir}/handoff.md"
  printf '%s\n' "- Outcome: Ready except push." > "${handoff}"
  printf 'unpushed\n' > "${workspace}/feature.txt"
  git -C "${workspace}" add feature.txt
  git -C "${workspace}" commit -q -m "unpushed change"

  output="$(
    WORKPAD_STUB_TITLE_LOG="${temp_dir}/workpad-title.log" \
    WORKPAD_STUB_SECTION_LOG="${temp_dir}/workpad-section.md" \
    STATE_STUB_ARGS_LOG="${temp_dir}/state-args.log" \
    bash "${SCRIPT_PATH}" \
      --issue-identifier ALL-123 \
      --workspace-dir "${workspace}" \
      --workpad-helper "${workpad_helper}" \
      --state-helper "${state_helper}" \
      --section-file "${handoff}"
  )"

  section="$(cat "${temp_dir}/workpad-section.md")"
  assert_contains "IN_PROGRESS_COMPLETE_STATUS=blocked" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_REASON=branch_unpushed_commits" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_PUSH_STATUS=unpushed_commits" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_AHEAD_COUNT=1" "${output}"
  assert_contains "Push status: unpushed_commits (ahead=1, behind=0)" "${section}"
  [[ ! -e "${temp_dir}/state-args.log" ]] || {
    echo "Expected unpushed commits not to call the state helper" >&2
    exit 1
  }

  rm -rf "${temp_dir}"
}

test_main_only_fetch_workspace_refreshes_configured_issue_upstream() {
  local temp_dir workspace workpad_helper state_helper handoff output section state_args
  temp_dir="$(mktemp -d)"
  make_pushed_workspace "${temp_dir}"
  write_helper_stubs "${temp_dir}"
  workspace="${temp_dir}/workspace"
  workpad_helper="${temp_dir}/workpad-helper.sh"
  state_helper="${temp_dir}/state-helper.sh"
  handoff="${temp_dir}/handoff.md"

  # Symphony clones only main. Preserve the configured issue-branch upstream,
  # but remove its remote-tracking ref to reproduce that workspace shape.
  git -C "${workspace}" config --replace-all remote.origin.fetch \
    '+refs/heads/main:refs/remotes/origin/main'
  git -C "${workspace}" update-ref -d refs/remotes/origin/all-123
  if git -C "${workspace}" rev-parse --verify '@{u}' >/dev/null 2>&1; then
    echo "Expected the configured upstream ref to be absent before the guard runs" >&2
    exit 1
  fi

  cat > "${handoff}" <<'EOF'
- Outcome: Ready from a main-only Symphony clone.

### Test Plan

- Behavior proved: The completion guard refreshes an absent issue-branch upstream reference.
- Focused tests: `scripts/tests/test_symphony_in_progress_complete.sh`
- Broader local coverage: Not needed; GitHub Actions supplies the broad clean-checkout gate.
EOF
  output="$(
    WORKPAD_STUB_TITLE_LOG="${temp_dir}/workpad-title.log" \
    WORKPAD_STUB_SECTION_LOG="${temp_dir}/workpad-section.md" \
    STATE_STUB_ARGS_LOG="${temp_dir}/state-args.log" \
    bash "${SCRIPT_PATH}" \
      --issue-identifier ALL-123 \
      --workspace-dir "${workspace}" \
      --workpad-helper "${workpad_helper}" \
      --state-helper "${state_helper}" \
      --section-file "${handoff}"
  )"

  section="$(cat "${temp_dir}/workpad-section.md")"
  state_args="$(cat "${temp_dir}/state-args.log")"
  assert_contains "IN_PROGRESS_COMPLETE_STATUS=completed" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_PUSH_STATUS=synced" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_UPSTREAM=origin/all-123" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_UPSTREAM_REFRESH_STATUS=fetched" "${output}"
  assert_contains "Upstream refresh: fetched" "${section}"
  assert_contains "--state Needs Review --from-state In Progress" "${state_args}"

  rm -rf "${temp_dir}"
}

test_missing_handoff_input_blocks_even_when_git_ready() {
  local temp_dir workspace workpad_helper state_helper output section
  temp_dir="$(mktemp -d)"
  make_pushed_workspace "${temp_dir}"
  write_helper_stubs "${temp_dir}"
  workspace="${temp_dir}/workspace"
  workpad_helper="${temp_dir}/workpad-helper.sh"
  state_helper="${temp_dir}/state-helper.sh"

  output="$(
    WORKPAD_STUB_TITLE_LOG="${temp_dir}/workpad-title.log" \
    WORKPAD_STUB_SECTION_LOG="${temp_dir}/workpad-section.md" \
    STATE_STUB_ARGS_LOG="${temp_dir}/state-args.log" \
    bash "${SCRIPT_PATH}" \
      --issue-identifier ALL-123 \
      --workspace-dir "${workspace}" \
      --workpad-helper "${workpad_helper}" \
      --state-helper "${state_helper}"
  )"

  section="$(cat "${temp_dir}/workpad-section.md")"
  assert_contains "IN_PROGRESS_COMPLETE_STATUS=blocked" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_REASON=missing_review_handoff_input" "${output}"
  assert_contains "Review Handoff input: missing" "${section}"
  assert_contains "provide the implementation summary" "${section}"
  [[ ! -e "${temp_dir}/state-args.log" ]] || {
    echo "Expected missing handoff input not to call the state helper" >&2
    exit 1
  }

  rm -rf "${temp_dir}"
}

test_missing_test_plan_blocks_even_when_git_ready() {
  local temp_dir workspace workpad_helper state_helper handoff output section
  temp_dir="$(mktemp -d)"
  make_pushed_workspace "${temp_dir}"
  write_helper_stubs "${temp_dir}"
  workspace="${temp_dir}/workspace"
  workpad_helper="${temp_dir}/workpad-helper.sh"
  state_helper="${temp_dir}/state-helper.sh"
  handoff="${temp_dir}/handoff.md"
  printf '%s\n' "- Outcome: Implementation is ready but its test plan is missing." > "${handoff}"

  output="$(
    WORKPAD_STUB_TITLE_LOG="${temp_dir}/workpad-title.log" \
    WORKPAD_STUB_SECTION_LOG="${temp_dir}/workpad-section.md" \
    STATE_STUB_ARGS_LOG="${temp_dir}/state-args.log" \
    bash "${SCRIPT_PATH}" \
      --issue-identifier ALL-123 \
      --workspace-dir "${workspace}" \
      --workpad-helper "${workpad_helper}" \
      --state-helper "${state_helper}" \
      --section-file "${handoff}"
  )"

  section="$(cat "${temp_dir}/workpad-section.md")"
  assert_contains "IN_PROGRESS_COMPLETE_STATUS=blocked" "${output}"
  assert_contains "IN_PROGRESS_COMPLETE_REASON=test_plan_missing" "${output}"
  assert_contains "Test Plan: missing" "${section}"
  assert_contains "Test Plan issue" "${section}"
  [[ ! -e "${temp_dir}/state-args.log" ]] || {
    echo "Expected missing Test Plan not to call the state helper" >&2
    exit 1
  }

  rm -rf "${temp_dir}"
}

test_help_describes_completion_guard
test_clean_pushed_workspace_moves_to_needs_review
test_dirty_workspace_blocks_and_keeps_in_progress
test_missing_hooks_blocks_and_reports_hook_paths
test_unpushed_commit_blocks_and_reports_ahead_count
test_main_only_fetch_workspace_refreshes_configured_issue_upstream
test_missing_handoff_input_blocks_even_when_git_ready
test_missing_test_plan_blocks_even_when_git_ready

echo "symphony_in_progress_complete tests passed"
