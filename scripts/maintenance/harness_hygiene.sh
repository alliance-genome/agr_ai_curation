#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="file_outputs/harness_hygiene/${STAMP}"
mkdir -p "${OUT_DIR}"

PASS=0
FAIL=0
RESULTS="${OUT_DIR}/results.tsv"
: > "${RESULTS}"

record() {
  local name="$1"
  local status="$2"
  local detail="$3"
  printf "%s\t%s\t%s\n" "${name}" "${status}" "${detail//$'\n'/ }" >> "${RESULTS}"
  if [[ "${status}" == "pass" ]]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
  fi
}

run_check() {
  local name="$1"
  local cmd="$2"
  if bash -lc "${cmd}" >"${OUT_DIR}/${name}.out" 2>"${OUT_DIR}/${name}.err"; then
    record "${name}" "pass" "$(cat "${OUT_DIR}/${name}.out")"
  else
    record "${name}" "fail" "$(cat "${OUT_DIR}/${name}.err")"
  fi
}

run_check "unit-ignore-path-validation" "bash backend/tests/unit/run_ci_unit_tests.sh --validate-only"
run_check "contract-core-path-validation" "bash backend/tests/contract/run_ci_contract_core_tests.sh --validate-only"

run_check "required-doc-presence" "python3 - <<'PY'
from pathlib import Path
import sys

required = [
    Path('AGENTS.md'),
    Path('docs/README.md'),
    Path('docs/developer/README.md'),
    Path('docs/developer/TEST_STRATEGY.md'),
    Path('.symphony/WORKFLOW.md'),
]
missing = [str(p) for p in required if not p.exists()]
if missing:
    print('Missing required docs:\\n' + '\\n'.join(missing))
    sys.exit(1)
print('required-docs: ok')
PY"

run_check "markdown-link-check" "python3 - <<'PY'
from pathlib import Path
import re
import sys

link_re = re.compile(r'\\[[^\\]]+\\]\\(([^)]+)\\)')
roots = [Path('README.md')] + list(Path('docs').rglob('*.md'))
errors = []

for md in roots:
    if not md.exists():
        continue
    text = md.read_text(encoding='utf-8', errors='ignore')
    for raw_target in link_re.findall(text):
        target = raw_target.strip()
        if not target or target.startswith('#'):
            continue
        if '://' in target or target.startswith('mailto:'):
            continue
        target = target.split('#', 1)[0]
        target = target.split('?', 1)[0]
        candidate = (md.parent / target).resolve()
        if not candidate.exists():
            errors.append(f'{md}: missing -> {raw_target}')

if errors:
    print('\\n'.join(errors))
    sys.exit(1)
print('markdown-links: ok')
PY"

run_check "symphony-workspace-hygiene" "python3 - <<'PY'
from pathlib import Path
import time
import os
import sys

root = Path.home() / '.symphony' / 'workspaces' / 'agr_ai_curation'
now = time.time()
stale_days = 14
stale = []
is_ci = os.getenv('GITHUB_ACTIONS', '').lower() == 'true'
if root.exists():
    for child in root.iterdir():
        try:
            age_days = (now - child.stat().st_mtime) / 86400.0
        except FileNotFoundError:
            continue
        if age_days >= stale_days:
            stale.append((child.name, round(age_days, 1)))

if stale:
    print('stale-workspaces:')
    for name, age in stale:
        print(f'- {name} ({age} days)')
    if not is_ci:
        sys.exit(1)
else:
    print('no-stale-workspaces')
PY"

SUMMARY="${OUT_DIR}/summary.md"
OVERALL="pass"
if (( FAIL > 0 )); then
  OVERALL="fail"
fi

{
  echo "# Harness Hygiene Report"
  echo
  echo "- timestamp_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "- overall: ${OVERALL}"
  echo "- pass_count: ${PASS}"
  echo "- fail_count: ${FAIL}"
  echo
  echo "## Checks"
  while IFS=$'\t' read -r name status detail; do
    echo "- ${name}: ${status}"
    if [[ -n "${detail}" ]]; then
      echo "  - detail: ${detail}"
    fi
  done < "${RESULTS}"
} > "${SUMMARY}"

mkdir -p file_outputs/harness_hygiene
cp "${SUMMARY}" file_outputs/harness_hygiene/latest.md

echo "Harness hygiene: ${OVERALL} (pass=${PASS}, fail=${FAIL})"
echo "Report: ${SUMMARY}"

if [[ "${OVERALL}" != "pass" ]]; then
  exit 1
fi
