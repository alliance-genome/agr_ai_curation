"""Unit tests for isolated package tool execution."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.lib.packages.package_runner import PackageToolRunner
from src.lib.packages.paths import (
    get_package_runner_metadata_path,
    get_package_runner_state_dir,
    get_package_runner_venv_dir,
)
from src.lib.packages.tool_registry import load_tool_registry

FIXTURE_PACKAGE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "package_runner" / "demo_runner"
)


@pytest.fixture(autouse=True)
def _clear_runtime_path_env(monkeypatch):
    for variable in (
        "AGR_RUNTIME_ROOT",
        "AGR_RUNTIME_CONFIG_DIR",
        "AGR_RUNTIME_PACKAGES_DIR",
        "AGR_RUNTIME_STATE_DIR",
        "AGR_RUNTIME_OVERRIDES_PATH",
        "PDF_STORAGE_PATH",
        "PDFX_JSON_STORAGE_PATH",
        "PROCESSED_JSON_STORAGE_PATH",
        "FILE_OUTPUT_STORAGE_PATH",
        "IDENTIFIER_PREFIX_STATE_DIR",
        "IDENTIFIER_PREFIX_FILE_PATH",
    ):
        monkeypatch.delenv(variable, raising=False)


def test_package_runner_executes_static_callable(monkeypatch, tmp_path):
    runner = _build_runner(monkeypatch, tmp_path)

    result = runner.execute_tool(
        "echo_value",
        kwargs={"value": "hello", "prefix": "pre-"},
    )

    assert result.ok is True
    assert result.error is None
    assert result.result == {"value": "pre-hello"}
    assert result.environment_reused is False
    assert get_package_runner_venv_dir("demo.runner").is_dir()


def test_package_runner_executes_context_factory(monkeypatch, tmp_path):
    runner = _build_runner(monkeypatch, tmp_path)

    result = runner.execute_tool(
        "build_message",
        kwargs={"subject": "Curation", "punctuation": "?"},
        context={"document_id": "DOC-1", "user_id": "user-7"},
    )

    assert result.ok is True
    assert result.result == {"message": "Curation for DOC-1 by user-7?"}


def test_package_runner_reuses_existing_virtual_environment(monkeypatch, tmp_path):
    runner = _build_runner(monkeypatch, tmp_path)

    first_result = runner.execute_tool("echo_value", kwargs={"value": "one"})
    second_result = runner.execute_tool("echo_value", kwargs={"value": "two"})

    metadata = json.loads(
        get_package_runner_metadata_path("demo.runner").read_text(encoding="utf-8")
    )
    assert first_result.ok is True
    assert first_result.environment_reused is False
    assert second_result.ok is True
    assert second_result.environment_reused is True
    assert metadata["package_id"] == "demo.runner"
    assert get_package_runner_state_dir() == tmp_path / "runtime" / "state" / "package_runner"


def test_package_runner_returns_bootstrap_failure(monkeypatch, tmp_path):
    package_dir = _stage_fixture_package(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    (package_dir / "requirements" / "runtime.txt").write_text(
        "not a valid requirement ===\n",
        encoding="utf-8",
    )

    runner = _build_runner_from_packages_dir(package_dir.parent)
    result = runner.execute_tool("echo_value", kwargs={"value": "hello"})

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "bootstrap_failure"
    assert result.error.details["step"] == "install_requirements"


def test_package_runner_returns_import_failure(monkeypatch, tmp_path):
    package_dir = _stage_fixture_package(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    bindings_path = package_dir / "tools" / "bindings.yaml"
    bindings_text = bindings_path.read_text(encoding="utf-8").replace(
        "demo_runner.tools:echo_value",
        "demo_runner.tools:missing_value",
        1,
    )
    bindings_path.write_text(bindings_text, encoding="utf-8")

    runner = _build_runner_from_packages_dir(package_dir.parent)
    result = runner.execute_tool("echo_value", kwargs={"value": "hello"})

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "import_failure"
    assert "missing_value" in result.error.message


def test_package_runner_returns_execution_failure(monkeypatch, tmp_path):
    runner = _build_runner(monkeypatch, tmp_path)

    result = runner.execute_tool("explode_value", kwargs={"value": "kaboom"})

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "execution_failure"
    assert "boom: kaboom" in result.error.message


def test_package_runner_returns_bad_runner_response(monkeypatch, tmp_path):
    runner = _build_runner(monkeypatch, tmp_path)
    original_run = subprocess.run

    def fake_run(*args, **kwargs):
        command = args[0]
        if (
            isinstance(command, list)
            and len(command) == 2
            and command[1].endswith("package_runner_entrypoint.py")
        ):
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="not-json",
                stderr="",
            )
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.execute_tool("echo_value", kwargs={"value": "hello"})

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "bad_runner_response"
    assert result.error.details["stdout"] == "not-json"


def test_package_runner_returns_timeout_failure(monkeypatch, tmp_path):
    runner = _build_runner(monkeypatch, tmp_path)
    original_run = subprocess.run

    def fake_run(*args, **kwargs):
        command = args[0]
        if (
            isinstance(command, list)
            and any(
                isinstance(part, str)
                and part.endswith("package_runner_entrypoint.py")
                for part in command
            )
        ):
            raise subprocess.TimeoutExpired(
                cmd=command,
                timeout=kwargs["timeout"],
            )
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = runner.execute_tool("echo_value", kwargs={"value": "hello"})

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "execution_failure"
    assert "Timed out while executing package tool 'echo_value'" == result.error.message
    assert result.error.details["timeout_seconds"] == 60.0


def _build_runner(monkeypatch, tmp_path: Path) -> PackageToolRunner:
    package_dir = _stage_fixture_package(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    return _build_runner_from_packages_dir(package_dir.parent)


def _build_runner_from_packages_dir(packages_dir: Path) -> PackageToolRunner:
    registry = load_tool_registry(
        packages_dir,
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )
    return PackageToolRunner(tool_registry=registry)


def _stage_fixture_package(tmp_path: Path) -> Path:
    packages_dir = tmp_path / "runtime" / "packages"
    package_dir = packages_dir / FIXTURE_PACKAGE_DIR.name
    shutil.copytree(FIXTURE_PACKAGE_DIR, package_dir)
    return package_dir
