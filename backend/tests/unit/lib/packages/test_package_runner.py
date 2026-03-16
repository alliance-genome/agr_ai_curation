"""Unit tests for isolated package tool execution."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import CORE_TOOLS_PACKAGE_EXPORTS, find_repo_root
from src.lib.packages.package_runner import PackageToolRunner
from src.lib.packages.paths import (
    get_package_runner_metadata_path,
    get_package_runner_state_dir,
    get_package_runner_venv_dir,
)
from src.lib.packages.tool_registry import load_tool_registry

REPO_ROOT = find_repo_root(Path(__file__))
CORE_PACKAGE_SRC = REPO_ROOT / "packages" / "core" / "python" / "src"
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


def test_package_runner_executes_static_sdk_style_tool(monkeypatch, tmp_path):
    runner = _build_runner(monkeypatch, tmp_path)

    result = runner.execute_tool(
        "sdk_static_tool",
        kwargs={"message": "hello", "punctuation": "?"},
    )

    assert result.ok is True
    assert result.result == {"message": "static:hello?"}


def test_package_runner_executes_context_factory_sdk_style_tool(monkeypatch, tmp_path):
    runner = _build_runner(monkeypatch, tmp_path)

    result = runner.execute_tool(
        "sdk_context_tool",
        kwargs={"message": "hello"},
        context={"document_id": "DOC-9", "user_id": "user-4"},
    )

    assert result.ok is True
    assert result.result == {"message": "DOC-9:user-4:hello!"}


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


def test_repo_core_tools_package_import_succeeds_without_backend_src(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
package_src = Path(sys.argv[2]).resolve()
clean_paths = []
for entry in sys.path:
    if not entry:
        continue
    try:
        resolved = Path(entry).resolve()
    except Exception:
        continue
    if resolved == repo_root or repo_root in resolved.parents:
        continue
    clean_paths.append(str(resolved))

sys.path[:] = [str(package_src), *dict.fromkeys(clean_paths)]
module = importlib.import_module("agr_ai_curation_core.tools")
print(
    json.dumps(
        {
            "exports": list(getattr(module, "__all__", [])),
            "loaded": sorted(
                name
                for name in sys.modules
                if name == "agr_ai_curation_core.tools"
                or name.startswith("agr_ai_curation_core.tools.")
            ),
        }
    )
)
""",
            str(REPO_ROOT),
            str(CORE_PACKAGE_SRC),
        ],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        text=True,
    )

    assert completed.returncode == 0, (
        "Isolated agr_ai_curation_core.tools import failed.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )

    payload = json.loads(completed.stdout)
    assert payload["exports"] == list(CORE_TOOLS_PACKAGE_EXPORTS)
    assert payload["loaded"] == ["agr_ai_curation_core.tools"]


def test_package_runner_executes_core_weaviate_bindings_in_isolation(monkeypatch, tmp_path):
    fake_backend_root = _write_fake_weaviate_backend(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(fake_backend_root))

    registry = load_tool_registry(
        REPO_ROOT / "packages",
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )
    runner = PackageToolRunner(
        tool_registry=registry,
        env_manager=_CurrentInterpreterEnvironmentManager(),
    )

    search_result = runner.execute_tool(
        "search_document",
        kwargs={"query": "wg", "limit": 5, "section_keywords": ["Results"]},
        context={"document_id": "doc-42", "user_id": "user-9"},
    )

    assert search_result.ok is True
    assert search_result.result == {
        "summary": "Found 1 chunks",
        "hits": [
            {
                "chunk_id": "chunk-search-1",
                "section_title": "Results",
                "page_number": 7,
                "score": 0.91,
                "content": "Wingless expression expanded in the mutant tissue.",
                "doc_items": [{"id": "bbox-search"}],
            }
        ],
    }

    section_result = runner.execute_tool(
        "read_section",
        kwargs={"section_name": "Methods"},
        context={"document_id": "doc-42", "user_id": "user-9"},
    )

    assert section_result.ok is True
    assert section_result.result == {
        "summary": "Read 2 chunks from 'Materials and Methods'",
        "section": {
            "section_title": "Materials and Methods",
            "page_numbers": [3, 4],
            "content": "Paragraph one\n\nParagraph two",
            "chunk_count": 2,
            "doc_items": [{"id": "bbox-1"}, {"id": "bbox-2"}],
        },
    }


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


class _CurrentInterpreterEnvironmentManager:
    def ensure_environment(self, _package):
        return SimpleNamespace(
            python_executable=Path(sys.executable),
            reused=False,
        )


def _write_fake_weaviate_backend(tmp_path: Path) -> Path:
    backend_root = tmp_path / "fake_backend"
    package_root = backend_root / "src" / "lib" / "weaviate_client"
    package_root.mkdir(parents=True)

    for directory in (
        backend_root / "src",
        backend_root / "src" / "lib",
        backend_root / "src" / "lib" / "weaviate_client",
    ):
        (directory / "__init__.py").write_text("", encoding="utf-8")

    (package_root / "chunks.py").write_text(
        """async def hybrid_search_chunks(*, document_id, query, user_id, limit=10, section_keywords=None, apply_mmr=True, strategy=\"hybrid\", **_kwargs):
    return [
        {
            \"id\": \"chunk-search-1\",
            \"score\": 0.91,
            \"text\": \"Wingless expression expanded in the mutant tissue.\",
            \"metadata\": {
                \"chunk_id\": \"chunk-search-1\",
                \"section_title\": \"Results\",
                \"page_number\": 7,
                \"doc_items\": [{\"id\": \"bbox-search\"}],
                \"document_id\": document_id,
                \"query\": query,
                \"user_id\": user_id,
                \"limit\": limit,
                \"section_keywords\": section_keywords,
                \"apply_mmr\": apply_mmr,
                \"strategy\": strategy,
            },
        }
    ]


async def get_document_sections(*_args, **_kwargs):
    return [{\"title\": \"Methods\", \"page_number\": 3, \"chunk_count\": 2}]


async def get_chunks_by_parent_section(*, document_id, parent_section, user_id, **_kwargs):
    return [
        {
            \"text\": \"Paragraph one\",
            \"page_number\": 3,
            \"section_title\": \"Materials and Methods\",
            \"metadata\": '{\"doc_items\": [{\"id\": \"bbox-1\"}], \"document_id\": \"%s\", \"parent_section\": \"%s\", \"user_id\": \"%s\"}' % (document_id, parent_section, user_id),
        },
        {
            \"content\": \"Paragraph two\",
            \"pageNumber\": 4,
            \"sectionTitle\": \"Materials and Methods\",
            \"metadata\": {\"doc_items\": [{\"id\": \"bbox-2\"}]},
        },
    ]


async def get_chunks_by_subsection(*, document_id, parent_section, subsection, user_id, **_kwargs):
    return [
        {
            \"text\": \"Subsection paragraph\",
            \"page_number\": 5,
            \"doc_items\": [{\"id\": \"bbox-subsection\"}],
            \"metadata\": {
                \"document_id\": document_id,
                \"parent_section\": parent_section,
                \"subsection\": subsection,
                \"user_id\": user_id,
            },
        }
    ]
""",
        encoding="utf-8",
    )

    return backend_root
