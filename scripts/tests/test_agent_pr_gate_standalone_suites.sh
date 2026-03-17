#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

temp_dir="$(mktemp -d)"
trap 'rm -rf "${temp_dir}"' EXIT

sandbox_repo="${temp_dir}/repo"
mkdir -p "${sandbox_repo}"
tar -C "${repo_root}" -cf - . | tar -C "${sandbox_repo}" -xf -
chmod -R u+w "${sandbox_repo}"
rm -f "${sandbox_repo}/.git/shallow.lock"

git -C "${sandbox_repo}" config user.name "Codex"
git -C "${sandbox_repo}" config user.email "codex@example.com"
git -C "${sandbox_repo}" add -A
git -C "${sandbox_repo}" commit --allow-empty -q -m "baseline for agent_pr_gate standalone suite regression"
git -C "${sandbox_repo}" branch -f scenario-base HEAD >/dev/null
git -C "${sandbox_repo}" remote set-url origin "${sandbox_repo}"

assert_check_state() {
  local report_path="$1"
  local check_name="$2"
  local expected_status="$3"
  local expected_execution="$4"
  local expected_result="$5"

  python3 - "$report_path" "$check_name" "$expected_status" "$expected_execution" "$expected_result" <<'PY'
import json
import sys

report_path, check_name, expected_status, expected_execution, expected_result = sys.argv[1:]
with open(report_path, "r", encoding="utf-8") as handle:
    payload = json.load(handle)

for check in payload.get("checks", []):
    if check.get("name") == check_name:
        break
else:
    raise SystemExit(f"Missing check record: {check_name}")

actual = (check.get("status"), check.get("execution"), check.get("result"))
expected = (expected_status, expected_execution, expected_result)
if actual != expected:
    raise SystemExit(
        f"{check_name} expected status/execution/result {expected} but found {actual}: {check}"
    )
PY
}

assert_overall_pass() {
  local report_path="$1"

  python3 - "$report_path" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)

if payload.get("overall") != "pass":
    raise SystemExit(f"Expected overall pass, found: {payload.get('overall')}")
PY
}

reset_to_base() {
  git -C "${sandbox_repo}" reset --hard -q scenario-base
  git -C "${sandbox_repo}" clean -fdq
}

commit_scenario_change() {
  local path="$1"
  local marker="$2"

  printf '\n%s\n' "$marker" >> "${sandbox_repo}/${path}"
  git -C "${sandbox_repo}" add "${path}"
  git -C "${sandbox_repo}" commit -q -m "scenario change: ${path}"
}

run_gate_for_scenario() {
  local report_path="$1"

  (
    cd "${sandbox_repo}"
    AGENT_GATE_SKIP_RUFF_IF_MISSING=1 \
    GITHUB_BASE_REF=scenario-base \
    AGENT_PR_GATE_REPORT="${report_path}" \
    bash scripts/testing/agent_pr_gate.sh >/dev/null
  )
}

reset_to_base
commit_scenario_change "README.md" "<!-- unrelated scenario marker -->"
report_path="${sandbox_repo}/file_outputs/ci/agent_pr_gate_unrelated_report.json"
run_gate_for_scenario "${report_path}"
assert_overall_pass "${report_path}"
assert_check_state "${report_path}" "installer-shell-regression-suite" "skipped" "skipped" "pass"
assert_check_state "${report_path}" "publish-artifact-shell-regression-suite" "skipped" "skipped" "pass"

reset_to_base
commit_scenario_change "scripts/install/01_preflight.sh" "# installer scenario marker"
report_path="${sandbox_repo}/file_outputs/ci/agent_pr_gate_installer_report.json"
run_gate_for_scenario "${report_path}"
assert_overall_pass "${report_path}"
assert_check_state "${report_path}" "installer-shell-regression-suite" "passed" "ran" "pass"
assert_check_state "${report_path}" "publish-artifact-shell-regression-suite" "skipped" "skipped" "pass"

reset_to_base
commit_scenario_change "scripts/release/prepare_publish_artifacts.sh" "# publish scenario marker"
report_path="${sandbox_repo}/file_outputs/ci/agent_pr_gate_publish_report.json"
run_gate_for_scenario "${report_path}"
assert_overall_pass "${report_path}"
assert_check_state "${report_path}" "installer-shell-regression-suite" "skipped" "skipped" "pass"
assert_check_state "${report_path}" "publish-artifact-shell-regression-suite" "passed" "ran" "pass"

echo "agent_pr_gate standalone suite checks passed"
