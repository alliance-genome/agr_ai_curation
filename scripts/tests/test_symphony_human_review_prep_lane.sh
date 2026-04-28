#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_human_review_prep_lane.sh"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/assertions.sh"

make_repo() {
  local repo_dir="$1"

  git init -b main "${repo_dir}" >/dev/null
  git -C "${repo_dir}" config user.name "Symphony Test"
  git -C "${repo_dir}" config user.email "symphony@example.com"
  printf 'seed\n' > "${repo_dir}/README.md"
  printf '{}\n' > "${repo_dir}/docker-compose.yml"
  git -C "${repo_dir}" add README.md docker-compose.yml
  git -C "${repo_dir}" commit -m "seed" >/dev/null
}

write_context_json() {
  local path="$1"
  local description="${2:-}"
  local label="${3:-}"
  local state="${4:-Human Review Prep}"

  jq -cn \
    --arg description "${description}" \
    --arg label "${label}" \
    --arg state "${state}" '
    {
      issue: {
        id: "issue-all-49",
        identifier: "ALL-49",
        title: "Human Review Prep lane",
        description: $description,
        state: {name: $state}
      },
      labels: (if $label == "" then [] else [{name: $label}] end),
      comments: [],
      workpad_comment: null,
      latest_non_workpad_comment: {
        id: "comment-human",
        body: "Please do a thing",
        updated_at: "2026-04-28T00:00:00Z",
        user_name: "Chris"
      },
      team: {
        states: [
          {id: "state-human-review-prep", name: "Human Review Prep"},
          {id: "state-human-review", name: "Human Review"},
          {id: "state-in-progress", name: "In Progress"}
        ]
      }
    }' > "${path}"
}

