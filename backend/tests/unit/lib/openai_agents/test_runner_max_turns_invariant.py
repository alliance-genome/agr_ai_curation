"""Guardrail tests for bounded OpenAI Agents SDK runner calls."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from src.lib.domain_packs.validator_dispatch import _max_turns_with_validator_finalization
from src.lib.openai_agents.config import (
    get_guardrail_single_shot_max_turns,
    get_hierarchy_resolution_max_turns,
    get_supervisor_max_calls_per_specialist,
    get_supervisor_max_specialist_calls_per_turn,
)

BACKEND_SRC = Path(__file__).resolve().parents[4] / "src"
RUNNER_METHODS = {"run", "run_sync", "run_streamed"}


@dataclass(frozen=True)
class RunnerCall:
    path: Path
    function: str
    line: int
    method: str
    has_max_turns: bool

    def display(self) -> str:
        relative_path = self.path.relative_to(BACKEND_SRC.parent)
        return f"{relative_path}:{self.line} in {self.function} ({self.method})"


class RunnerMaxTurnsVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[RunnerCall] = []
        self._scope_stack: list[str] = []
        self._max_turns_kwargs_stack: list[set[str]] = [set()]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._scope_stack.append("<lambda>")
        self._max_turns_kwargs_stack.append(set())
        self.generic_visit(node)
        self._max_turns_kwargs_stack.pop()
        self._scope_stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        self._record_max_turns_subscript_targets(node.targets)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record_max_turns_subscript_targets([node.target])
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._record_max_turns_subscript_targets([node.target])
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        method = _runner_method_name(node.func)
        if method is not None:
            self.calls.append(
                RunnerCall(
                    path=self.path,
                    function=".".join(self._scope_stack) or "<module>",
                    line=node.lineno,
                    method=method,
                    has_max_turns=self._call_has_max_turns(node),
                )
            )
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._scope_stack.append(node.name)
        self._max_turns_kwargs_stack.append(set())
        self.generic_visit(node)
        self._max_turns_kwargs_stack.pop()
        self._scope_stack.pop()

    def _record_max_turns_subscript_targets(self, targets: list[ast.expr]) -> None:
        for target in targets:
            kwargs_name = _max_turns_subscript_owner(target)
            if kwargs_name:
                self._max_turns_kwargs_stack[-1].add(kwargs_name)

    def _call_has_max_turns(self, node: ast.Call) -> bool:
        for keyword in node.keywords:
            if keyword.arg == "max_turns":
                return True
            if keyword.arg is None and isinstance(keyword.value, ast.Name):
                if keyword.value.id in self._max_turns_kwargs_stack[-1]:
                    return True
        return False


def test_all_backend_runner_calls_pin_max_turns() -> None:
    unbounded_calls = [
        call.display()
        for call in _runner_calls_under_backend_src()
        if not call.has_max_turns
    ]

    assert not unbounded_calls, (
        "Every Runner.run/run_sync/run_streamed call under backend/src must pass "
        "max_turns explicitly. Unbounded calls:\n"
        + "\n".join(unbounded_calls)
    )


def test_supervisor_budget_getters_are_positive_documented_defaults(
    monkeypatch,
) -> None:
    monkeypatch.delenv("SUPERVISOR_MAX_SPECIALIST_CALLS_PER_TURN", raising=False)
    monkeypatch.delenv("SUPERVISOR_MAX_CALLS_PER_SPECIALIST", raising=False)

    max_specialist_calls_per_turn = get_supervisor_max_specialist_calls_per_turn()
    max_calls_per_specialist = get_supervisor_max_calls_per_specialist()

    assert isinstance(max_specialist_calls_per_turn, int)
    assert max_specialist_calls_per_turn == 25
    assert max_specialist_calls_per_turn >= 1
    assert isinstance(max_calls_per_specialist, int)
    assert max_calls_per_specialist == 8
    assert max_calls_per_specialist >= 1


def test_single_shot_runner_budget_getters_are_positive_documented_defaults(
    monkeypatch,
) -> None:
    monkeypatch.delenv("GUARDRAIL_SINGLE_SHOT_MAX_TURNS", raising=False)
    monkeypatch.delenv("HIERARCHY_RESOLUTION_MAX_TURNS", raising=False)

    guardrail_max_turns = get_guardrail_single_shot_max_turns()
    hierarchy_max_turns = get_hierarchy_resolution_max_turns()

    assert isinstance(guardrail_max_turns, int)
    assert guardrail_max_turns == 10
    assert guardrail_max_turns >= 1
    assert isinstance(hierarchy_max_turns, int)
    assert hierarchy_max_turns == 10
    assert hierarchy_max_turns >= 1


def test_validator_finalization_turn_budget_has_two_turn_buffer() -> None:
    assert _max_turns_with_validator_finalization(0, minimum=4) == 4
    assert _max_turns_with_validator_finalization(2, minimum=4) == 4
    assert _max_turns_with_validator_finalization(3, minimum=4) == 5
    assert _max_turns_with_validator_finalization(8, minimum=4) == 10


def _runner_calls_under_backend_src() -> list[RunnerCall]:
    calls: list[RunnerCall] = []
    for path in sorted(BACKEND_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = RunnerMaxTurnsVisitor(path)
        visitor.visit(tree)
        calls.extend(visitor.calls)
    return calls


def _runner_method_name(func: ast.expr) -> str | None:
    if not isinstance(func, ast.Attribute) or func.attr not in RUNNER_METHODS:
        return None
    return func.attr if _expr_ends_with_runner(func.value) else None


def _expr_ends_with_runner(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Name):
        return expr.id.endswith("Runner")
    if isinstance(expr, ast.Attribute):
        return expr.attr.endswith("Runner")
    return False


def _max_turns_subscript_owner(target: ast.expr) -> str | None:
    if not isinstance(target, ast.Subscript):
        return None
    if not isinstance(target.value, ast.Name):
        return None
    if _literal_string(target.slice) != "max_turns":
        return None
    return target.value.id


def _literal_string(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None
