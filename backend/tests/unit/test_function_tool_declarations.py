"""Every function tool is declared through agr_ai_curation_runtime.function_tools.

The SDK's own ``function_tool`` and ``default_tool_error_function`` give the model a
redacted, fixed error message. Only the runtime wrapper may use the SDK decorator;
no module may use the SDK default error function.
"""

import ast
from pathlib import Path

import pytest

from tests.unit.lib.packages import find_repo_root

REPO_ROOT = find_repo_root(Path(__file__))
WRAPPER = REPO_ROOT / "backend" / "src" / "agr_ai_curation_runtime" / "function_tools.py"
SCANNED_ROOTS = (REPO_ROOT / "backend" / "src", REPO_ROOT / "packages")
FORBIDDEN_EVERYWHERE = {"default_tool_error_function"}
FORBIDDEN_OUTSIDE_WRAPPER = {"function_tool"}


def _is_sdk_module(name: str | None) -> bool:
    return name is not None and (name == "agents" or name.startswith("agents."))


def sdk_tool_declaration_problems(source: str, *, wrapper: bool = False) -> list[str]:
    """Return each direct use of the SDK decorator or default error function."""

    forbidden = FORBIDDEN_EVERYWHERE | (set() if wrapper else FORBIDDEN_OUTSIDE_WRAPPER)
    tree = ast.parse(source)
    sdk_module_aliases: set[str] = set()
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_sdk_module(alias.name):
                    sdk_module_aliases.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and _is_sdk_module(node.module):
            for alias in node.names:
                if alias.name == "*" or alias.name in forbidden:
                    problems.append(f"line {node.lineno}: from {node.module} import {alias.name}")
                elif _is_sdk_module(f"{node.module}.{alias.name}"):
                    # ``from agents import tool`` binds a submodule.
                    sdk_module_aliases.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            root = node.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if node.attr in FORBIDDEN_EVERYWHERE or (
                isinstance(root, ast.Name) and root.id in sdk_module_aliases
            ):
                problems.append(f"line {node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_EVERYWHERE:
            problems.append(f"line {node.lineno}: {node.id}")
    return problems


def _python_files():
    for root in SCANNED_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "tests" in path.relative_to(REPO_ROOT).parts:
                continue
            yield path


def test_tools_are_declared_only_through_the_runtime_wrapper():
    problems = {
        str(path.relative_to(REPO_ROOT)): found
        for path in _python_files()
        if (found := sdk_tool_declaration_problems(
            path.read_text(encoding="utf-8"), wrapper=path == WRAPPER))
    }
    assert problems == {}


def test_wrapper_is_the_one_sdk_decorator_user():
    source = WRAPPER.read_text(encoding="utf-8")
    assert sdk_tool_declaration_problems(source) == [
        "line 25: from agents import function_tool"]
    assert sdk_tool_declaration_problems(source, wrapper=True) == []


@pytest.mark.parametrize("source", [
    "from agents import function_tool\n",
    "from agents import Agent, function_tool as tool\n",
    "from agents.tool import function_tool\n",
    "import agents\n\n@agents.function_tool\ndef f(x: str) -> str:\n    return x\n",
    "import agents.tool as sdk_tool\nt = sdk_tool.function_tool(f)\n",
    "from agents import tool\nt = tool.function_tool(f)\n",
    "from agents import *\n",
    "from agents.tool import default_tool_error_function\n",
    "from agents import tool\nmessage = tool.default_tool_error_function(ctx, error)\n",
])
def test_each_direct_sdk_form_is_reported(source):
    assert sdk_tool_declaration_problems(source)


def test_runtime_wrapper_imports_are_allowed():
    source = (
        "from agr_ai_curation_runtime.function_tools import function_tool\n"
        "from agents import Agent, FunctionTool\n"
        "@function_tool\ndef f(x: str) -> str:\n    return x\n"
    )
    assert sdk_tool_declaration_problems(source) == []
