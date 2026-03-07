#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

REPORT_PATH="${AGENT_PR_GATE_REPORT:-file_outputs/ci/agent_pr_gate_report.json}"
mkdir -p "$(dirname "${REPORT_PATH}")"

BASE_REF="${GITHUB_BASE_REF:-main}"
git fetch --no-tags --depth=200 origin "${BASE_REF}:refs/remotes/origin/${BASE_REF}" >/dev/null 2>&1 || true
if git rev-parse --verify "origin/${BASE_REF}" >/dev/null 2>&1; then
  DIFF_RANGE="origin/${BASE_REF}...HEAD"
elif git rev-parse --verify "${BASE_REF}" >/dev/null 2>&1; then
  MERGE_BASE="$(git merge-base "${BASE_REF}" HEAD)"
  DIFF_RANGE="${MERGE_BASE}...HEAD"
else
  ROOT_COMMIT="$(git rev-list --max-parents=0 HEAD | tail -n 1)"
  DIFF_RANGE="${ROOT_COMMIT}...HEAD"
fi

PASS_COUNT=0
FAIL_COUNT=0
CHECKS_TSV="$(mktemp)"
trap 'rm -f "${CHECKS_TSV}" /tmp/agent_gate.out /tmp/agent_gate.err' EXIT

record_check() {
  local name="$1"
  local result="$2"
  local detail="$3"

  if [[ "${result}" == "pass" ]]; then
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  printf "%s\t%s\t%s\n" "${name}" "${result}" "${detail//$'\n'/ }" >> "${CHECKS_TSV}"
}

run_check() {
  local name="$1"
  local cmd="$2"
  if bash -lc "${cmd}" >/tmp/agent_gate.out 2>/tmp/agent_gate.err; then
    record_check "${name}" "pass" "$(cat /tmp/agent_gate.out)"
  else
    local error_output
    error_output="$(cat /tmp/agent_gate.err)"
    if [[ -z "${error_output}" ]]; then
      error_output="$(cat /tmp/agent_gate.out)"
    fi
    record_check "${name}" "fail" "${error_output}"
  fi
}

# Keep these as fast-fail preflight checks in the gate so path-config mistakes
# surface before the heavier Docker-backed CI jobs start.
run_check "unit-ignore-path-validation" \
  "bash backend/tests/unit/run_ci_unit_tests.sh --validate-only"

run_check "contract-core-path-validation" \
  "bash backend/tests/contract/run_ci_contract_core_tests.sh --validate-only"

mapfile -t CHANGED_BACKEND_PY_FILES < <(
  git diff --name-only --diff-filter=ACMR "${DIFF_RANGE}" -- backend/src backend/tests | awk '/\.py$/'
)