write_stub_helpers() {
  local helper_dir="$1"
  local workpad_log="$2"
  local state_log="$3"
  local prep_log="$4"
  local prep_body_file="$5"

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
while [[ $# -gt 0 ]]; do
  case "$1" in
    --state)
      target_state="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done
printf '%s\n' "${target_state}" > "${SYMPHONY_TEST_STATE_LOG:?}"
echo "LINEAR_STATE_STATUS=ok"
EOF

  cat > "${helper_dir}/prep.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "${SYMPHONY_TEST_PREP_LOG:?}"
cat "${SYMPHONY_TEST_PREP_BODY_FILE:?}"
exit "${SYMPHONY_TEST_PREP_EXIT_CODE:-0}"
EOF

  chmod +x "${helper_dir}/workpad.sh" "${helper_dir}/state.sh" "${helper_dir}/prep.sh"
  export SYMPHONY_TEST_WORKPAD_LOG="${workpad_log}"
  export SYMPHONY_TEST_STATE_LOG="${state_log}"
  export SYMPHONY_TEST_PREP_LOG="${prep_log}"
  export SYMPHONY_TEST_PREP_BODY_FILE="${prep_body_file}"
}

test_no_pr_default_skip_moves_to_human_review() {
  local temp_root repo context helper_dir output workpad_log state_log prep_log prep_body
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-49"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"
  prep_log="${temp_root}/prep.args"
  prep_body="${temp_root}/prep.out"

  make_repo "${repo}"
  write_context_json "${context}" "" "workflow:no-pr"
  cat > "${prep_body}" <<'EOF'
human_review_prep_wrapper_status=skipped
human_review_prep_wrapper_reason=start_test_containers_false
start_test_containers=0
stack_startup=skipped_by_flag
dependency_start_status=skipped_by_flag
frontend_health=skipped_by_flag
backend_health=skipped_by_flag
curation_db_health=skipped_by_flag
pdf_extraction_health=skipped_by_flag
EOF
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${prep_log}" "${prep_body}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-49 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --prep-helper "${helper_dir}/prep.sh" \
    > "${output}"

  assert_contains "HUMAN_REVIEW_PREP_STATUS=ready" "${output}"
  assert_contains "HUMAN_REVIEW_PREP_TO_STATE=Human Review" "${output}"
  assert_contains "Human Review" "${state_log}"
  assert_contains 'Delivery mode: `no_pr`' "${workpad_log}"
  assert_contains 'stack_startup: `skipped_by_flag`' "${workpad_log}"
  assert_contains "Local stack startup was intentionally skipped" "${workpad_log}"

  rm -rf "${temp_root}"
}

test_pr_mode_does_not_call_github_or_claude() {
  local temp_root repo context helper_dir output workpad_log state_log prep_log prep_body fake_bin
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-49"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"
  prep_log="${temp_root}/prep.args"
  prep_body="${temp_root}/prep.out"
  fake_bin="${temp_root}/bin"

  make_repo "${repo}"
  write_context_json "${context}" "" ""
  cat > "${prep_body}" <<'EOF'
human_review_prep_wrapper_status=skipped
human_review_prep_wrapper_reason=start_test_containers_false
start_test_containers=0
stack_startup=skipped_by_flag
EOF
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${prep_log}" "${prep_body}"
  mkdir -p "${fake_bin}"
  cat > "${fake_bin}/gh" <<'EOF'
#!/usr/bin/env bash
echo "gh should not be called" >&2
exit 99
EOF
  chmod +x "${fake_bin}/gh"

  PATH="${fake_bin}:${PATH}" bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-49 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --prep-helper "${helper_dir}/prep.sh" \
    > "${output}"

  assert_contains "HUMAN_REVIEW_PREP_STATUS=ready" "${output}"
  assert_contains 'Delivery mode: `pr`' "${workpad_log}"
  assert_not_contains "Claude" "${workpad_log}"
  assert_not_contains "PR Handoff" "${workpad_log}"

  rm -rf "${temp_root}"
}

test_dirty_workspace_routes_to_in_progress_without_prep() {
  local temp_root repo context helper_dir output workpad_log state_log prep_log prep_body
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-49"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"
  prep_log="${temp_root}/prep.args"
  prep_body="${temp_root}/prep.out"

  make_repo "${repo}"
  printf 'dirty\n' >> "${repo}/README.md"
  write_context_json "${context}" "" ""
  : > "${prep_body}"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${prep_log}" "${prep_body}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-49 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --prep-helper "${helper_dir}/prep.sh" \
    > "${output}"

  assert_contains "HUMAN_REVIEW_PREP_STATUS=returned_to_in_progress" "${output}"
  assert_contains "In Progress" "${state_log}"
  assert_contains "did not run local prep" "${workpad_log}"
  if [[ -s "${prep_log}" ]]; then
    echo "Prep helper should not be called for a dirty workspace" >&2
    exit 1
  fi

  rm -rf "${temp_root}"
}

test_start_test_containers_token_is_forwarded() {
  local temp_root repo context helper_dir output workpad_log state_log prep_log prep_body
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-49"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"
  prep_log="${temp_root}/prep.args"
  prep_body="${temp_root}/prep.out"

  make_repo "${repo}"
  write_context_json "${context}" "Please run start_test_containers=true for this one." ""
  cat > "${prep_body}" <<'EOF'
human_review_prep_wrapper_status=ready
human_review_prep_wrapper_reason=healthy
start_test_containers=1
stack_startup=started
frontend_health=healthy
backend_health={"status":"healthy"}
review_frontend_url=http://192.168.86.44:3049/
review_backend_url=http://192.168.86.44:8049/health
EOF
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${prep_log}" "${prep_body}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-49 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --prep-helper "${helper_dir}/prep.sh" \
    > "${output}"

  assert_contains "start-test-containers" "${prep_log}"
  assert_contains "true" "${prep_log}"
  assert_contains "Frontend: http://192.168.86.44:3049/" "${workpad_log}"
  assert_contains "HUMAN_REVIEW_PREP_STATUS=ready" "${output}"

  rm -rf "${temp_root}"
}

test_partial_prep_still_moves_to_human_review_and_redacts() {
  local temp_root repo context helper_dir output workpad_log state_log prep_log prep_body
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-49"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"
  prep_log="${temp_root}/prep.args"
  prep_body="${temp_root}/prep.out"

  make_repo "${repo}"
  write_context_json "${context}" "start_test_containers=true" ""
  _root_cause_dsn="postgresql://""user:secret@example.test/db"
  cat > "${prep_body}" <<EOF
human_review_prep_wrapper_status=partial
human_review_prep_wrapper_reason=backend_unreachable
start_test_containers=1
stack_startup=started
frontend_health=healthy
backend_health=unreachable
backend_root_cause=${_root_cause_dsn} token=abc123 Authorization: Bearer abc123 api_key: xyz secret: shh
api_token=abc123
EOF
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${prep_log}" "${prep_body}"
  export SYMPHONY_TEST_PREP_EXIT_CODE=1

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-49 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --prep-helper "${helper_dir}/prep.sh" \
    > "${output}"

  assert_contains "HUMAN_REVIEW_PREP_STATUS=ready" "${output}"
  assert_contains "HUMAN_REVIEW_PREP_PREP_EXIT_CODE=1" "${output}"
  assert_contains "Human Review" "${state_log}"
  assert_contains "Local prep is partial" "${workpad_log}"
  assert_contains "postgresql://REDACTED@example.test/db token=REDACTED authorization: REDACTED api_key: REDACTED secret: REDACTED" "${workpad_log}"
  assert_not_contains "api_token=abc123" "${workpad_log}"
  assert_not_contains "Bearer abc123" "${workpad_log}"
  assert_not_contains "api_key: xyz" "${workpad_log}"

  unset SYMPHONY_TEST_PREP_EXIT_CODE
  rm -rf "${temp_root}"
}

test_unstructured_prep_failure_exits_nonzero_without_transition() {
  local temp_root repo context helper_dir output workpad_log state_log prep_log prep_body status
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-49"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"
  prep_log="${temp_root}/prep.args"
  prep_body="${temp_root}/prep.out"

  make_repo "${repo}"
  write_context_json "${context}" "" ""
  cat > "${prep_body}" <<'EOF'
Workspace is missing docker-compose.yml: /tmp/nope/docker-compose.yml
EOF
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${prep_log}" "${prep_body}"
  export SYMPHONY_TEST_PREP_EXIT_CODE=2

  set +e
  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-49 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --prep-helper "${helper_dir}/prep.sh" \
    > "${output}" 2>&1
  status=$?
  set -e

  [[ "${status}" -ne 0 ]] || {
    echo "Expected unstructured prep failure to exit non-zero" >&2
    exit 1
  }
  assert_contains "did not emit required" "${output}"
  if [[ -f "${state_log}" ]]; then
    echo "State helper should not be called for unstructured prep failure" >&2
    exit 1
  fi
  if [[ -f "${workpad_log}" ]]; then
    echo "Workpad helper should not be called for unstructured prep failure" >&2
    exit 1
  fi

  unset SYMPHONY_TEST_PREP_EXIT_CODE
  rm -rf "${temp_root}"
}

test_state_changed_before_prep_is_noop() {
  local temp_root repo context helper_dir output workpad_log state_log prep_log prep_body
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-49"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"
  prep_log="${temp_root}/prep.args"
  prep_body="${temp_root}/prep.out"

  make_repo "${repo}"
  write_context_json "${context}" "" "" "Human Review"
  : > "${prep_body}"
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${prep_log}" "${prep_body}"

  bash "${SCRIPT_PATH}" \
    --issue-identifier ALL-49 \
    --context-json-file "${context}" \
    --workspace-dir "${repo}" \
    --workpad-helper "${helper_dir}/workpad.sh" \
    --state-helper "${helper_dir}/state.sh" \
    --prep-helper "${helper_dir}/prep.sh" \
    > "${output}"

  assert_contains "HUMAN_REVIEW_PREP_STATUS=noop" "${output}"
  assert_contains "HUMAN_REVIEW_PREP_REASON=state_changed_before_prep" "${output}"
  if [[ -f "${state_log}" || -f "${workpad_log}" || -f "${prep_log}" ]]; then
    echo "No helpers should be called after an early state-change noop" >&2
    exit 1
  fi

  rm -rf "${temp_root}"
}

test_runtime_noise_does_not_fail_no_code_guard() {
  local temp_root repo context helper_dir output workpad_log state_log prep_log prep_body
  temp_root="$(mktemp -d)"
  repo="${temp_root}/ALL-49"
  context="${temp_root}/context.json"
  helper_dir="${temp_root}/helpers"
  output="${temp_root}/output.txt"
  workpad_log="${temp_root}/workpad.md"
  state_log="${temp_root}/state.txt"
  prep_log="${temp_root}/prep.args"
  prep_body="${temp_root}/prep.out"

  make_repo "${repo}"
  write_context_json "${context}" "" ""
  cat > "${prep_body}" <<'EOF'
human_review_prep_wrapper_status=skipped
human_review_prep_wrapper_reason=start_test_containers_false
start_test_containers=0
stack_startup=skipped_by_flag
EOF
  write_stub_helpers "${helper_dir}" "${workpad_log}" "${state_log}" "${prep_log}" "${prep_body}"
  cat > "${helper_dir}/prep.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "${SYMPHONY_TEST_PREP_LOG:?}"
mkdir -p .symphony .symphony-docker-config scripts
printf 'workflow\n' > .symphony/WORKFLOW.md
printf '{}\n' > .symphony-docker-config/config.json
printf 'export TUNNEL=1\n' > scripts/local_db_tunnel_env.sh
cat "${SYMPHONY_TEST_PREP_BODY_FILE:?}"
EOF
  chmod +x "${helper_dir}/prep.sh"

  (
    cd "${repo}"
    bash "${SCRIPT_PATH}" \
      --issue-identifier ALL-49 \
      --context-json-file "${context}" \
      --workspace-dir "${repo}" \
      --workpad-helper "${helper_dir}/workpad.sh" \
      --state-helper "${helper_dir}/state.sh" \
      --prep-helper "${helper_dir}/prep.sh" \
      > "${output}"
  )

  assert_contains "HUMAN_REVIEW_PREP_STATUS=ready" "${output}"
  assert_contains "Human Review" "${state_log}"

  rm -rf "${temp_root}"
}

test_no_pr_default_skip_moves_to_human_review
test_pr_mode_does_not_call_github_or_claude
test_dirty_workspace_routes_to_in_progress_without_prep
test_start_test_containers_token_is_forwarded
test_partial_prep_still_moves_to_human_review_and_redacts
test_unstructured_prep_failure_exits_nonzero_without_transition
test_state_changed_before_prep_is_noop
test_runtime_noise_does_not_fail_no_code_guard

echo "symphony_human_review_prep_lane tests passed"
