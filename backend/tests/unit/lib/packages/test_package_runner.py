"""Unit tests for isolated package tool execution."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.lib.packages.env_manager import PackageEnvironmentManager
from . import SHIPPED_TOOLS_PACKAGE_EXPORTS, find_repo_root
from src.lib.packages.package_runner import PackageToolRunner
from src.lib.packages.paths import (
    get_package_runner_metadata_path,
    get_package_runner_state_dir,
    get_package_runner_venv_dir,
)
from src.lib.packages.runner_protocol import PROTOCOL_VERSION, RunnerRequest, encode_request
from src.lib.packages.registry import load_package_registry
from src.lib.packages.tool_registry import load_tool_registry

REPO_ROOT = find_repo_root(Path(__file__))
BACKEND_ROOT = REPO_ROOT / "backend"
ALLIANCE_PACKAGE_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
BACKEND_SRC = REPO_ROOT / "backend" / "src"
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
        "GROUPS_CONFIG_PATH",
        "CONNECTIONS_CONFIG_PATH",
        "CURATION_DB_URL",
        "PYTHONPATH",
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


def test_package_runner_hydrates_backend_context_for_static_sdk_tool(monkeypatch, tmp_path):
    runner = _build_runner(monkeypatch, tmp_path)

    result = runner.execute_tool(
        "sdk_static_context_probe",
        context={
            "trace_id": "trace-123",
            "session_id": "session-456",
            "user_id": "user-789",
            "output_filename_stem": "focus_genes_publication",
        },
    )

    assert result.ok is True
    assert result.result == {
        "trace_id": "trace-123",
        "session_id": "session-456",
        "user_id": "user-789",
        "output_filename_stem": "focus_genes_publication",
    }


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

    expected_python = _venv_python_path(get_package_runner_venv_dir("demo.runner"))
    metadata = json.loads(
        get_package_runner_metadata_path("demo.runner").read_text(encoding="utf-8")
    )
    assert first_result.ok is True
    assert first_result.environment_reused is False
    assert second_result.ok is True
    assert second_result.environment_reused is True
    assert metadata["package_id"] == "demo.runner"
    assert Path(metadata["python_executable"]) == expected_python
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


def test_environment_manager_serializes_concurrent_bootstrap(monkeypatch, tmp_path):
    package_dir = _stage_fixture_package(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    (package_dir / "requirements" / "runtime.txt").write_text(
        "demo-dependency==1.0\n",
        encoding="utf-8",
    )

    registry = load_package_registry(
        package_dir.parent,
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )
    package = registry.get_package("demo.runner")
    assert package is not None

    manager = PackageEnvironmentManager(host_python=Path(sys.executable))
    install_started = threading.Event()
    allow_install_to_finish = threading.Event()
    steps: list[str] = []

    def fake_run_command(command, *, package_id, step):
        steps.append(step)
        if step == "create_venv":
            python_path = get_package_runner_venv_dir(package_id) / "bin" / "python"
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            return
        if step == "install_requirements":
            install_started.set()
            assert allow_install_to_finish.wait(timeout=5)
            return
        raise AssertionError(f"Unexpected step: {step}")

    monkeypatch.setattr(manager, "_run_command", fake_run_command)

    results: list[object] = []
    errors: list[BaseException] = []

    def _bootstrap() -> None:
        try:
            results.append(manager.ensure_environment(package))
        except BaseException as exc:  # pragma: no cover - defensive test harness
            errors.append(exc)

    first = threading.Thread(target=_bootstrap)
    second = threading.Thread(target=_bootstrap)
    first.start()
    assert install_started.wait(timeout=5)
    second.start()

    # The second call should wait for the lock rather than re-running bootstrap.
    assert steps.count("create_venv") == 1
    assert steps.count("install_requirements") == 1

    allow_install_to_finish.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not errors
    assert len(results) == 2
    reused_flags = sorted(result.reused for result in results)
    assert reused_flags == [False, True]
    assert steps.count("create_venv") == 1
    assert steps.count("install_requirements") == 1


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


def test_repo_alliance_tools_package_import_succeeds_without_backend_src(tmp_path):
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
module = importlib.import_module("agr_ai_curation_alliance.tools")
print(
    json.dumps(
        {
            "exports": list(getattr(module, "__all__", [])),
            "loaded": sorted(
                name
                for name in sys.modules
                if name == "agr_ai_curation_alliance.tools"
                or name.startswith("agr_ai_curation_alliance.tools.")
            ),
        }
    )
)
""",
            str(REPO_ROOT),
            str(ALLIANCE_PACKAGE_SRC),
        ],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        text=True,
    )

    assert completed.returncode == 0, (
        "Isolated agr_ai_curation_alliance.tools import failed.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )

    payload = json.loads(completed.stdout)
    assert payload["exports"] == list(SHIPPED_TOOLS_PACKAGE_EXPORTS)
    assert payload["loaded"] == ["agr_ai_curation_alliance.tools"]


def test_alliance_agr_runtime_boundary_loads_from_public_runtime_surface(tmp_path):
    runtime_root = _write_fake_agr_runtime(tmp_path)
    env = os.environ.copy()
    env["AGR_RUNTIME_ROOT"] = str(runtime_root)

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
backend_root = Path(sys.argv[2]).resolve()
backend_src = Path(sys.argv[3]).resolve()
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

sys.path[:] = [str(backend_src), str(backend_root), *dict.fromkeys(clean_paths)]

from agr_ai_curation_runtime import (
    get_curation_resolver,
    is_valid_curie,
    list_groups,
)

print(
    json.dumps(
        {
            "is_valid_curie": is_valid_curie("FB:FBgn0262738"),
            "groups": [
                {"group_id": group.group_id, "taxon": group.taxon}
                for group in list_groups()
            ],
            "connection_url": get_curation_resolver().get_connection_url(),
        }
    )
)
""",
            str(REPO_ROOT),
            str(BACKEND_ROOT),
            str(BACKEND_SRC),
        ],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        text=True,
    )

    assert completed.returncode == 0, (
        "Isolated agr_ai_curation_runtime import failed.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )

    payload = json.loads(completed.stdout)
    assert payload["is_valid_curie"] is True
    assert payload["groups"] == [{"group_id": "FB", "taxon": "NCBITaxon:7227"}]
    assert payload["connection_url"] == "postgresql://db.invalid:5432/curation"


def test_alliance_runtime_requirements_include_public_runtime_deps():
    requirements_path = REPO_ROOT / "packages" / "alliance" / "requirements" / "runtime.txt"
    requirements_text = requirements_path.read_text(encoding="utf-8")

    assert "weaviate-client>=4.0" in requirements_text
    assert "grpcio>=1.72.0" in requirements_text


def test_alliance_agr_curation_module_preserves_group_mapping_load_failure(tmp_path):
    env = os.environ.copy()
    env["GROUPS_CONFIG_PATH"] = str(tmp_path / "missing-groups.yaml")
    env["PYTHONPATH"] = str(_write_fake_agents_dependency(tmp_path))

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
backend_root = Path(sys.argv[2]).resolve()
backend_src = Path(sys.argv[3]).resolve()
package_src = Path(sys.argv[4]).resolve()
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

sys.path[:] = [str(backend_src), str(backend_root), str(package_src), *dict.fromkeys(clean_paths)]

module = importlib.import_module("agr_ai_curation_alliance.tools.agr_curation")
print(
    json.dumps(
        {
            "provider_to_taxon": module.PROVIDER_TO_TAXON,
            "load_error": module._GROUP_MAPPING_LOAD_ERROR,
        }
    )
)
""",
            str(REPO_ROOT),
            str(BACKEND_ROOT),
            str(BACKEND_SRC),
            str(ALLIANCE_PACKAGE_SRC),
        ],
        check=False,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        text=True,
    )

    assert completed.returncode == 0, (
        "Isolated agr_ai_curation_alliance.tools.agr_curation import failed.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )

    payload = json.loads(completed.stdout)
    assert payload["provider_to_taxon"] == {}
    assert "missing-groups.yaml" in (payload["load_error"] or "")


def test_package_runner_executes_alliance_weaviate_bindings_in_isolation(monkeypatch, tmp_path):
    fake_backend_root = _write_fake_weaviate_backend(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(fake_backend_root))

    runner, env_manager = _build_alliance_runner(tmp_path)

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
    _assert_isolated_python(env_manager)

    section_result = runner.execute_tool(
        "read_section",
        kwargs={"section_name": "Methods"},
        context={"document_id": "doc-42", "user_id": "user-9"},
    )

    assert section_result.ok is True
    assert section_result.result == {
        "summary": (
            "Read 2 chunks from 'Materials and Methods'. "
            "Use section.source_chunks[].chunk_id with record_evidence."
        ),
        "section": {
            "section_title": "Materials and Methods",
            "page_numbers": [3, 4],
            "content": "Paragraph one\n\nParagraph two",
            "chunk_count": 2,
            "source_chunks": [
                {
                    "chunk_id": "chunk-methods-1",
                    "page_number": 3,
                    "section_title": "Materials and Methods",
                    "subsection": "Animals",
                    "content_preview": "Paragraph one",
                },
                {
                    "chunk_id": "chunk-methods-2",
                    "page_number": 4,
                    "section_title": "Materials and Methods",
                    "subsection": None,
                    "content_preview": "Paragraph two",
                },
            ],
            "doc_items": [{"id": "bbox-1"}, {"id": "bbox-2"}],
        },
    }


def test_package_runner_executes_alliance_file_output_binding_in_isolation(
    monkeypatch,
    tmp_path,
):
    fake_backend_root = _write_fake_file_output_backend(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(fake_backend_root))
    monkeypatch.setenv("FAKE_TRACE_ID", "feedfacefeedfacefeedfacefeedface")
    monkeypatch.setenv("FAKE_SESSION_ID", "session-42")
    monkeypatch.setenv("FAKE_USER_ID", "user-24")

    runner, env_manager = _build_alliance_runner(tmp_path)

    result = runner.execute_tool(
        "save_csv_file",
        kwargs={
            "data_json": json.dumps(
                [{"gene_id": "FBgn0001", "symbol": "Notch"}]
            ),
            "filename": "gene_results",
        },
    )

    assert result.ok is True
    _assert_isolated_python(env_manager)
    assert result.result["file_id"] == "fake-file-id-001"
    assert result.result["filename"] == (
        "feedfacefeedfacefeedfacefeedface_gene_results.csv"
    )
    assert result.result["format"] == "csv"
    assert result.result["size_bytes"] > 0
    assert result.result["hash_sha256"] == "fake-hash-123"
    assert result.result["mime_type"] == "text/csv"
    assert result.result["download_url"] == "/api/files/fake-file-id-001/download"
    assert result.result["trace_id"] == "feedfacefeedfacefeedfacefeedface"
    assert result.result["session_id"] == "session-42"
    assert result.result["curator_id"] == "user-24"

    output_path = fake_backend_root / "file_outputs" / result.result["filename"]
    assert output_path.read_text(encoding="utf-8") == (
        "gene_id,symbol\nFBgn0001,Notch\n"
    )


def test_package_runner_executes_alliance_agr_curation_binding_in_isolation(
    monkeypatch,
    tmp_path,
):
    runtime_root = _write_fake_agr_runtime(tmp_path)
    fake_dependency_root = _write_fake_agr_curation_api_dependency(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("PYTHONPATH", str(fake_dependency_root))

    runner, env_manager = _build_alliance_runner(tmp_path)

    result = runner.execute_tool(
        "agr_curation_query",
        kwargs={
            "method": "search_genes",
            "gene_symbol": "norpA",
            "data_provider": "FB",
            "limit": 5,
        },
    )

    assert result.ok is True
    assert result.error is None
    _assert_isolated_python(env_manager)
    assert result.result["status"] == "ok"
    assert result.result["count"] == 1
    assert result.result["warnings"] is None
    assert result.result["data"] == [
        {
            "curie": "FB:FBgn0262738",
            "curie_validated": True,
            "match_type": "exact",
            "name": "phospholipase C at 21C",
            "symbol": "norpA",
            "taxon": "NCBITaxon:7227",
        }
    ]


def test_package_runner_entrypoint_resolves_public_runtime_outside_backend_cwd(tmp_path):
    runtime_root = _write_fake_agr_runtime(tmp_path)
    fake_dependency_root = _write_fake_agr_curation_api_dependency(tmp_path)
    fake_agents_root = _write_fake_agents_dependency(tmp_path)
    request = RunnerRequest(
        protocol_version=PROTOCOL_VERSION,
        package_id="agr.alliance",
        package_version="1.0.0",
        package_root=str(REPO_ROOT / "packages" / "alliance"),
        python_package_root="python/src/agr_ai_curation_alliance",
        tool_id="agr_curation_query",
        import_path="agr_ai_curation_alliance.tools.agr_curation:agr_curation_query",
        import_attribute_kind="callable",
        binding_kind="static",
        required_context=[],
        context={},
        args=[],
        kwargs={
            "method": "search_genes",
            "gene_symbol": "norpA",
            "data_provider": "FB",
            "limit": 5,
        },
    )
    env = os.environ.copy()
    env["AGR_RUNTIME_ROOT"] = str(runtime_root)
    env["PYTHONPATH"] = os.pathsep.join(
        (
            str(fake_dependency_root),
            str(fake_agents_root),
        )
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "backend" / "src" / "lib" / "packages" / "package_runner_entrypoint.py"),
        ],
        check=False,
        capture_output=True,
        text=True,
        input=encode_request(request),
        cwd=tmp_path,
        env=env,
    )

    assert completed.returncode == 0, (
        "Package runner entrypoint failed outside backend cwd.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )

    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert payload["result"]["status"] == "ok"
    assert payload["result"]["count"] == 1
    assert payload["result"]["data"][0]["curie"] == "FB:FBgn0262738"


def _build_runner(monkeypatch, tmp_path: Path) -> PackageToolRunner:
    package_dir = _stage_fixture_package(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    return _build_runner_from_packages_dir(package_dir.parent)


def _build_alliance_runner(
    tmp_path: Path,
) -> tuple[PackageToolRunner, "_IsolatedInterpreterEnvironmentManager"]:
    registry = load_tool_registry(
        REPO_ROOT / "packages",
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )
    env_manager = _IsolatedInterpreterEnvironmentManager(tmp_path / "isolated_runner")
    return PackageToolRunner(tool_registry=registry, env_manager=env_manager), env_manager


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


def _assert_isolated_python(
    env_manager: "_IsolatedInterpreterEnvironmentManager",
) -> None:
    assert env_manager.python_executable is not None
    completed = subprocess.run(
        [
            str(env_manager.python_executable),
            "-c",
            (
                "import json, sys; "
                "print(json.dumps({"
                "'executable': sys.executable, "
                "'prefix': sys.prefix, "
                "'base_prefix': sys.base_prefix"
                "}))"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

    payload = json.loads(completed.stdout)
    assert Path(payload["executable"]) == env_manager.python_executable
    assert Path(payload["prefix"]) == env_manager.venv_dir
    assert Path(payload["base_prefix"]) != env_manager.venv_dir


def _venv_python_path(venv_dir: Path) -> Path:
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return venv_dir / bin_dir / executable


class _IsolatedInterpreterEnvironmentManager:
    def __init__(self, venv_dir: Path) -> None:
        self.venv_dir = venv_dir
        self.python_executable: Path | None = None

    def ensure_environment(self, _package):
        reused = self.venv_dir.exists()
        if not reused:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "venv",
                    "--system-site-packages",
                    str(self.venv_dir),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "Failed to create isolated test virtual environment.\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )

        self.python_executable = _venv_python_path(self.venv_dir)
        if not self.python_executable.is_file():
            raise RuntimeError(
                f"Expected virtual environment python at {self.python_executable}"
            )

        return SimpleNamespace(
            python_executable=self.python_executable,
            reused=reused,
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
                \"chunk_id\": \"stale-metadata-search-id\",
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
            \"id\": \"chunk-methods-1\",
            \"text\": \"Paragraph one\",
            \"page_number\": 3,
            \"section_title\": \"Materials and Methods\",
            \"subsection\": \"Animals\",
            \"metadata\": '{\"chunk_id\": \"stale-metadata-methods-1\", \"doc_items\": [{\"id\": \"bbox-1\"}], \"document_id\": \"%s\", \"parent_section\": \"%s\", \"user_id\": \"%s\"}' % (document_id, parent_section, user_id),
        },
        {
            \"chunkId\": \"chunk-methods-2\",
            \"content\": \"Paragraph two\",
            \"pageNumber\": 4,
            \"sectionTitle\": \"Materials and Methods\",
            \"metadata\": {\"chunk_id\": \"stale-metadata-methods-2\", \"doc_items\": [{\"id\": \"bbox-2\"}]},
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


def _write_fake_file_output_backend(tmp_path: Path) -> Path:
    backend_root = tmp_path / "fake_backend"
    file_outputs_root = backend_root / "src" / "lib" / "file_outputs"
    models_root = backend_root / "src" / "models" / "sql"
    file_output_dir = backend_root / "file_outputs"
    file_output_dir.mkdir(parents=True)

    for directory in (
        backend_root / "src",
        backend_root / "src" / "lib",
        file_outputs_root,
        backend_root / "src" / "models",
        models_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")

    (backend_root / "src" / "lib" / "context.py").write_text(
        """import os


def get_current_trace_id():
    return os.environ.get("FAKE_TRACE_ID")


def get_current_session_id():
    return os.environ.get("FAKE_SESSION_ID")


def get_current_user_id():
    return os.environ.get("FAKE_USER_ID")
""",
        encoding="utf-8",
    )

    (file_outputs_root / "storage.py").write_text(
        """from pathlib import Path


class FileOutputStorageService:
    def save_output(self, *, trace_id, session_id, content, file_type, descriptor):
        output_dir = Path(__file__).resolve().parents[3] / "file_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"{trace_id}_{descriptor}.{file_type}"
        file_path.write_text(content, encoding="utf-8")
        return file_path, "fake-hash-123", len(content.encode("utf-8")), ["fake warning"]
""",
        encoding="utf-8",
    )

    (models_root / "database.py").write_text(
        """class _Session:
    def add(self, _file_output):
        return None

    def commit(self):
        return None

    def refresh(self, _file_output):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def SessionLocal():
    return _Session()
""",
        encoding="utf-8",
    )

    (models_root / "file_output.py").write_text(
        """class FileOutput:
    def __init__(self, **kwargs):
        self.id = "fake-file-id-001"
        self.__dict__.update(kwargs)
""",
        encoding="utf-8",
    )

    return backend_root


def _write_fake_agr_runtime(tmp_path: Path) -> Path:
    runtime_root = tmp_path / "runtime"
    config_dir = runtime_root / "config"
    prefix_dir = runtime_root / "state" / "identifier_prefixes"
    config_dir.mkdir(parents=True, exist_ok=True)
    prefix_dir.mkdir(parents=True, exist_ok=True)

    (config_dir / "groups.yaml").write_text(
        """identity_provider:
  type: cognito
  group_claim: cognito:groups
groups:
  FB:
    name: FlyBase
    taxon: NCBITaxon:7227
    provider_groups:
      - fb-curators
""",
        encoding="utf-8",
    )
    (config_dir / "connections.yaml").write_text(
        """services:
  curation_db:
    url: postgresql://db.invalid:5432/curation
    credentials:
      source: env
""",
        encoding="utf-8",
    )
    (prefix_dir / "identifier_prefixes.json").write_text(
        json.dumps({"prefixes": ["FB"]}),
        encoding="utf-8",
    )
    return runtime_root


def _write_fake_agr_curation_api_dependency(tmp_path: Path) -> Path:
    dependency_root = tmp_path / "fake_external"
    package_root = dependency_root / "agr_curation_api"
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "db_methods.py").write_text(
        """class DatabaseConfig:
    def __init__(self):
        self.username = None
        self.password = None
        self.database = None
        self.host = None
        self.port = None


class _Display:
    def __init__(self, text):
        self.displayText = text


class _Gene:
    def __init__(self, curie, symbol, name, taxon):
        self.primaryExternalId = curie
        self.geneSymbol = _Display(symbol)
        self.geneFullName = _Display(name)
        self.taxon = taxon
        self.geneType = None


class DatabaseMethods:
    def __init__(self, config):
        self.config = config

    def search_entities(self, entity_type, search_pattern, taxon_curie, include_synonyms, limit):
        if (
            entity_type == "gene"
            and search_pattern == "norpA"
            and taxon_curie == "NCBITaxon:7227"
        ):
            return [
                {
                    "entity_curie": "FB:FBgn0262738",
                    "entity": "norpA",
                    "match_type": "exact",
                }
            ]
        return []

    def get_gene(self, gene_id):
        if gene_id == "FB:FBgn0262738":
            return _Gene(
                "FB:FBgn0262738",
                "norpA",
                "phospholipase C at 21C",
                "NCBITaxon:7227",
            )
        return None

    def get_data_providers(self):
        return [("FB", "NCBITaxon:7227")]
""",
        encoding="utf-8",
    )
    return dependency_root


def _write_fake_agents_dependency(tmp_path: Path) -> Path:
    dependency_root = tmp_path / "fake_agents"
    package_root = dependency_root / "agents"
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "__init__.py").write_text(
        """def function_tool(*decorator_args, **decorator_kwargs):
    def decorate(func):
        return func

    if decorator_args and callable(decorator_args[0]) and not decorator_kwargs:
        return decorate(decorator_args[0])
    return decorate
""",
        encoding="utf-8",
    )
    return dependency_root
