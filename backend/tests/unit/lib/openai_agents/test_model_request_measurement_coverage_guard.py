"""ALL-1279 regression guard: no model request path may bypass measurement.

Measurement lives at the Agents SDK's per-turn model resolution plus the
explicit direct-client helper. This guard fails when new code adds a path the
matrix in docs/developer/guides/MODEL_REQUEST_MEASUREMENT_MATRIX.md does not
cover:

- a module calling ``Runner.run*`` without installing measurement;
- a direct provider request call (``.responses.create(...)``,
  ``.chat.completions.create(...)`` ...) instead of passing the bound method to
  ``call_measured_direct_request``;
- a direct ``Model.get_response``/``stream_response`` call outside the wrapper;
- a new OpenAI client construction site not listed in the matrix.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[4]
SOURCE_ROOTS = [BACKEND_ROOT / "src"]
_packages_root = BACKEND_ROOT.parent / "packages"
if _packages_root.exists():
    SOURCE_ROOTS.append(_packages_root)
MATRIX_CANDIDATES = [
    BACKEND_ROOT.parent / "docs/developer/guides/MODEL_REQUEST_MEASUREMENT_MATRIX.md",
    Path("/app/docs/developer/guides/MODEL_REQUEST_MEASUREMENT_MATRIX.md"),
]

RUNNER_MODULE = "src.lib.openai_agents.runner"
MEASUREMENT_MODULE = "src/lib/openai_agents/model_request_measurement.py"

# Provider request methods that send a model request when called directly.
DIRECT_REQUEST_CHAINS = {
    ("responses", "create"),
    ("responses", "parse"),
    ("responses", "stream"),
    ("responses", "compact"),
    ("completions", "create"),
    ("completions", "parse"),
    ("completions", "stream"),
}

# Every module that constructs an OpenAI SDK client, and how its requests are
# measured. Keep in sync with the matrix document.
CLIENT_CONSTRUCTION_SITES = {
    "src/lib/openai_agents/runner.py": "SafeAsyncOpenAI: SDK models measured at resolution; compact wrapped",
    "src/lib/openai_agents/config.py": "compatible-provider SDK models measured at resolution",
    "src/lib/openai_agents/prompt_utils.py": "call_measured_direct_request",
    "src/lib/benchmarks/adjudication.py": "call_measured_direct_request",
}


def _python_files():
    for root in SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if "tests" in parts or "node_modules" in parts or ".venv" in parts:
                continue
            yield path


def _relative(path: Path) -> str:
    for root in SOURCE_ROOTS:
        try:
            relative = path.relative_to(root.parent)
            return relative.as_posix().removeprefix("backend/")
        except ValueError:
            continue
    return path.as_posix()


def _attribute_chain(node: ast.AST) -> list[str]:
    chain: list[str] = []
    while isinstance(node, ast.Attribute):
        chain.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        chain.append(node.id)
    return list(reversed(chain))


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _module_installs_measurement(tree: ast.Module, module_path: str) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            if _attribute_chain(node.value.func)[-1:] == ["install_model_request_measurement"]:
                return True
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                package = module_path.rsplit("/", 1)[0].replace("/", ".")
                module = f"{package}.{module}" if module else package
            if module == RUNNER_MODULE or (
                module == "src.lib.openai_agents"
                and any(alias.name == "runner" for alias in node.names)
            ):
                return True
    return False


def test_every_runner_call_site_installs_measurement():
    offenders = []
    runner_modules = []
    for path in _python_files():
        tree = _parse(path)
        if tree is None:
            continue
        calls_runner = any(
            isinstance(node, ast.Call)
            and _attribute_chain(node.func)[:1] == ["Runner"]
            and _attribute_chain(node.func)[-1:] in (["run"], ["run_streamed"], ["run_sync"])
            for node in ast.walk(tree)
        )
        if not calls_runner:
            continue
        module_path = _relative(path)
        runner_modules.append(module_path)
        if not _module_installs_measurement(tree, module_path):
            offenders.append(module_path)
    assert runner_modules, "guard found no Runner call sites; scan roots are wrong"
    assert offenders == [], (
        "These modules run Agents SDK models without installing model request "
        f"measurement (call install_model_request_measurement() at import): {offenders}"
    )


def test_no_direct_provider_request_calls_bypass_measurement():
    offenders = []
    for path in _python_files():
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            chain = tuple(_attribute_chain(node.func)[-2:])
            if chain in DIRECT_REQUEST_CHAINS:
                offenders.append(f"{_relative(path)}:{node.lineno}")
    assert offenders == [], (
        "Direct provider requests must go through call_measured_direct_request "
        f"(pass the bound method as call=...): {offenders}"
    )


def test_no_direct_model_calls_outside_measurement_wrapper():
    allowed = {MEASUREMENT_MODULE}
    offenders = []
    for path in _python_files():
        module_path = _relative(path)
        if module_path in allowed:
            continue
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"get_response", "stream_response"}
            ):
                offenders.append(f"{module_path}:{node.lineno}")
    assert offenders == [], (
        "Call models through Runner so the SDK resolution wrapper measures them: "
        f"{offenders}"
    )


def test_client_construction_sites_are_documented_in_matrix():
    found = set()
    for path in _python_files():
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _attribute_chain(node.func)[-1:] in (
                ["AsyncOpenAI"],
                ["OpenAI"],
                ["SafeAsyncOpenAI"],
                ["SafeLangfuseAsyncOpenAI"],
            ):
                found.add(_relative(path))
            if isinstance(node, ast.ClassDef) and any(
                _attribute_chain(base)[-1:] == ["AsyncOpenAI"] for base in node.bases
            ):
                found.add(_relative(path))
    assert found == set(CLIENT_CONSTRUCTION_SITES), (
        "OpenAI client construction sites changed; measure the new path and update "
        f"CLIENT_CONSTRUCTION_SITES and the matrix. found={sorted(found)}"
    )
    matrix = next((path for path in MATRIX_CANDIDATES if path.exists()), None)
    assert matrix is not None, "MODEL_REQUEST_MEASUREMENT_MATRIX.md is missing"
    text = matrix.read_text(encoding="utf-8")
    for module_path in CLIENT_CONSTRUCTION_SITES:
        assert module_path.removeprefix("src/") in text, module_path


def test_importing_specialist_or_studio_runtime_alone_installs_measurement():
    for module in (
        "src.lib.openai_agents.streaming_tools",
        "src.lib.agent_studio.openai_runtime",
        "src.lib.openai_agents.runner",
    ):
        script = (
            f"import {module}\n"
            "from src.lib.openai_agents.model_request_measurement import "
            "model_request_measurement_installed\n"
            "assert model_request_measurement_installed()\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=BACKEND_ROOT,
            env={**os.environ, "PYTHONPATH": str(BACKEND_ROOT)},
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert completed.returncode == 0, (module, completed.stderr[-2000:])
