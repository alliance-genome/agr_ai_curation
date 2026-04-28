#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_finalizing_lane.sh"

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
  local state="${2:-Finalizing}"
  local delivery_label="${3:-}"

  jq -cn \
    --arg state "${state}" \
    --arg delivery_label "${delivery_label}" '
    {
      issue: {
        id: "issue-all-123",
        identifier: "ALL-123",
        title: "Script Finalizing lane",
        description: "Description.",
        url: "https://linear.example/ALL-123",
        state: {name: $state},
        labels: (if $delivery_label == "" then [] else [{id: "label-no-pr", name: $delivery_label, color: "#888888"}] end)
      },
      comments: [],
      workpad_comment: null,
      latest_non_workpad_comment: null,
      team: {
        states: [
          {id: "state-finalizing", name: "Finalizing"},
          {id: "state-progress", name: "In Progress"},
          {id: "state-blocked", name: "Blocked"},
          {id: "state-done", name: "Done"}
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

test_successful_finalization_moves_to_done() {
  local temp_root repo context helper_dir finalizer output workpad_log state_log section_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/repo"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  finalizer="${helper_dir}/finalize.sh"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.log"
  state_log="${temp_root}/state.log"
  section_log="${temp_root}/section.md"

  make_repo "${repo}"
  write_context_json "${context}" "Finalizing" "workflow:no-pr"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${section_log}"

  cat > "${finalizer}" <<'EOF'
#!/usr/bin/env bash
expected_delivery_mode=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --delivery-mode)
      expected_delivery_mode="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
if [[ "${expected_delivery_mode}" != "no_pr" ]]; then
  echo "Expected delivery mode no_pr, got ${expected_delivery_mode}" >&2
  exit 1
fi
cat <<'OUT'
FINALIZE_STATUS=finalized_no_pr
FINALIZE_NEXT_STATE=Done
FINALIZE_DELIVERY_MODE=no_pr
FINALIZE_BRANCH=all-123
FINALIZE_PRE_CLEANUP_STATUS=success
FINALIZE_POST_CLEANUP_STATUS=success
FINALIZE_WORKSPACE_REMOVAL=deferred_to_terminal_cleanup
FINALIZE_MERGE_STATUS=skipped_no_pr
FINALIZE_MESSAGE=No PR merge required.
OUT
EOF
  chmod +x "${finalizer}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --finalize-helper "${finalizer}" \
    > "${output}"

  assert_contains "FINALIZING_LANE_STATUS=done" "${output}"
  assert_contains "FINALIZING_LANE_TO_STATE=Done" "${output}"
  assert_contains "target=Done" "${state_log}"
  assert_contains "from=Finalizing" "${state_log}"
  assert_contains "FINALIZE_STATUS" "${section_log}"
  assert_contains "finalized_no_pr" "${section_log}"

  rm -rf "${temp_root}"
}

test_merge_conflict_returns_to_in_progress() {
  local temp_root repo context helper_dir finalizer output workpad_log state_log section_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/repo"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  finalizer="${helper_dir}/finalize.sh"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.log"
  state_log="${temp_root}/state.log"
  section_log="${temp_root}/section.md"

  make_repo "${repo}"
  write_context_json "${context}"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${section_log}"

  cat > "${finalizer}" <<'EOF'
#!/usr/bin/env bash
cat <<'OUT'
FINALIZE_STATUS=merge_conflict
FINALIZE_NEXT_STATE=In Progress
FINALIZE_DELIVERY_MODE=pr
FINALIZE_BRANCH=all-123
FINALIZE_PR_NUMBER=123
FINALIZE_MERGE_OUTPUT=merge conflict
CONFLICT_FILES=README.md
FINALIZE_MESSAGE=PR has merge conflicts.
OUT
exit 22
EOF
  chmod +x "${finalizer}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --finalize-helper "${finalizer}" \
    > "${output}"

  assert_contains "FINALIZING_LANE_STATUS=returned_to_in_progress" "${output}"
  assert_contains "FINALIZING_LANE_TO_STATE=In Progress" "${output}"
  assert_contains "target=In Progress" "${state_log}"
  assert_contains "CONFLICT_FILES" "${section_log}"

  rm -rf "${temp_root}"
}

test_missing_pr_moves_to_blocked() {
  local temp_root repo context helper_dir finalizer output workpad_log state_log section_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/repo"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  finalizer="${helper_dir}/finalize.sh"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.log"
  state_log="${temp_root}/state.log"
  section_log="${temp_root}/section.md"

  make_repo "${repo}"
  write_context_json "${context}"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${section_log}"

  cat > "${finalizer}" <<'EOF'
#!/usr/bin/env bash
cat <<'OUT'
FINALIZE_STATUS=blocked_missing_pr
FINALIZE_NEXT_STATE=Blocked
FINALIZE_DELIVERY_MODE=pr
FINALIZE_BRANCH=all-123
FINALIZE_MESSAGE=No open PR found.
OUT
exit 20
EOF
  chmod +x "${finalizer}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --finalize-helper "${finalizer}" \
    > "${output}"

  assert_contains "FINALIZING_LANE_STATUS=blocked" "${output}"
  assert_contains "FINALIZING_LANE_TO_STATE=Blocked" "${output}"
  assert_contains "target=Blocked" "${state_log}"
  assert_contains "blocked_missing_pr" "${section_log}"

  rm -rf "${temp_root}"
}

test_non_finalizing_state_noops() {
  local temp_root repo context helper_dir finalizer output workpad_log state_log section_log
  temp_root="$(mktemp -d)"
  repo="${temp_root}/repo"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  finalizer="${helper_dir}/finalize.sh"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.log"
  state_log="${temp_root}/state.log"
  section_log="${temp_root}/section.md"

  make_repo "${repo}"
  write_context_json "${context}" "Done"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${section_log}"

  cat > "${finalizer}" <<'EOF'
#!/usr/bin/env bash
echo "finalizer should not run" >&2
exit 1
EOF
  chmod +x "${finalizer}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-123 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --finalize-helper "${finalizer}" \
    > "${output}"

  assert_contains "FINALIZING_LANE_STATUS=noop" "${output}"
  assert_contains "FINALIZING_LANE_TO_STATE=Done" "${output}"
  [[ ! -f "${state_log}" ]]
  [[ ! -f "${section_log}" ]]

  rm -rf "${temp_root}"
}

test_successful_finalization_moves_to_done
test_merge_conflict_returns_to_in_progress
test_missing_pr_moves_to_blocked
test_non_finalizing_state_noops

echo "symphony_finalizing_lane tests passed"
