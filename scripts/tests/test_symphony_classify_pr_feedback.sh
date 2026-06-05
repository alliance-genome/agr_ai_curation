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
test_json_override_config_is_used
test_source_root_override_config_is_used_when_workspace_copy_missing
test_malformed_override_config_warns_and_uses_defaults

echo "symphony_classify_pr_feedback tests passed"
