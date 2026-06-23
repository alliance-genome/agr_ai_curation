#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${REPO_ROOT}/scripts/utilities/symphony_classify_pr_feedback.sh"

assert_contains() {
  local expected="$1"
  local actual="$2"
  if [[ "${actual}" != *"${expected}"* ]]; then
    echo "Expected output to contain '${expected}'" >&2
    printf 'Actual output:\n%s\n' "${actual}" >&2
    exit 1
  fi
}

run_fixture() {
  local classification="$1"
  local expected_rc="$2"
  local temp_dir report fixture output_file rc output
  temp_dir="$(mktemp -d)"
  report="${temp_dir}/report.md"
  fixture="${temp_dir}/fixture.json"
  output_file="${temp_dir}/output.txt"

  printf 'Claude review report\n' > "${report}"
  if [[ "${classification}" == "clean" ]]; then
    cat > "${fixture}" <<EOF
{"classification":"${classification}","reason":"fixture reason","action_items":[]}
EOF
  else
    cat > "${fixture}" <<EOF
{"classification":"${classification}","reason":"fixture reason","action_items":["fixture item"]}
EOF
  fi

  set +e
  bash "${SCRIPT_PATH}" \
    --report-file "${report}" \
    --fixture-output-file "${fixture}" \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"
  [[ "${rc}" == "${expected_rc}" ]] || {
    echo "Expected exit code ${expected_rc}, got ${rc}" >&2
    printf 'Actual output:\n%s\n' "${output}" >&2
    exit 1
  }
  assert_contains "PR_FEEDBACK_CLASSIFIER_STATUS=${classification}" "${output}"
  assert_contains "PR_FEEDBACK_CLASSIFIER_REASON=fixture reason" "${output}"
}

test_fixture_classifications_map_to_exit_codes() {
  run_fixture clean 0
  run_fixture actionable 10
  run_fixture uncertain 11
}

test_invalid_json_is_error() {
  local temp_dir report fixture output_file rc output
  temp_dir="$(mktemp -d)"
  report="${temp_dir}/report.md"
  fixture="${temp_dir}/fixture.json"
  output_file="${temp_dir}/output.txt"

  printf 'Claude review report\n' > "${report}"
  printf 'not json\n' > "${fixture}"

  set +e
  bash "${SCRIPT_PATH}" \
    --report-file "${report}" \
    --fixture-output-file "${fixture}" \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"
  [[ "${rc}" == "2" ]] || {
    echo "Expected exit code 2, got ${rc}" >&2
    printf 'Actual output:\n%s\n' "${output}" >&2
    exit 1
  }
  assert_contains "PR_FEEDBACK_CLASSIFIER_STATUS=error" "${output}"
  assert_contains "Invalid classifier JSON" "${output}"
}

test_clean_with_action_items_is_uncertain() {
  local temp_dir report fixture output_file rc output
  temp_dir="$(mktemp -d)"
  report="${temp_dir}/report.md"
  fixture="${temp_dir}/fixture.json"
  output_file="${temp_dir}/output.txt"

  printf 'Claude review report\n' > "${report}"
  cat > "${fixture}" <<'EOF'
{"classification":"clean","reason":"looks ok but please add coverage","action_items":["add coverage"]}
EOF

  set +e
  bash "${SCRIPT_PATH}" \
    --report-file "${report}" \
    --fixture-output-file "${fixture}" \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"
  [[ "${rc}" == "11" ]] || {
    echo "Expected exit code 11, got ${rc}" >&2
    printf 'Actual output:\n%s\n' "${output}" >&2
    exit 1
  }
  assert_contains "PR_FEEDBACK_CLASSIFIER_STATUS=uncertain" "${output}"
  assert_contains "PR_FEEDBACK_CLASSIFIER_ACTION_ITEM_1=add coverage" "${output}"
}

