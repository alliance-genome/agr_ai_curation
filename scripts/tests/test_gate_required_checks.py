#!/usr/bin/env python3
"""The agent PR gate's REQUIRED_CHECKS must name exactly the jobs of the Unit Tests workflow.

A job added to .github/workflows/test.yml but missing from the gate's list would run
without blocking anything; a required name with no matching job (a renamed job) would
make every pull request's gate wait for its full timeout and then fail. Every job in
test.yml is unconditional, so each one is required.

Standard library only (the runner's python3 has no PyYAML): the job names are read from
the two-space job keys under `jobs:` and their four-space `name:` lines.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_WORKFLOW = REPO_ROOT / ".github/workflows/test.yml"
GATE_WORKFLOW = REPO_ROOT / ".github/workflows/agent-pr-gate.yml"


def unit_tests_job_names(text: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index("jobs:")
    names: list[str] = []
    job: str | None = None
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break  # the next top-level key ends the jobs block
        key = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*", line)
        if key:
            if job is not None:
                raise SystemExit(f"test.yml job {job!r} has no name: line")
            job = key.group(1)
            continue
        name = re.fullmatch(r"    name:\s*(.+?)\s*", line)
        if name and job is not None:
            names.append(name.group(1).strip("'\""))
            job = None
    if job is not None:
        raise SystemExit(f"test.yml job {job!r} has no name: line")
    return names


def gate_required_checks(text: str) -> list[str]:
    match = re.search(r"^\s*REQUIRED_CHECKS:\s*(.+?)\s*$", text, re.MULTILINE)
    if not match:
        raise SystemExit("agent-pr-gate.yml has no REQUIRED_CHECKS line")
    return [name.strip() for name in match.group(1).split(",") if name.strip()]


def main() -> int:
    jobs = unit_tests_job_names(TEST_WORKFLOW.read_text())
    required = gate_required_checks(GATE_WORKFLOW.read_text())
    problems = []
    for label, names in (("test.yml job names", jobs), ("REQUIRED_CHECKS", required)):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            problems.append(f"duplicate {label}: {duplicates}")
    missing = sorted(set(jobs) - set(required))
    unknown = sorted(set(required) - set(jobs))
    if missing:
        problems.append(f"test.yml jobs the gate does not wait for: {missing}")
    if unknown:
        problems.append(f"REQUIRED_CHECKS names with no test.yml job (the gate would wait and fail): {unknown}")
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"gate required checks match the {len(jobs)} Unit Tests jobs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
