#!/usr/bin/env python3
"""The agent PR gate's REQUIRED_CHECKS must name exactly the check runs of the Unit Tests workflow.

A job added to .github/workflows/test.yml but missing from the gate's list would run
without blocking anything; a required name with no matching check run (a renamed job)
would make every pull request's gate wait for its full timeout and then fail. Every job
in test.yml is unconditional, so each one is required.

A matrix job does not report a check run under its plain name: GitHub names each run
"Name (value, ...)" in matrix key order. The expected names are expanded from simple
one-key matrices (a list of plain values). Any other matrix shape fails loudly, so nobody
adds a matrix job that the gate would wait two hours for: include, exclude, nested values,
a name built from matrix values, and any expanded name with a comma (two or more keys
give "Name (a, b)", which the comma-separated REQUIRED_CHECKS cannot name).

Standard library only (the runner's python3 has no PyYAML): the structure is read from
the indentation of test.yml, which is all this check needs.

Usage: test_gate_required_checks.py [--test-workflow PATH] [--gate-workflow PATH]
"""

from __future__ import annotations

import argparse
import itertools
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class WorkflowShapeError(Exception):
    """test.yml has a shape this check cannot verify; fail loudly."""


def _strip_comment(value: str) -> str:
    # YAML: a comment starts at a '#' preceded by whitespace (or at the start).
    return re.sub(r"(^|\s)#.*$", "", value).strip()


def _scalar(value: str) -> str:
    value = _strip_comment(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _significant(lines: list[str]) -> list[str]:
    """Drop blank lines and whole-line comments (at any column)."""
    return [line for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _job_blocks(text: str) -> dict[str, list[str]]:
    lines = text.splitlines()
    try:
        start = lines.index("jobs:")
    except ValueError as exc:
        raise WorkflowShapeError("test.yml has no top-level 'jobs:' block") from exc
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in _significant(lines[start + 1:]):
        if _indent(line) == 0:
            break  # the next top-level key ends the jobs block
        key = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*(#.*)?", line)
        if key:
            current = key.group(1)
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    return blocks


def _child_block(lines: list[str], key: str, indent: int) -> list[str] | None:
    """Lines nested under `key:` at the given indent, or None when the key is absent."""
    for index, line in enumerate(lines):
        if _indent(line) != indent:
            continue
        if re.fullmatch(rf"\s*{re.escape(key)}:\s*(#.*)?", line):
            block = []
            for child in lines[index + 1:]:
                if _indent(child) <= indent:
                    break
                block.append(child)
            return block
        if re.match(rf"\s*{re.escape(key)}:\s*\S", line):
            raise WorkflowShapeError(f"'{key}:' with an inline value is not supported")
    return None


def _matrix_values(matrix: list[str], job: str) -> list[tuple[str, list[str]]]:
    if not matrix:
        raise WorkflowShapeError(f"job {job!r}: empty matrix")
    key_indent = _indent(matrix[0])
    dimensions: list[tuple[str, list[str]]] = []
    index = 0
    while index < len(matrix):
        line = matrix[index]
        match = re.fullmatch(r"\s*([A-Za-z0-9_-]+):\s*(.*)", line)
        if _indent(line) != key_indent or not match:
            raise WorkflowShapeError(f"job {job!r}: unsupported matrix line {line.strip()!r}")
        key, rest = match.group(1), _strip_comment(match.group(2))
        if key in ("include", "exclude"):
            raise WorkflowShapeError(
                f"job {job!r}: matrix '{key}' is not supported; extend this check to expand it"
            )
        index += 1
        if rest:
            inline = re.fullmatch(r"\[(.*)\]", rest)
            if not inline:
                raise WorkflowShapeError(f"job {job!r}: matrix key {key!r} must be a list")
            values = [_scalar(item) for item in inline.group(1).split(",") if item.strip()]
        else:
            values = []
            while index < len(matrix) and _indent(matrix[index]) > key_indent:
                item = re.fullmatch(r"\s*-\s+(.+)", matrix[index])
                if not item or ":" in _strip_comment(item.group(1)):
                    raise WorkflowShapeError(f"job {job!r}: matrix key {key!r} must list plain values")
                values.append(_scalar(item.group(1)))
                index += 1
        if not values:
            raise WorkflowShapeError(f"job {job!r}: matrix key {key!r} has no values")
        dimensions.append((key, values))
    return dimensions


def expected_check_names(text: str) -> list[str]:
    names: list[str] = []
    for job, lines in _job_blocks(text).items():
        name_lines = [line for line in lines if _indent(line) == 4 and re.match(r"\s*name:", line)]
        if not name_lines:
            raise WorkflowShapeError(f"test.yml job {job!r} has no name: line")
        name = _scalar(name_lines[0].split(":", 1)[1])
        strategy = _child_block(lines, "strategy", 4)
        if strategy is None:
            names.append(name)
            continue
        if "${{" in name:
            raise WorkflowShapeError(f"job {job!r}: a check name built from matrix values is not supported")
        matrix = _child_block(strategy, "matrix", _indent(strategy[0])) if strategy else None
        if matrix is None:
            raise WorkflowShapeError(f"job {job!r}: 'strategy:' without a 'matrix:' is not supported")
        dimensions = _matrix_values(matrix, job)
        for combination in itertools.product(*(values for _key, values in dimensions)):
            expanded = f"{name} ({', '.join(combination)})"
            if "," in expanded:
                # REQUIRED_CHECKS is a comma-separated list (the gate splits it on ','),
                # so a check name containing a comma can never be required.
                raise WorkflowShapeError(
                    f"job {job!r}: matrix check name {expanded!r} contains a comma, which REQUIRED_CHECKS "
                    "(comma-separated) cannot name; use a one-key matrix with comma-free values"
                )
            names.append(expanded)
    return names


def gate_required_checks(text: str) -> list[str]:
    match = re.search(r"^\s*REQUIRED_CHECKS:\s*(.+?)\s*$", text, re.MULTILINE)
    if not match:
        raise WorkflowShapeError("agent-pr-gate.yml has no REQUIRED_CHECKS line")
    return [name.strip() for name in _strip_comment(match.group(1)).split(",") if name.strip()]


def check(test_workflow: Path, gate_workflow: Path) -> list[str]:
    """Return the problems found (empty when the lists match)."""
    try:
        expected = expected_check_names(test_workflow.read_text())
        required = gate_required_checks(gate_workflow.read_text())
    except WorkflowShapeError as exc:
        return [str(exc)]
    problems = []
    for label, names in (("check names from test.yml", expected), ("REQUIRED_CHECKS", required)):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            problems.append(f"duplicate {label}: {duplicates}")
    missing = sorted(set(expected) - set(required))
    unknown = sorted(set(required) - set(expected))
    if missing:
        problems.append(f"test.yml check runs the gate does not wait for: {missing}")
    if unknown:
        problems.append(f"REQUIRED_CHECKS names with no test.yml check run (the gate would wait and fail): {unknown}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-workflow", type=Path, default=REPO_ROOT / ".github/workflows/test.yml")
    parser.add_argument("--gate-workflow", type=Path, default=REPO_ROOT / ".github/workflows/agent-pr-gate.yml")
    args = parser.parse_args(argv)
    problems = check(args.test_workflow, args.gate_workflow)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    count = len(expected_check_names(args.test_workflow.read_text()))
    print(f"gate required checks match the {count} Unit Tests check runs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