test_prompt_guides_ci_verification_only_as_clean_pr_gate() {
  local temp_dir report codex_stub prompt_capture output_file rc output prompt
  temp_dir="$(mktemp -d)"
  report="${temp_dir}/report.md"
  codex_stub="${temp_dir}/codex"
  prompt_capture="${temp_dir}/prompt.md"
  output_file="${temp_dir}/output.txt"

  cat > "${report}" <<'EOF'
# Claude Code Review Report

### BLOCKING Issues

None.

### Non-blocking Notes

- Confirm the four new tests pass in CI before merge.

### Assessment

Approve once CI is green.
EOF
  cat > "${codex_stub}" <<EOF
#!/usr/bin/env bash
output_file=""
while [[ \$# -gt 0 ]]; do
  if [[ "\$1" == "-o" ]]; then
    output_file="\${2:-}"
    shift 2
    continue
  fi
  shift
done
cat > "${prompt_capture}"
cat > "\${output_file}" <<'JSON'
{"classification":"clean","reason":"Only CI/check verification remains; Ready for PR handles GitHub checks separately.","action_items":[]}
JSON
EOF
  chmod +x "${codex_stub}"

  set +e
  bash "${SCRIPT_PATH}" \
    --report-file "${report}" \
    --codex-bin "${codex_stub}" \
    --github-check-status clean \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"
  [[ "${rc}" == "0" ]] || {
    echo "Expected exit code 0, got ${rc}" >&2
    printf 'Actual output:\n%s\n' "${output}" >&2
    exit 1
  }
  assert_contains "PR_FEEDBACK_CLASSIFIER_STATUS=clean" "${output}"
  assert_contains "PR_FEEDBACK_CLASSIFIER_GITHUB_CHECK_STATUS=clean" "${output}"
  assert_contains "Only CI/check verification remains" "${output}"

  prompt="$(cat "${prompt_capture}")"
  assert_contains "Deterministic PR gate context:" "${prompt}"
  assert_contains "GitHub check gate status: clean" "${prompt}"
  assert_contains "Shell automation owns PR discovery, merge conflict detection, GitHub check status" "${prompt}"
  assert_contains "This classifier owns only the semantic reading of Claude's review prose" "${prompt}"
  assert_contains "If the GitHub check gate status is clean, do not classify a review as actionable merely because Claude says to wait for, confirm, verify, or ensure CI/checks/tests/builds pass before merge." "${prompt}"
  assert_contains "Treat pure PR-gate language as \"clean\"" "${prompt}"
  assert_contains "If the review also asks for code, tests, docs, config, behavior changes, coverage, or failing-check repair, classify as \"actionable\"" "${prompt}"
}

test_fixture_actionable_ci_only_is_not_rewritten() {
  local temp_dir report fixture output_file rc output
  temp_dir="$(mktemp -d)"
  report="${temp_dir}/report.md"
  fixture="${temp_dir}/fixture.json"
  output_file="${temp_dir}/output.txt"

  cat > "${report}" <<'EOF'
# Claude Code Review Report

### BLOCKING Issues

None.

### Non-blocking Notes

- Confirm the four new tests pass in CI before merge.

### Assessment

Approve once CI is green.
EOF
  cat > "${fixture}" <<'EOF'
{
  "classification": "actionable",
  "reason": "The only follow-up is to confirm CI before merging.",
  "action_items": ["Confirm the four new tests pass in CI before merge."]
}
EOF

  set +e
  bash "${SCRIPT_PATH}" \
    --report-file "${report}" \
    --fixture-output-file "${fixture}" \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"
  [[ "${rc}" == "10" ]] || {
    echo "Expected exit code 10, got ${rc}" >&2
    printf 'Actual output:\n%s\n' "${output}" >&2
    exit 1
  }
  assert_contains "PR_FEEDBACK_CLASSIFIER_STATUS=actionable" "${output}"
  assert_contains "PR_FEEDBACK_CLASSIFIER_ACTION_ITEM_1=Confirm the four new tests pass in CI before merge." "${output}"
}

test_ci_verification_plus_repo_work_still_actionable() {
  local temp_dir report fixture output_file rc output
  temp_dir="$(mktemp -d)"
  report="${temp_dir}/report.md"
  fixture="${temp_dir}/fixture.json"
  output_file="${temp_dir}/output.txt"

  cat > "${report}" <<'EOF'
# Claude Code Review Report

Non-blocking issues:
- Please add a regression test for the retry path and confirm CI passes.
EOF
  cat > "${fixture}" <<'EOF'
{
  "classification": "actionable",
  "reason": "The review asks for a test and CI verification.",
  "action_items": [
    "Add a regression test for the retry path.",
    "Confirm CI passes before merge."
  ]
}
EOF

  set +e
  bash "${SCRIPT_PATH}" \
    --report-file "${report}" \
    --fixture-output-file "${fixture}" \
    > "${output_file}"
  rc=$?
  set -e

  output="$(cat "${output_file}")"
  [[ "${rc}" == "10" ]] || {
    echo "Expected exit code 10, got ${rc}" >&2
    printf 'Actual output:\n%s\n' "${output}" >&2
    exit 1
  }
  assert_contains "PR_FEEDBACK_CLASSIFIER_STATUS=actionable" "${output}"
  assert_contains "PR_FEEDBACK_CLASSIFIER_ACTION_ITEM_1=Add a regression test" "${output}"
}

test_json_override_config_is_used() {
  local temp_dir report fixture overrides output
  temp_dir="$(mktemp -d)"
  report="${temp_dir}/report.md"
  fixture="${temp_dir}/fixture.json"
  overrides="${temp_dir}/codex-overrides.json"

  printf 'Claude review report\n' > "${report}"
  printf '{"classification":"clean","reason":"ok","action_items":[]}\n' > "${fixture}"
  cat > "${overrides}" <<'EOF'
{
  "model_overrides": {},
  "pr_feedback_classifier": {
    "model": "gpt-5.5",
    "reasoning_effort": "xhigh"
  }
}
EOF

  output="$(
    bash "${SCRIPT_PATH}" \
      --report-file "${report}" \
      --fixture-output-file "${fixture}" \
      --overrides-file "${overrides}"
  )"

  assert_contains "PR_FEEDBACK_CLASSIFIER_MODEL=gpt-5.5" "${output}"
  assert_contains "PR_FEEDBACK_CLASSIFIER_REASONING_EFFORT=xhigh" "${output}"
}

test_source_root_override_config_is_used_when_workspace_copy_missing() {
  local temp_dir source_root workspace report fixture overrides output
  temp_dir="$(mktemp -d)"
  source_root="${temp_dir}/source"
  workspace="${temp_dir}/workspace"
  report="${workspace}/report.md"
  fixture="${workspace}/fixture.json"
  overrides="${source_root}/.symphony/codex-overrides.json"

  mkdir -p "${source_root}/.symphony" "${workspace}/.symphony"
  printf 'Claude review report\n' > "${report}"
  printf '{"classification":"clean","reason":"ok","action_items":[]}\n' > "${fixture}"
  cat > "${overrides}" <<'EOF'
{
  "model_overrides": {},
  "pr_feedback_classifier": {
    "model": "gpt-5.5",
    "reasoning_effort": "high"
  }
}
EOF

  output="$(
    cd "${workspace}"
    SYMPHONY_LOCAL_SOURCE_ROOT="${source_root}" \
      bash "${SCRIPT_PATH}" \
        --report-file "${report}" \
        --fixture-output-file "${fixture}"
  )"

  assert_contains "PR_FEEDBACK_CLASSIFIER_MODEL=gpt-5.5" "${output}"
  assert_contains "PR_FEEDBACK_CLASSIFIER_REASONING_EFFORT=high" "${output}"
}

