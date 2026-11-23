#!/usr/bin/env python3
"""Cases for test_gate_required_checks.py, on small fixture workflows (standard library only)."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("gate_checks", HERE / "test_gate_required_checks.py")
gate_checks = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(gate_checks)

HEADER = "name: Unit Tests\n\non:\n  pull_request:\n\njobs:\n"
PLAIN = "  unit:\n    name: Backend Unit Tests\n    runs-on: ubuntu-latest\n    steps:\n      - name: Run\n        run: true\n"


def problems(test_yml: str, required: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        test_path, gate_path = Path(tmp, "test.yml"), Path(tmp, "gate.yml")
        test_path.write_text(test_yml)
        gate_path.write_text(f"jobs:\n  gate:\n    env:\n      REQUIRED_CHECKS: {required}\n")
        return gate_checks.check(test_path, gate_path)


def expect_pass(label: str, test_yml: str, required: str) -> None:
    found = problems(test_yml, required)
    assert not found, f"{label}: expected no problems, got {found}"


def expect_fail(label: str, test_yml: str, required: str, fragment: str) -> None:
    found = problems(test_yml, required)
    assert any(fragment in problem for problem in found), f"{label}: expected {fragment!r} in {found}"


MATRIX_INLINE = PLAIN + (
    "  shards:\n    name: Shard Tests\n    runs-on: ubuntu-latest\n"
    "    strategy:\n      fail-fast: false\n      matrix:\n        shard: [1, 2]\n"
    "    steps:\n      - run: true\n"
)
MATRIX_BLOCK_TWO_KEYS = PLAIN + (
    "  combos:\n    name: Combo Tests\n    runs-on: ubuntu-latest\n"
    "    strategy:\n      matrix:\n        python:\n          - '3.11'\n          - '3.12'  # current\n"
    "        db: [postgres]\n    steps:\n      - run: true\n"
)

expect_pass("plain job listed", HEADER + PLAIN, "Backend Unit Tests")
expect_fail("plain job missing", HEADER + PLAIN, "Other", "does not wait for")
expect_fail("renamed job", HEADER + PLAIN, "Backend Unit Tests,Old Name", "would wait and fail")
expect_fail("duplicate required name", HEADER + PLAIN, "Backend Unit Tests,Backend Unit Tests", "duplicate")

# Matrix jobs report "Name (value, ...)" runs, never the plain name.
expect_pass("matrix expanded names listed", HEADER + MATRIX_INLINE,
            "Backend Unit Tests,Shard Tests (1),Shard Tests (2)")
expect_fail("matrix listed by its plain name", HEADER + MATRIX_INLINE,
            "Backend Unit Tests,Shard Tests", "Shard Tests (1)")
# Two keys give "Name (a, b)": REQUIRED_CHECKS is split on commas, so it can never name them.
expect_fail("two-key matrix cannot be required", HEADER + MATRIX_BLOCK_TWO_KEYS,
            "Backend Unit Tests", "contains a comma")
MATRIX_BLOCK_ONE_KEY = PLAIN + (
    "  py:\n    name: Py Tests\n    runs-on: ubuntu-latest\n"
    "    strategy:\n      matrix:\n        python:\n          - '3.11'\n          - '3.12'  # current\n"
    "    steps:\n      - run: true\n"
)
expect_pass("one-key block matrix with a trailing comment", HEADER + MATRIX_BLOCK_ONE_KEY,
            "Backend Unit Tests,Py Tests (3.11),Py Tests (3.12)")
expect_fail("matrix include is refused", HEADER + PLAIN + (
    "  inc:\n    name: Inc\n    strategy:\n      matrix:\n        include:\n          - os: linux\n"
    "    steps:\n      - run: true\n"), "Backend Unit Tests,Inc", "not supported")
expect_fail("matrix-built name is refused", HEADER + PLAIN + (
    "  tmpl:\n    name: Tmpl ${{ matrix.shard }}\n    strategy:\n      matrix:\n        shard: [1]\n"
    "    steps:\n      - run: true\n"), "Backend Unit Tests", "built from matrix values")

# Comments: a trailing comment on a name line, and a column-0 comment inside jobs:.
expect_pass("trailing comment on the name line", HEADER + PLAIN.replace(
    "name: Backend Unit Tests\n", "name: Backend Unit Tests  # the main suite\n"), "Backend Unit Tests")
expect_pass("column-0 comment inside jobs", HEADER + "# Backend jobs\n" + PLAIN + "\n# more later\n",
            "Backend Unit Tests")
expect_pass("quoted name", HEADER + PLAIN.replace(
    "name: Backend Unit Tests\n", "name: 'Backend Unit Tests'\n"), "Backend Unit Tests")

print("gate required-checks cases passed")
sys.exit(0)
