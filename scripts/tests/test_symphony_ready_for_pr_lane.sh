#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_ready_for_pr_lane.sh"
GUARD_PATH="${REPO_ROOT}/scripts/utilities/symphony_guard_no_code_changes.sh"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/assertions.sh"

make_repo() {
  local repo_dir="$1"

  git init -b main "${repo_dir}" >/dev/null
  git -C "${repo_dir}" config user.name "Symphony Test"
  git -C "${repo_dir}" config user.email "symphony@example.com"
  printf 'seed\n' > "${repo_dir}/README.md"
  git -C "${repo_dir}" add README.md
  git -C "${repo_dir}" commit -m "seed" >/dev/null
  git -C "${repo_dir}" switch -c all-123 >/dev/null
}

write_context_json() {
  local path="$1"
  local delivery_label="${2:-}"

  jq -cn \
    --arg delivery_label "${delivery_label}" '
    {
      issue: {
        id: "issue-all-123",
        identifier: "ALL-123",
        title: "Script Ready for PR lane",
        description: "Description.",
        url: "https://linear.example/ALL-123",
        state: {name: "Ready for PR"},
        labels: (if $delivery_label == "" then [] else [{id: "label-no-pr", name: $delivery_label, color: "#888888"}] end)
      },
      comments: [],
      workpad_comment: null,
      latest_non_workpad_comment: null,
      team: {
        states: [
          {id: "state-ready", name: "Ready for PR"},
          {id: "state-progress", name: "In Progress"},
          {id: "state-hrp", name: "Human Review Prep"},
          {id: "state-blocked", name: "Blocked"}
        ]
      }
    }' > "${path}"
}

write_stub_helpers() {
  local helper_dir="$1"
  local workpad_log="$2"
  local state_log="$3"
  local section_log="$4"

  mkdir -p "${helper_dir}"

  cat > "${helper_dir}/workpad.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "show" ]]; then
  echo "WORKPAD_STATUS=missing"
  exit 0
fi
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
printf '%s\n' "$*" > "${SYMPHONY_TEST_WORKPAD_LOG:?}"
cat "${section_file}" > "${SYMPHONY_TEST_SECTION_LOG:?}"
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
  export SYMPHONY_TEST_SECTION_LOG="${section_log}"
}

test_clean_pr_moves_to_human_review_prep() {
  local temp_root repo context helper_dir ready_helper output workpad_log state_log section_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/repo"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  ready_helper="${helper_dir}/ready.sh"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.log"
  state_log="${temp_root}/state.log"
  section_log="${temp_root}/section.md"

  make_repo "${repo}"
  write_context_json "${context}"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${section_log}"

  cat > "${ready_helper}" <<'EOF'
#!/usr/bin/env bash
cat <<'OUT'
READY_FOR_PR_STATUS=existing_pr
READY_FOR_PR_NEXT_STATE=Ready for PR
READY_FOR_PR_BRANCH=all-123
READY_FOR_PR_PR_NUMBER=123
READY_FOR_PR_PR_URL=https://example.test/pr/123
READY_FOR_PR_CHECK_STATUS=clean
READY_FOR_PR_CLAUDE_STATUS=quiet
READY_FOR_PR_INSTRUCTIONS=Clean PR.
OUT
EOF
  chmod +x "${ready_helper}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --ready-helper "${ready_helper}" \
    --guard-helper "${GUARD_PATH}" \
    --wait-for-review-seconds 0 \
    --wait-for-checks-seconds 0 \
    > "${output}"

  assert_contains "READY_FOR_PR_LANE_STATUS=ready" "${output}"
  assert_contains "READY_FOR_PR_LANE_TO_STATE=Human Review Prep" "${output}"
  assert_contains "target=Human Review Prep" "${state_log}"
  assert_contains "from=Ready for PR" "${state_log}"
  assert_contains "PR gate is clean" "${section_log}"

  rm -rf "${temp_root}"
}

test_helper_auto_bounce_does_not_write_second_transition() {
  local temp_root repo context helper_dir ready_helper output workpad_log state_log section_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/repo"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  ready_helper="${helper_dir}/ready.sh"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.log"
  state_log="${temp_root}/state.log"
  section_log="${temp_root}/section.md"

  make_repo "${repo}"
  write_context_json "${context}"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${section_log}"

  cat > "${ready_helper}" <<'EOF'
#!/usr/bin/env bash
cat <<'OUT'
READY_FOR_PR_STATUS=existing_pr
READY_FOR_PR_NEXT_STATE=In Progress
READY_FOR_PR_CHECK_STATUS=failed
READY_FOR_PR_CHECK_ACTION=bounced_to_in_progress
OUT
EOF
  chmod +x "${ready_helper}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --ready-helper "${ready_helper}" \
    --guard-helper "${GUARD_PATH}" \
    --wait-for-review-seconds 0 \
    --wait-for-checks-seconds 0 \
    > "${output}"

  assert_contains "READY_FOR_PR_LANE_STATUS=bounced_to_in_progress" "${output}"
  [[ ! -f "${state_log}" ]]
  [[ ! -f "${section_log}" ]]

  rm -rf "${temp_root}"
}