if python3 -m ruff --version >/dev/null 2>&1; then
  if (( ${#CHANGED_BACKEND_PY_FILES[@]} > 0 )); then
    RUFF_FILE_ARGS="$(printf '%q ' "${CHANGED_BACKEND_PY_FILES[@]}")"
    run_check "ruff-lint" \
      "python3 -m ruff check ${RUFF_FILE_ARGS}"
  else
    record_check "ruff-lint" "pass" "no changed backend Python files; lint not required"
  fi

  if [[ "${AGENT_GATE_ENABLE_RUFF_FORMAT_CHECK:-0}" == "1" ]]; then
    if (( ${#CHANGED_BACKEND_PY_FILES[@]} > 0 )); then
      RUFF_FILE_ARGS="$(printf '%q ' "${CHANGED_BACKEND_PY_FILES[@]}")"
      run_check "ruff-format-check" \
        "python3 -m ruff format --check ${RUFF_FILE_ARGS}"
    else
      record_check "ruff-format-check" "pass" "no changed backend Python files; format check not required"
    fi
  else
    record_check "ruff-format-check" "pass" "ruff format check disabled by default (set AGENT_GATE_ENABLE_RUFF_FORMAT_CHECK=1 to enable)"
  fi
else
  if [[ "${AGENT_GATE_SKIP_RUFF_IF_MISSING:-0}" == "1" ]]; then
    record_check "ruff-lint" "pass" "ruff missing; skipped by AGENT_GATE_SKIP_RUFF_IF_MISSING=1"
  else
    record_check "ruff-lint" "fail" "ruff is not installed (install with: python3 -m pip install ruff)"
  fi

  if [[ "${AGENT_GATE_ENABLE_RUFF_FORMAT_CHECK:-0}" == "1" ]]; then
    if [[ "${AGENT_GATE_SKIP_RUFF_IF_MISSING:-0}" == "1" ]]; then
      record_check "ruff-format-check" "pass" "ruff missing; skipped by AGENT_GATE_SKIP_RUFF_IF_MISSING=1"
    else
      record_check "ruff-format-check" "fail" "ruff is not installed (install with: python3 -m pip install ruff)"
    fi
  else
    record_check "ruff-format-check" "pass" "ruff format check disabled by default (set AGENT_GATE_ENABLE_RUFF_FORMAT_CHECK=1 to enable)"
  fi
fi

run_check "yaml-schema-parse-check" \
  "python3 - <<'PY'
from pathlib import Path
import sys
import yaml

roots = [Path('config')]
errors = []
for root in roots:
    for path in root.rglob('*.yaml'):
        try:
            yaml.safe_load(path.read_text(encoding='utf-8'))
        except Exception as exc:
            errors.append(f'{path}: {exc}')

if errors:
    print('\\n'.join(errors))
    sys.exit(1)
print('yaml-parse-check: ok')
PY"

CHANGED_BACKEND_FILES="$(git diff --name-only "${DIFF_RANGE}" -- backend || true)"
if [[ -n "${CHANGED_BACKEND_FILES}" ]]; then
  if docker image inspect ai-curation-unit-tests:latest >/dev/null 2>&1; then
    run_check "backend-unit-smoke" \
      "docker run --rm -v \"${ROOT_DIR}/backend:/app/backend\" ai-curation-unit-tests:latest python -m pytest tests/unit/test_exceptions.py -q"
  else
    if [[ "${AGENT_GATE_SKIP_TEST_SMOKE_IF_MISSING:-0}" == "1" ]]; then
      record_check "backend-unit-smoke" "pass" "backend changed; skipped because image missing and AGENT_GATE_SKIP_TEST_SMOKE_IF_MISSING=1"
    else
      record_check "backend-unit-smoke" "fail" "backend changed but ai-curation-unit-tests:latest image is missing"
    fi
  fi
else
  record_check "backend-unit-smoke" "pass" "backend unchanged; smoke test not required"
fi

# Frontend validation intentionally lives in .github/workflows/test.yml.
# The gate keeps orchestration responsibility by waiting on the Frontend Tests
# and Frontend Build checks instead of rerunning npm ci/test/build inline here.

IGNORE_JUSTIFICATION="${AGENT_GATE_IGNORE_JUSTIFICATION:-}"
CHANGED_IGNORE_LINES="$(git diff --name-only "${DIFF_RANGE}" -- backend/tests/unit/.ci-ignore-paths backend/tests/contract/.core-test-paths || true)"
if [[ -n "${CHANGED_IGNORE_LINES}" ]]; then
  if [[ "${IGNORE_JUSTIFICATION}" == *"Ignore-Justification:"* ]]; then
    record_check "ignore-list-justification" "pass" "ignore-list changed with justification marker"
  else
    record_check "ignore-list-justification" "fail" "ignore-list changed without 'Ignore-Justification:' marker in PR body"
  fi
else
  record_check "ignore-list-justification" "pass" "ignore-list unchanged"
fi

OVERALL="pass"
if (( FAIL_COUNT > 0 )); then
  OVERALL="fail"
fi

python3 - "${CHECKS_TSV}" "${REPORT_PATH}" "${OVERALL}" "${BASE_REF}" "${DIFF_RANGE}" "${PASS_COUNT}" "${FAIL_COUNT}" <<'PY'
import csv
import json
import sys

checks_tsv, report_path, overall, base_ref, diff_range, pass_count, fail_count = sys.argv[1:]

checks = []
with open(checks_tsv, "r", encoding="utf-8") as handle:
    reader = csv.reader(handle, delimiter="\t")
    for row in reader:
        if len(row) < 3:
            continue
        checks.append({"name": row[0], "result": row[1], "detail": row[2]})

payload = {
    "overall": overall,
    "base_ref": base_ref,
    "diff_range": diff_range,
    "pass_count": int(pass_count),
    "fail_count": int(fail_count),
    "checks": checks,
}

with open(report_path, "w", encoding="utf-8") as out:
    json.dump(payload, out, indent=2)
    out.write("\n")
PY

echo "Agent PR gate: ${OVERALL} (pass=${PASS_COUNT}, fail=${FAIL_COUNT})"
echo "Report: ${REPORT_PATH}"

if [[ "${OVERALL}" != "pass" ]]; then
  exit 1
fi