test_malformed_override_config_warns_and_uses_defaults() {
  local temp_dir report fixture overrides output_file output
  temp_dir="$(mktemp -d)"
  report="${temp_dir}/report.md"
  fixture="${temp_dir}/fixture.json"
  overrides="${temp_dir}/codex-overrides.json"
  output_file="${temp_dir}/output.txt"

  printf 'Claude review report\n' > "${report}"
  printf '{"classification":"clean","reason":"ok","action_items":[]}\n' > "${fixture}"
  printf '{broken json\n' > "${overrides}"

  bash "${SCRIPT_PATH}" \
    --report-file "${report}" \
    --fixture-output-file "${fixture}" \
    --overrides-file "${overrides}" \
    > "${output_file}" 2>&1

  output="$(cat "${output_file}")"
  assert_contains "PR_FEEDBACK_CLASSIFIER_CONFIG_WARNING=Failed to read overrides file" "${output}"
  assert_contains "PR_FEEDBACK_CLASSIFIER_MODEL=gpt-5.4-mini" "${output}"
  assert_contains "PR_FEEDBACK_CLASSIFIER_REASONING_EFFORT=high" "${output}"
}

test_fixture_classifications_map_to_exit_codes
test_invalid_json_is_error
test_clean_with_action_items_is_uncertain
test_prompt_guides_ci_verification_only_as_clean_pr_gate
test_fixture_actionable_ci_only_is_not_rewritten
test_ci_verification_plus_repo_work_still_actionable
test_json_override_config_is_used
test_source_root_override_config_is_used_when_workspace_copy_missing
test_malformed_override_config_warns_and_uses_defaults

echo "symphony_classify_pr_feedback tests passed"
