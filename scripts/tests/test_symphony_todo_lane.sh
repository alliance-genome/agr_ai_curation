#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_todo_lane.sh"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/assertions.sh"

assert_equals() {
  local expected="$1"
  local actual="$2"

  if [[ "${actual}" != "${expected}" ]]; then
    echo "Expected '${expected}', got '${actual}'" >&2
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

write_context_json() {
  local path="$1"
  local state="${2:-Todo}"

  jq -cn \
    --arg state "${state}" '
    {
      issue: {
        id: "issue-all-123",
        identifier: "ALL-123",
        title: "Script Todo lane",
        description: "Description.",
        state: {name: $state}
      },
      comments: [],
      workpad_comment: null,
      latest_non_workpad_comment: null,
      team: {
        states: [
          {id: "state-todo", name: "Todo"},
          {id: "state-in-progress", name: "In Progress"},
          {id: "state-blocked", name: "Blocked"}
        ]
      }
    }' > "${path}"
}

write_stub_helpers() {
  local helper_dir="$1"
  local workpad_log="$2"
  local state_log="$3"

  mkdir -p "${helper_dir}"

  cat > "${helper_dir}/workpad.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
section_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --section-file)
      section_file="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
cat "${section_file}" > "${SYMPHONY_TEST_WORKPAD_LOG:?}"
echo "WORKPAD_STATUS=updated"
EOF

  cat > "${helper_dir}/state.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
target_state=""
from_state=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --state)
      target_state="$2"
      shift 2
      ;;
    --from-state)
      from_state="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
{
  echo "target=${target_state}"
  echo "from=${from_state}"
} > "${SYMPHONY_TEST_STATE_LOG:?}"
echo "LINEAR_STATE_STATUS=ok"
EOF

  chmod +x "${helper_dir}/workpad.sh" "${helper_dir}/state.sh"
  export SYMPHONY_TEST_WORKPAD_LOG="${workpad_log}"
  export SYMPHONY_TEST_STATE_LOG="${state_log}"
}

test_clean_todo_creates_branch_and_moves_to_in_progress() {
  local temp_root repo context helper_dir output workpad_log state_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-123"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"

  make_repo "${repo}"
  write_context_json "${context}" "Todo"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    > "${output}"

  assert_contains "TODO_LANE_STATUS=handed_off" "${output}"
  assert_contains "TODO_LANE_TO_STATE=In Progress" "${output}"
  assert_contains "TODO_LANE_BRANCH=all-123" "${output}"
  assert_equals "all-123" "$(git -C "${repo}" branch --show-current)"
  assert_contains "Branch helper status: \`created\`" "${workpad_log}"
  assert_contains "Next lane: In Progress" "${workpad_log}"
  assert_contains "target=In Progress" "${state_log}"
  assert_contains "from=Todo" "${state_log}"

  rm -rf "${temp_root}"
}

test_dirty_todo_blocks_without_switching() {
  local temp_root repo context helper_dir output workpad_log state_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-123"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"

  make_repo "${repo}"
  printf 'dirty\n' >> "${repo}/README.md"
  write_context_json "${context}" "Todo"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    > "${output}"

  assert_contains "TODO_LANE_STATUS=blocked" "${output}"
  assert_contains "TODO_LANE_TO_STATE=Blocked" "${output}"
  assert_contains "TODO_LANE_REASON=blocked_dirty_worktree" "${output}"
  assert_equals "main" "$(git -C "${repo}" branch --show-current)"
  assert_contains "Todo intake could not safely prepare" "${workpad_log}"
  assert_contains "target=Blocked" "${state_log}"

  rm -rf "${temp_root}"
}

test_dirty_todo_blocks_when_already_on_issue_branch() {
  local temp_root repo context helper_dir output workpad_log state_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-123"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"

  make_repo "${repo}"
  git -C "${repo}" switch -c all-123 >/dev/null
  printf 'dirty\n' >> "${repo}/README.md"
  write_context_json "${context}" "Todo"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    > "${output}"

  assert_contains "TODO_LANE_STATUS=blocked" "${output}"
  assert_contains "TODO_LANE_TO_STATE=Blocked" "${output}"
  assert_contains "TODO_LANE_REASON=blocked_dirty_worktree" "${output}"
  assert_equals "all-123" "$(git -C "${repo}" branch --show-current)"
  assert_contains "Branch helper status: \`blocked_dirty_worktree\`" "${workpad_log}"
  assert_contains "target=Blocked" "${state_log}"

  rm -rf "${temp_root}"
}

test_unexpected_branch_blocks_without_switching() {
  local temp_root repo context helper_dir output workpad_log state_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-123"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"

  make_repo "${repo}"
  git -C "${repo}" switch -c unrelated-work >/dev/null
  write_context_json "${context}" "Todo"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    > "${output}"

  assert_contains "TODO_LANE_STATUS=blocked" "${output}"
  assert_contains "TODO_LANE_TO_STATE=Blocked" "${output}"
  assert_contains "TODO_LANE_REASON=blocked_unexpected_branch" "${output}"
  assert_equals "unrelated-work" "$(git -C "${repo}" branch --show-current)"
  assert_contains "Branch helper status: \`blocked_unexpected_branch\`" "${workpad_log}"
  assert_contains "target=Blocked" "${state_log}"

  rm -rf "${temp_root}"
}

test_non_todo_state_is_noop() {
  local temp_root repo context helper_dir output workpad_log state_log branch_log branch_helper
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-123"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"
  branch_log="${temp_root}/branch.log"
  branch_helper="${helper_dir}/branch.sh"

  make_repo "${repo}"
  write_context_json "${context}" "In Progress"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}"

  cat > "${branch_helper}" <<'EOF'
#!/usr/bin/env bash
echo called > "${SYMPHONY_TEST_BRANCH_LOG:?}"
EOF
  chmod +x "${branch_helper}"
  export SYMPHONY_TEST_BRANCH_LOG="${branch_log}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --branch-helper "${branch_helper}" \
    > "${output}"

  assert_contains "TODO_LANE_STATUS=noop" "${output}"
  assert_contains "TODO_LANE_TO_STATE=In Progress" "${output}"
  [[ ! -f "${branch_log}" ]]
  [[ ! -f "${workpad_log}" ]]
  [[ ! -f "${state_log}" ]]

  rm -rf "${temp_root}"
}

test_clean_todo_creates_branch_and_moves_to_in_progress
test_dirty_todo_blocks_without_switching
test_dirty_todo_blocks_when_already_on_issue_branch
test_unexpected_branch_blocks_without_switching
test_non_todo_state_is_noop

echo "symphony_todo_lane tests passed"