test_pending_gate_stays_ready_for_pr_without_transition() {
  local temp_root repo context helper_dir ready_helper output workpad_log state_log section_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/repo"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  ready_helper="${helper_dir}/ready.sh"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.log"
  state_log="${temp_root}/state.log"
  section_log="${temp_root}/section.md"

  make_repo "${repo}"
  write_context_json "${context}"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${section_log}"

  cat > "${ready_helper}" <<'EOF'
#!/usr/bin/env bash
cat <<'OUT'
READY_FOR_PR_STATUS=existing_pr
READY_FOR_PR_NEXT_STATE=Ready for PR
READY_FOR_PR_CHECK_STATUS=clean
READY_FOR_PR_CLAUDE_STATUS=pending
READY_FOR_PR_INSTRUCTIONS=Claude is pending.
OUT
exit 11
EOF
  chmod +x "${ready_helper}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --ready-helper "${ready_helper}" \
    --guard-helper "${GUARD_PATH}" \
    --wait-for-review-seconds 0 \
    --wait-for-checks-seconds 0 \
    > "${output}"

  assert_contains "READY_FOR_PR_LANE_STATUS=waiting" "${output}"
  assert_contains "READY_FOR_PR_LANE_TO_STATE=Ready for PR" "${output}"
  assert_contains "PR gate is still waiting" "${section_log}"
  [[ ! -f "${state_log}" ]]

  rm -rf "${temp_root}"
}

test_no_pr_label_moves_to_human_review_prep() {
  local temp_root repo context helper_dir ready_helper output workpad_log state_log section_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/repo"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  ready_helper="${helper_dir}/ready.sh"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.log"
  state_log="${temp_root}/state.log"
  section_log="${temp_root}/section.md"

  make_repo "${repo}"
  write_context_json "${context}" "workflow:no-pr"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${section_log}"

  cat > "${ready_helper}" <<'EOF'
#!/usr/bin/env bash
delivery_mode=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --delivery-mode)
      delivery_mode="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
if [[ "${delivery_mode}" != "no_pr" ]]; then
  echo "Expected delivery mode no_pr, got ${delivery_mode}" >&2
  exit 1
fi
cat <<'OUT'
READY_FOR_PR_STATUS=skip_no_pr
READY_FOR_PR_NEXT_STATE=Human Review Prep
READY_FOR_PR_CHECK_STATUS=skipped
READY_FOR_PR_CLAUDE_STATUS=skipped
OUT
EOF
  chmod +x "${ready_helper}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --ready-helper "${ready_helper}" \
    --guard-helper "${GUARD_PATH}" \
    --wait-for-review-seconds 0 \
    --wait-for-checks-seconds 0 \
    > "${output}"

  assert_contains "READY_FOR_PR_LANE_STATUS=ready" "${output}"
  assert_contains "READY_FOR_PR_LANE_TO_STATE=Human Review Prep" "${output}"
  assert_contains "READY_FOR_PR_LANE_REASON=workflow_no_pr" "${output}"
  assert_contains "target=Human Review Prep" "${state_log}"

  rm -rf "${temp_root}"
}

test_dirty_workspace_at_entry_returns_to_in_progress() {
  local temp_root repo context helper_dir ready_helper output workpad_log state_log section_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/repo"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  ready_helper="${helper_dir}/ready.sh"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.log"
  state_log="${temp_root}/state.log"
  section_log="${temp_root}/section.md"

  make_repo "${repo}"
  write_context_json "${context}"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${section_log}"

  cat > "${ready_helper}" <<'EOF'
#!/usr/bin/env bash
echo "ready helper should not run" >&2
exit 99
EOF
  chmod +x "${ready_helper}"
  printf 'dirty before lane\n' >> "${repo}/README.md"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --ready-helper "${ready_helper}" \
    --guard-helper "${GUARD_PATH}" \
    --wait-for-review-seconds 0 \
    --wait-for-checks-seconds 0 \
    > "${output}"

  assert_contains "READY_FOR_PR_LANE_STATUS=returned_to_in_progress" "${output}"
  assert_contains "READY_FOR_PR_LANE_REASON=workspace_dirty_at_entry" "${output}"
  assert_contains "target=In Progress" "${state_log}"
  assert_contains "workspace_dirty_at_entry" "${section_log}"

  rm -rf "${temp_root}"
}

test_dirty_workspace_after_helper_returns_to_in_progress() {
  local temp_root repo context helper_dir ready_helper output workpad_log state_log section_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/repo"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  ready_helper="${helper_dir}/ready.sh"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.log"
  state_log="${temp_root}/state.log"
  section_log="${temp_root}/section.md"

  make_repo "${repo}"
  write_context_json "${context}"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${section_log}"

  cat > "${ready_helper}" <<'EOF'
#!/usr/bin/env bash
printf 'dirty during helper\n' >> README.md
cat <<'OUT'
READY_FOR_PR_STATUS=existing_pr
READY_FOR_PR_NEXT_STATE=Ready for PR
READY_FOR_PR_BRANCH=all-123
READY_FOR_PR_PR_NUMBER=123
READY_FOR_PR_PR_URL=https://example.test/pr/123
READY_FOR_PR_CHECK_STATUS=clean
READY_FOR_PR_CLAUDE_STATUS=quiet
READY_FOR_PR_INSTRUCTIONS=Clean PR.
OUT
EOF
  chmod +x "${ready_helper}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --ready-helper "${ready_helper}" \
    --guard-helper "${GUARD_PATH}" \
    --wait-for-review-seconds 0 \
    --wait-for-checks-seconds 0 \
    > "${output}"

  assert_contains "READY_FOR_PR_LANE_STATUS=returned_to_in_progress" "${output}"
  assert_contains "READY_FOR_PR_LANE_REASON=workspace_dirty_after_ready_for_pr" "${output}"
  assert_contains "target=In Progress" "${state_log}"
  assert_contains "workspace_dirty_after_ready_for_pr" "${section_log}"

  rm -rf "${temp_root}"
}

test_clean_pr_moves_to_human_review_prep
test_helper_auto_bounce_does_not_write_second_transition
test_pending_gate_stays_ready_for_pr_without_transition
test_no_pr_label_moves_to_human_review_prep
test_dirty_workspace_at_entry_returns_to_in_progress
test_dirty_workspace_after_helper_returns_to_in_progress

echo "symphony_ready_for_pr_lane tests passed"
