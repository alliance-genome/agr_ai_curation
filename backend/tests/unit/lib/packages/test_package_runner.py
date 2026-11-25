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
import yaml
from packaging.requirements import Requirement
from sqlalchemy import create_engine

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
        "AGR_PACKAGE_ENV_BOOTSTRAP_MODE",
        "AGR_PREBUILT_PACKAGE_ENVS_DIR",
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


@pytest.fixture(scope="module")
def _shared_alliance_runner(tmp_path_factory):
    # The Alliance binding tests vary per-test fake runtimes via monkeypatch,
    # but they do not mutate the Alliance package manifest or requirements.
    # Reusing this venv avoids paying python -m venv for every binding test.
    registry = load_tool_registry(
        REPO_ROOT / "packages",
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )
    env_manager = _IsolatedInterpreterEnvironmentManager(
        tmp_path_factory.mktemp("alliance_package_runner") / "isolated_runner"
    )
    return PackageToolRunner(tool_registry=registry, env_manager=env_manager), env_manager


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


def _demo_package(monkeypatch, tmp_path):
    package_dir = _stage_fixture_package(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    (package_dir / "requirements" / "runtime.txt").write_text(
        "demo-dependency\n", encoding="utf-8",
    )
    registry = load_package_registry(
        package_dir.parent,
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )
    return registry.get_package("demo.runner")


def _recording_manager(monkeypatch, constraints_path):
    manager = PackageEnvironmentManager(
        host_python=Path(sys.executable), constraints_path=constraints_path,
    )
    commands: list[list[str]] = []

    def fake_run_command(command, *, package_id, step):
        commands.append(command)
        if step == "create_venv":
            python_path = get_package_runner_venv_dir(package_id) / "bin" / "python"
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    monkeypatch.setattr(manager, "_run_command", fake_run_command)
    return manager, commands


def test_package_environment_installs_with_the_backend_lock_as_constraints(monkeypatch, tmp_path):
    from src.lib.packages.env_manager import BACKEND_DEPENDENCY_LOCK

    assert BACKEND_DEPENDENCY_LOCK == BACKEND_ROOT / "requirements.lock.txt"
    assert BACKEND_DEPENDENCY_LOCK.is_file()
    package = _demo_package(monkeypatch, tmp_path)
    manager, commands = _recording_manager(monkeypatch, BACKEND_DEPENDENCY_LOCK)

    environment = manager.ensure_environment(package)

    assert commands[-1][-4:] == [
        "-r", str(environment.requirements_path), "-c", str(BACKEND_DEPENDENCY_LOCK),
    ]
    metadata = json.loads(
        get_package_runner_metadata_path(package.package_id).read_text(encoding="utf-8"))
    assert metadata["constraints_path"] == str(BACKEND_DEPENDENCY_LOCK)


def test_changed_backend_lock_rebuilds_the_package_environment(monkeypatch, tmp_path):
    package = _demo_package(monkeypatch, tmp_path)
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("demo-dependency==1.0\n", encoding="utf-8")
    manager, commands = _recording_manager(monkeypatch, lock)

    first = manager.ensure_environment(package)
    assert manager.ensure_environment(package).reused is True
    lock.write_text("demo-dependency==2.0\n", encoding="utf-8")
    rebuilt = manager.ensure_environment(package)

    assert rebuilt.fingerprint != first.fingerprint
    assert rebuilt.reused is False
    assert [command for command in commands if "-c" in command][-1][-1] == str(lock)


def test_missing_backend_lock_fails_the_environment_build(monkeypatch, tmp_path):
    from src.lib.packages.env_manager import PackageEnvironmentBootstrapError

    package = _demo_package(monkeypatch, tmp_path)
    manager, commands = _recording_manager(monkeypatch, tmp_path / "absent.lock.txt")

    with pytest.raises(PackageEnvironmentBootstrapError, match="Backend dependency lock not found"):
        manager.ensure_environment(package)
    assert commands == []


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
    assert result.error.details["timeout_seconds"] == 120.0


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


def test_alliance_sqlalchemy_pin_matches_backend_lock():
    def sqlalchemy_requirement(path):
        requirements = [
            Requirement(line.strip())
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        return next(req for req in requirements if req.name.lower() == "sqlalchemy")

    package_requirement = sqlalchemy_requirement(
        REPO_ROOT / "packages" / "alliance" / "requirements" / "runtime.txt"
    )
    backend_requirement = sqlalchemy_requirement(BACKEND_ROOT / "requirements.lock.txt")
    assert package_requirement.specifier == backend_requirement.specifier
    assert len(package_requirement.specifier) == 1
    assert next(iter(package_requirement.specifier)).operator == "=="


def test_plain_postgresql_url_loads_installed_psycopg2_without_connecting():
    # Constructing the engine loads the DBAPI but does not open a connection.
    engine = create_engine("postgresql://fixture:fixture@127.0.0.1/fixture")
    try:
        assert engine.dialect.driver == "psycopg2"
        assert engine.dialect.dbapi.__name__ == "psycopg2"
    finally:
        engine.dispose()


def test_alliance_document_bindings_use_canonical_package_factories():
    bindings_path = REPO_ROOT / "packages" / "alliance" / "tools" / "bindings.yaml"
    bindings = yaml.safe_load(bindings_path.read_text(encoding="utf-8"))
    tools_by_id = {tool["tool_id"]: tool for tool in bindings["tools"]}

    expected_factories = {
        "search_document": "create_search_document_tool",
        "read_chunk": "create_read_chunk_tool",
        "read_section": "create_read_section_tool",
        "read_subsection": "create_read_subsection_tool",
    }
    for tool_id, factory_name in expected_factories.items():
        binding = tools_by_id[tool_id]
        assert binding["binding_kind"] == "context_factory"
        assert binding["callable_factory"] == (
            f"agr_ai_curation_alliance.tools.documents:{factory_name}"
        )
        assert binding["source_file"] == (
            "python/src/agr_ai_curation_alliance/tools/documents.py"
        )


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


def test_package_runner_executes_alliance_weaviate_bindings_in_isolation(
    monkeypatch,
    tmp_path,
    _shared_alliance_runner,
):
    fake_backend_root = _write_fake_weaviate_backend(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(fake_backend_root))

    runner, env_manager = _shared_alliance_runner

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
                "chunk_index": 4,
                "section_title": "Results",
                "page_number": 7,
                "score": 0.91,
                "content": "Wingless expression expanded in the mutant tissue.",
                "doc_items": [{"id": "bbox-search"}],
                "tables": None,
            }
        ],
        "error_code": None,
        "result_bounds": None,
    }
    _assert_isolated_python(env_manager)

    chunk_result = runner.execute_tool(
        "read_chunk",
        kwargs={"chunk_index": 4},
        context={"document_id": "doc-42", "user_id": "user-9"},
    )

    assert chunk_result.ok is True
    assert chunk_result.result["summary"] == (
        "Read chunk 4 from page 7. "
        "Select chunk_index/span_index pairs for record_evidence."
    )
    assert chunk_result.result["chunk"]["chunk_id"] == "chunk-search-1"
    assert chunk_result.result["chunk"]["chunk_index"] == 4
    assert chunk_result.result["chunk"]["chunk_number"] == 5
    assert chunk_result.result["chunk"]["previous_chunk_id"] == "chunk-search-0"
    assert chunk_result.result["chunk"]["next_chunk_id"] == "chunk-search-2"
    assert chunk_result.result["chunk"]["content"] == (
        "Wingless expression expanded in the mutant tissue. "
        "This sentence is exact evidence."
    )
    assert [span["text"] for span in chunk_result.result["chunk"]["evidence_spans"]] == [
        "Wingless expression expanded in the mutant tissue.",
        "This sentence is exact evidence.",
    ]
    assert chunk_result.result["chunk"]["evidence_spans"][0]["span_id"].startswith(
        "chunk-search-1:s0000:c0000-c0050:"
    )
    assert "evidence_spans" not in search_result.result["hits"][0]

    lexical_result = runner.execute_tool(
        "search_document",
        kwargs={"query": "strategy-check", "search_mode": "lexical"},
        context={"document_id": "doc-42", "user_id": "user-9"},
    )

    assert lexical_result.ok is True
    assert lexical_result.result["hits"][0]["section_title"] == "lexical"
    assert lexical_result.result["hits"][0]["content"] == "strategy=lexical"

    auto_result = runner.execute_tool(
        "search_document",
        kwargs={"query": "strategy-check", "search_mode": "auto"},
        context={"document_id": "doc-42", "user_id": "user-9"},
    )

    assert auto_result.ok is True
    assert auto_result.result["hits"][0]["section_title"] == "hybrid"
    assert auto_result.result["hits"][0]["content"] == "strategy=hybrid"

    monkeypatch.setenv("WEAVIATE_SEARCH_INITIAL_LIMIT", "77")
    monkeypatch.setenv("WEAVIATE_SEARCH_HYBRID_ALPHA", "0.6")
    monkeypatch.setenv("WEAVIATE_SEARCH_MMR_ENABLED", "true")
    monkeypatch.setenv("WEAVIATE_SEARCH_MMR_LAMBDA", "0.9")
    config_result = runner.execute_tool(
        "search_document",
        kwargs={"query": "config-check"},
        context={"document_id": "doc-42", "user_id": "user-9"},
    )

    assert config_result.ok is True
    assert config_result.result["hits"][0]["content"] == (
        "initial=77 alpha=0.6 mmr=True lambda=0.9"
    )

    section_result = runner.execute_tool(
        "read_section",
        kwargs={"section_name": "Methods"},
        context={"document_id": "doc-42", "user_id": "user-9"},
    )

    assert section_result.ok is True
    section = section_result.result["section"]
    assert section_result.result["summary"].startswith("Read all 2 chunks of 'Methods'.")
    assert {key: section[key] for key in (
        "requested_section", "file", "page_numbers", "total_chunk_count",
        "text_contains", "matched_chunk_count",
    )} == {
        "requested_section": "Methods", "file": "paper.pdf",
        "page_numbers": [3, 4], "total_chunk_count": 2, "text_contains": None,
        "matched_chunk_count": None,
    }
    assert [
        (p["chunk_id"], p["chunk_index"], p["page_number"], p["subsection"], p["content"], p["doc_items"])
        for p in section["passages"]
    ] == [
        ("chunk-methods-1", 0, 3, "Animals", "Paragraph one", [{"id": "bbox-1"}]),
        ("chunk-methods-2", 1, 4, None, "Paragraph two", [{"id": "bbox-2"}]),
    ]
    assert [[span["text"] for span in p["evidence_spans"]] for p in section["passages"]] == [
        ["Paragraph one"], ["Paragraph two"],
    ]
    assert not {"content", "source_chunks", "offset", "next_offset"} & set(section)


def test_package_runner_does_not_expose_removed_raw_file_output_binding(
    _shared_alliance_runner,
):
    runner, env_manager = _shared_alliance_runner

    result = runner.execute_tool(
        "save_csv_file",
        kwargs={},
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "tool_not_found"
    _assert_isolated_python(env_manager)


def test_package_runner_executes_alliance_agr_curation_binding_in_isolation(
    monkeypatch,
    tmp_path,
    _shared_alliance_runner,
):
    runtime_root = _write_fake_agr_runtime(tmp_path)
    fake_dependency_root = _write_fake_agr_curation_api_dependency(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("PYTHONPATH", str(fake_dependency_root))

    runner, env_manager = _shared_alliance_runner

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


def test_package_runner_alliance_agr_bulk_contract_reports_no_matches(
    monkeypatch,
    tmp_path,
    _shared_alliance_runner,
):
    runtime_root = _write_fake_agr_runtime(tmp_path)
    fake_dependency_root = _write_fake_agr_curation_api_dependency(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("PYTHONPATH", str(fake_dependency_root))

    runner, env_manager = _shared_alliance_runner

    result = runner.execute_tool(
        "agr_curation_query",
        kwargs={
            "method": "search_genes_bulk",
            "gene_symbols": ["NopeA", "NopeB"],
            "data_provider": "FB",
            "limit": 5,
        },
    )

    assert result.ok is True
    assert result.error is None
    _assert_isolated_python(env_manager)
    assert result.result["status"] == "ok"
    assert result.result["count"] == 0
    assert result.result["lookup_status"] == "not_found"
    assert result.result["failure_classification"] == "not_found"
    assert result.result["data"]["requested_count"] == 2
    assert result.result["data"]["resolved_count"] == 0
    assert result.result["data"]["total_matches"] == 0
    assert result.result["data"]["resolution_status"] == "no_matches"
    assert result.result["data"]["status_counts"] == {"no_matches": 2}
    assert [item["status"] for item in result.result["data"]["items"]] == [
        "no_matches",
        "no_matches",
    ]


def test_package_runner_alliance_agr_data_provider_helper_in_isolation(
    monkeypatch,
    tmp_path,
    _shared_alliance_runner,
):
    runtime_root = _write_fake_agr_runtime(tmp_path)
    fake_dependency_root = _write_fake_agr_curation_api_dependency(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("PYTHONPATH", str(fake_dependency_root))

    runner, env_manager = _shared_alliance_runner

    result = runner.execute_tool(
        "agr_curation_query",
        kwargs={"method": "get_data_providers"},
    )

    assert result.ok is True
    assert result.error is None
    _assert_isolated_python(env_manager)
    assert result.result["status"] == "ok"
    assert result.result["count"] == 1
    assert result.result["data"] == [
        {
            "abbreviation": "FB",
            "taxon_id": "NCBITaxon:7227",
            "display_name": "FlyBase",
        }
    ]

    lookup_result = runner.execute_tool(
        "agr_curation_query",
        kwargs={
            "method": "get_data_provider",
            "abbreviation": "FB",
            "taxon_id": "NCBITaxon:7227",
        },
    )
    assert lookup_result.ok is True
    assert lookup_result.result["lookup_status"] == "success"
    assert lookup_result.result["data"]["matches"] == [
        {
            "abbreviation": "FB",
            "taxon_id": "NCBITaxon:7227",
            "display_name": "FlyBase",
        }
    ]


def test_package_runner_alliance_agr_ontology_helpers_in_isolation(
    monkeypatch,
    tmp_path,
    _shared_alliance_runner,
):
    runtime_root = _write_fake_agr_runtime(tmp_path)
    fake_dependency_root = _write_fake_agr_curation_api_dependency(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("PYTHONPATH", str(fake_dependency_root))

    runner, env_manager = _shared_alliance_runner

    get_result = runner.execute_tool(
        "agr_curation_query",
        kwargs={
            "method": "get_ontology_term",
            "term": "GO:0003674",
            "ontology_term_type": "GOTerm",
        },
    )
    bulk_result = runner.execute_tool(
        "agr_curation_query",
        kwargs={
            "method": "get_ontology_terms",
            "terms": ["GO:0003674", "GO:missing"],
        },
    )
    search_result = runner.execute_tool(
        "agr_curation_query",
        kwargs={
            "method": "search_ontology_terms",
            "term": "molecular",
            "ontology_term_type": "GOTerm",
            "exact_match": True,
            "include_synonyms": False,
            "limit": 3,
        },
    )

    assert get_result.ok is True
    assert bulk_result.ok is True
    assert search_result.ok is True
    _assert_isolated_python(env_manager)
    assert get_result.result["data"] == {
        "curie": "GO:0003674",
        "curie_validated": True,
        "definition": "Elemental activities of a gene product.",
        "name": "molecular_function",
        "namespace": "molecular_function",
        "ontology_type": "GOTerm",
        "synonyms": ["activity"],
    }
    assert bulk_result.result["count"] == 1
    assert bulk_result.result["data"][0]["curie"] == "GO:0003674"
    assert search_result.result["count"] == 1
    assert search_result.result["data"][0]["name"] == "molecular_function"


def test_package_runner_alliance_literature_reference_tool_in_isolation(
    monkeypatch,
    tmp_path,
    _shared_alliance_runner,
):
    fake_dependency_root = _write_fake_agr_curation_api_dependency(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(fake_dependency_root))

    runner, env_manager = _shared_alliance_runner

    result = runner.execute_tool(
        "agr_literature_reference_lookup",
        kwargs={
            "method": "get_literature_reference",
            "identifier": "PMID:27528223",
        },
    )

    assert result.ok is True
    _assert_isolated_python(env_manager)
    assert result.result["status"] == "ok"
    assert result.result["lookup_status"] == "success"
    assert result.result["source"] == "literature_es"
    assert result.result["resolved_reference"]["curie"] == "AGRKB:101000000924191"
    assert result.result["resolved_reference"]["matched_identifier"] == "PMID:27528223"


def test_package_runner_alliance_agr_entity_helpers_in_isolation(
    monkeypatch,
    tmp_path,
    _shared_alliance_runner,
):
    runtime_root = _write_fake_agr_runtime(tmp_path)
    fake_dependency_root = _write_fake_agr_curation_api_dependency(tmp_path)
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("PYTHONPATH", str(fake_dependency_root))

    runner, env_manager = _shared_alliance_runner

    names_result = runner.execute_tool(
        "agr_curation_query",
        kwargs={
            "method": "map_entity_names_to_curies",
            "entity_type": "gene",
            "entity_names": ["norpA"],
            "data_provider": "FB",
        },
    )
    curies_result = runner.execute_tool(
        "agr_curation_query",
        kwargs={
            "method": "map_entity_curies_to_info",
            "entity_type": "gene",
            "entity_curies": ["FB:FBgn0262738"],
        },
    )
    names_by_curie_result = runner.execute_tool(
        "agr_curation_query",
        kwargs={
            "method": "map_curies_to_names",
            "category": "gene",
            "curies": ["FB:FBgn0262738"],
        },
    )

    assert names_result.ok is True
    assert curies_result.ok is True
    assert names_by_curie_result.ok is True
    _assert_isolated_python(env_manager)
    assert names_result.result["data"] == [
        {
            "curie": "FB:FBgn0262738",
            "curie_validated": True,
            "entity": "norpA",
            "entity_curie": "FB:FBgn0262738",
            "match_type": "exact",
            "name": "norpA",
            "symbol": "norpA",
        }
    ]
    assert curies_result.result["data"] == [
        {
            "curie": "FB:FBgn0262738",
            "curie_validated": True,
            "entity": "norpA",
            "entity_curie": "FB:FBgn0262738",
            "name": "norpA",
            "symbol": "norpA",
            "taxon_id": "NCBITaxon:7227",
        }
    ]
    assert names_by_curie_result.result["data"] == [
        {"curie": "FB:FBgn0262738", "curie_validated": True, "name": "norpA"}
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

    reference_root = backend_root / "src" / "lib" / "openai_agents"
    reference_root.mkdir()
    (reference_root / "__init__.py").write_text("", encoding="utf-8")
    # This subprocess contract test isolates host services; real durable source
    # scope/generation/issued-window behavior has persistence coverage separately.
    (reference_root / "document_references.py").write_text(
        """from contextlib import asynccontextmanager

@asynccontextmanager
async def document_reference_scope(document_id, user_id, *, tool_name, call_id):
    assert document_id == "doc-42" and user_id == "user-9"
    yield

class _OneFile:
    def __init__(self):
        self.files = (self,)

    def contains(self, chunk_index):
        return type(chunk_index) is int and 0 <= chunk_index <= 99

    def file_for(self, chunk_index):
        return self if self.contains(chunk_index) else None

    def page_of(self, page):
        return page

    def labelled(self):
        return (("paper.pdf", self),)

    def ranges_text(self):
        return "0 to 99"


def current_readable_files():
    return _OneFile()

async def record_files_shown(chunk_indexes):
    assert chunk_indexes == [4]

async def retain_issued_spans(reads):
    issued = {chunk["id"]: (chunk["chunk_index"], indexes) for chunk, _spans, indexes in reads}
    assert issued in (
        {"chunk-search-1": (4, [0, 1])},
        {"chunk-methods-1": (0, [0]), "chunk-methods-2": (1, [0])},
    )
    for chunk, spans, _indexes in reads:
        assert all(span["span_id"].startswith(chunk["id"] + ":") for span in spans)
""", encoding="utf-8",
    )
    (package_root / "chunks.py").write_text(
        """async def hybrid_search_chunks(*, document_id, query, user_id, limit=10, initial_limit=None, alpha=None, section_keywords=None, apply_mmr=None, mmr_lambda=None, strategy=\"hybrid\", **_kwargs):
    section_title = strategy if query == \"strategy-check\" else \"Results\"
    if query == \"strategy-check\":
        text = f\"strategy={strategy}\"
    elif query == \"config-check\":
        text = f\"initial={initial_limit} alpha={alpha} mmr={apply_mmr} lambda={mmr_lambda}\"
    else:
        text = \"Wingless expression expanded in the mutant tissue.\"
    return [
        {
            \"id\": \"chunk-search-1\",
            \"chunk_index\": 4,
            \"score\": 0.91,
            \"text\": text,
            \"metadata\": {
                \"chunk_id\": \"stale-metadata-search-id\",
                \"section_title\": section_title,
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


async def get_chunk_by_index(*, chunk_index, document_id, user_id, **_kwargs):
    assert chunk_index == 4
    assert document_id == \"doc-42\"
    assert user_id == \"user-9\"
    return {
        \"id\": \"chunk-search-1\",
        \"text\": \"Wingless expression expanded in the mutant tissue. This sentence is exact evidence.\",
        \"chunk_index\": 4,
        \"page_number\": 7,
        \"section_title\": \"Results\",
        \"subsection\": \"Expression\",
        \"doc_items\": [{\"id\": \"bbox-read\"}],
        \"metadata\": {\"document_id\": document_id},
    }


async def get_chunk_neighbor_ids(*, document_id, user_id, chunk_index, **_kwargs):
    assert document_id == \"doc-42\"
    assert user_id == \"user-9\"
    assert chunk_index == 4
    return {\"previous_chunk_id\": \"chunk-search-0\", \"next_chunk_id\": \"chunk-search-2\"}


async def get_chunks_by_parent_section(*, document_id, parent_section, user_id, **_kwargs):
    return [
        {
            \"id\": \"chunk-methods-1\",
            \"chunk_index\": 0,
            \"text\": \"Paragraph one\",
            \"page_number\": 3,
            \"section_title\": \"Materials and Methods\",
            \"parent_section\": \"Materials and Methods\",
            \"subsection\": \"Animals\",
            \"metadata\": '{\"chunk_id\": \"stale-metadata-methods-1\", \"doc_items\": [{\"id\": \"bbox-1\"}], \"document_id\": \"%s\", \"parent_section\": \"%s\", \"user_id\": \"%s\"}' % (document_id, parent_section, user_id),
        },
        {
            \"chunkId\": \"chunk-methods-2\",
            \"chunk_index\": 1,
            \"content\": \"Paragraph two\",
            \"pageNumber\": 4,
            \"sectionTitle\": \"Materials and Methods\",
            \"parent_section\": \"Materials and Methods\",
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
        json.dumps({"prefixes": ["FB", "GO"]}),
        encoding="utf-8",
    )
    return runtime_root


def _write_fake_agr_curation_api_dependency(tmp_path: Path) -> Path:
    dependency_root = tmp_path / "fake_external"
    package_root = dependency_root / "agr_curation_api"
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "__init__.py").write_text(
        """class AGRCurationAPIClient:
    def __init__(self, data_source=None, **_kwargs):
        self.data_source = data_source

    def get_literature_reference(self, identifier):
        if self.data_source != "db":
            raise RuntimeError("literature reference helpers must use db mode")
        if identifier == "PMID:27528223":
            return {
                "reference_id": None,
                "curie": "AGRKB:101000000924191",
                "title": "Suppressed Helicobocter pylori-induced gastritis",
                "short_citation": "Hahm KB et al.",
                "cross_references": ["PMID:27528223"],
                "source": "literature_es",
                "obsolete": False,
            }
        return None

    def search_literature_references(self, query, exact_match=False, limit=20):
        if self.data_source != "db":
            raise RuntimeError("literature reference helpers must use db mode")
        if query == "Hahm KB Suppressed Helicobocter pylori":
            return [self.get_literature_reference("PMID:27528223")]
        return []
""",
        encoding="utf-8",
    )
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


class _OntologyTerm:
    def __init__(self, curie, name, namespace, definition, ontology_type, synonyms):
        self.curie = curie
        self.name = name
        self.namespace = namespace
        self.definition = definition
        self.ontology_type = ontology_type
        self.synonyms = synonyms


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

    def get_ontology_term(self, curie):
        if curie == "GO:0003674":
            return _OntologyTerm(
                "GO:0003674",
                "molecular_function",
                "molecular_function",
                "Elemental activities of a gene product.",
                "GOTerm",
                ["activity"],
            )
        return None

    def get_ontology_terms(self, curies):
        return {curie: self.get_ontology_term(curie) for curie in curies}

    def search_ontology_terms(self, term, ontology_type, exact_match, include_synonyms, limit):
        if (
            term == "molecular"
            and ontology_type == "GOTerm"
            and exact_match is True
            and include_synonyms is False
            and limit == 25
        ):
            return [self.get_ontology_term("GO:0003674")]
        return []

    def map_entity_names_to_curies(self, entity_type, entity_names, taxon_curie):
        if (
            entity_type == "gene"
            and entity_names == ["norpA"]
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

    def map_entity_curies_to_info(self, entity_type, entity_curies):
        if entity_type == "gene" and entity_curies == ["FB:FBgn0262738"]:
            return [
                {
                    "entity_curie": "FB:FBgn0262738",
                    "entity": "norpA",
                    "taxon_id": "NCBITaxon:7227",
                }
            ]
        return []

    def map_curies_to_names(self, category, curies):
        if category == "gene" and curies == ["FB:FBgn0262738"]:
            return {"FB:FBgn0262738": "norpA"}
        return {}
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

    # Like the real SDK, function_tool(func, **options) builds the tool directly.
    if decorator_args and callable(decorator_args[0]):
        return decorate(decorator_args[0])
    return decorate
""",
        encoding="utf-8",
    )
    # Surfaces imported by agr_ai_curation_runtime.function_tools.
    (package_root / "exceptions.py").write_text(
        "class ModelBehaviorError(Exception):\n    pass\n", encoding="utf-8",
    )
    (package_root / "function_schema.py").write_text(
        "from types import SimpleNamespace\n\n\n"
        "def function_schema(**kwargs):\n"
        "    return SimpleNamespace(params_pydantic_model=None)\n",
        encoding="utf-8",
    )
    (package_root / "tool.py").write_text("class FunctionTool:\n    pass\n", encoding="utf-8")
    return dependency_root


def test_production_image_ships_the_backend_lock_for_package_environments():
    dockerfile = (BACKEND_ROOT / "Dockerfile.prod").read_text(encoding="utf-8")
    builder_stage, runtime_stage = dockerfile.split(" AS runtime", 1)
    assert "/usr/local/bin/python3 -I -S" in builder_stage
    assert builder_stage.index("/usr/local/bin/python3 -I -S") < builder_stage.index("/tmp/cost_pricing")
    assert "src.lib.packages.build_package_envs" in builder_stage
    assert "--allowed-pth" in builder_stage
    assert "/opt/package-envs" in builder_stage
    assert "KNOWN_PTH=" in builder_stage
    assert "WORKDIR /app/backend" in runtime_stage
    assert "requirements.lock.txt ./" in runtime_stage
    assert "COPY --from=builder /opt/package-envs /opt/package-envs" in runtime_stage
    assert "AGR_PACKAGE_ENV_BOOTSTRAP_MODE=prebuilt" in runtime_stage
    assert "AGR_PREBUILT_PACKAGE_ENVS_DIR=/opt/package-envs" in runtime_stage


def _dockerignore_is_ignored(rel_path: str, rules: list[tuple[bool, str]]) -> bool:
    """Evaluate Dockerignore last-match-wins rules with ** glob support."""
    import re

    def _compile_pattern(pattern: str) -> re.Pattern[str]:
        normalized = pattern.strip().lstrip("/")
        parts: list[str] = []
        idx = 0
        while idx < len(normalized):
            if normalized[idx : idx + 3] == "**/":
                parts.append("(?:.*/)?")
                idx += 3
            elif normalized[idx : idx + 2] == "**":
                parts.append(".*")
                idx += 2
            elif normalized[idx] == "*":
                parts.append("[^/]*")
                idx += 1
            elif normalized[idx] == "?":
                parts.append("[^/]")
                idx += 1
            elif normalized[idx] == "[":
                end = normalized.find("]", idx + 1)
                if end != -1:
                    parts.append(normalized[idx : end + 1])
                    idx = end + 1
                else:
                    parts.append(re.escape(normalized[idx]))
                    idx += 1
            else:
                parts.append(re.escape(normalized[idx]))
                idx += 1
        body = "".join(parts)
        return re.compile(f"^(?:{body})(?:/.*)?$")

    ignored = False
    for negated, pattern in rules:
        if _compile_pattern(pattern).match(rel_path):
            ignored = not negated
    return ignored


def test_production_dockerignore_excludes_nested_secrets_and_keeps_build_inputs():
    dockerignore_path = BACKEND_ROOT / "Dockerfile.prod.dockerignore"
    raw_lines = [
        line.strip()
        for line in dockerignore_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    rules = [
        (line.startswith("!"), line[1:] if line.startswith("!") else line)
        for line in raw_lines
    ]

    for secret_path in (
        ".env",
        "packages/custom/.env",
        "packages/custom/.env.local",
        "packages/custom/.envrc",
        "packages/custom/local.env",
        "packages/custom/tls/client.pem",
        "packages/custom/tls/private.key",
        "packages/custom/vpn.ovpn",
        "packages/custom/aws_credentials.json",
        "packages/custom/api_secrets.yaml",
        "packages/custom/my_secret.txt",
        "packages/custom/.git/config",
        "backend/src/.env",
        "backend/src/tls/server.key",
        "backend/src/credentials.json",
        "frontend/src/App.tsx",
    ):
        assert _dockerignore_is_ignored(secret_path, rules), (
            f"Expected {secret_path} to be excluded by backend/Dockerfile.prod.dockerignore"
        )

    for build_input in (
        "backend/requirements.lock.txt",
        "backend/requirements.txt",
        "backend/main.py",
        "backend/alembic.ini",
        "backend/src/lib/packages/env_manager.py",
        "packages/core/package.yaml",
        "packages/alliance/package.yaml",
        "packages/alliance/requirements/runtime.lock.txt",
    ):
        assert not _dockerignore_is_ignored(build_input, rules), (
            f"Expected build input {build_input} to remain included"
        )

    for tree in (REPO_ROOT / "packages", BACKEND_ROOT / "src"):
        for file_path in tree.rglob("*"):
            if not file_path.is_file():
                continue
            if any(part in {"__pycache__", ".pytest_cache"} for part in file_path.parts):
                continue
            if file_path.suffix in {".pyc", ".pyo", ".pyd"}:
                continue
            rel = file_path.relative_to(REPO_ROOT).as_posix()
            assert not _dockerignore_is_ignored(rel, rules), (
                f"Tracked/build repo file unexpectedly excluded by dockerignore: {rel}"
            )


def _stage_locked_demo_package(
    packages_root: Path,
    *,
    requirements_text: str = "demo-dependency==1.0.0\n",
    lock_text: str | None = (
        "demo-dependency==1.0.0 \\\n"
        "    --hash=sha256:" + ("a" * 64) + "\n"
    ),
    declare_lock: bool = True,
):
    package_dir = packages_root / FIXTURE_PACKAGE_DIR.name
    shutil.copytree(FIXTURE_PACKAGE_DIR, package_dir)
    if declare_lock:
        manifest_path = package_dir / "package.yaml"
        manifest_text = manifest_path.read_text(encoding="utf-8").replace(
            "requirements_file: requirements/runtime.txt",
            "requirements_file: requirements/runtime.txt\nrequirements_lock_file: requirements/runtime.lock.txt",
        )
        manifest_path.write_text(manifest_text, encoding="utf-8")
    (package_dir / "requirements" / "runtime.txt").write_text(
        requirements_text,
        encoding="utf-8",
    )
    if lock_text is not None:
        (package_dir / "requirements" / "runtime.lock.txt").write_text(
            lock_text,
            encoding="utf-8",
        )
    registry = load_package_registry(
        packages_root,
        runtime_version="1.5.0",
        supported_package_api_version="1.0.0",
    )
    package = registry.get_package("demo.runner")
    assert package is not None
    return package


def test_prebuilt_environment_matches_across_different_mount_roots_with_zero_subprocess_calls(
    monkeypatch,
    tmp_path: Path,
):
    from src.lib.packages.env_manager import PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT

    build_package = _stage_locked_demo_package(tmp_path / "build" / "packages")
    runtime_package = _stage_locked_demo_package(tmp_path / "runtime" / "packages")
    build_constraints = tmp_path / "build" / "requirements.lock.txt"
    runtime_constraints = tmp_path / "app" / "backend" / "requirements.lock.txt"
    runtime_constraints.parent.mkdir(parents=True, exist_ok=True)
    lock_body = "demo-dependency==1.0.0\n"
    build_constraints.write_text(lock_body, encoding="utf-8")
    runtime_constraints.write_text(lock_body, encoding="utf-8")

    prebuilt_root = tmp_path / "opt" / "package-envs"
    builder = PackageEnvironmentManager(
        host_python=Path(sys.executable),
        constraints_path=build_constraints,
    )
    build_commands: list[tuple[str, list[str]]] = []

    def fake_build_run(command, *, package_id, step):
        build_commands.append((step, command))
        if step == "create_venv":
            py = prebuilt_root / package_id / "venv" / "bin" / "python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            py.chmod(0o755)

    monkeypatch.setattr(builder, "_run_command", fake_build_run)
    built = builder.build_environment(
        build_package,
        target_dir=prebuilt_root / build_package.package_id,
    )
    assert [step for step, _ in build_commands] == [
        "create_venv",
        "install_requirements",
        "verify_requirements",
    ]
    install_cmd = build_commands[1][1]
    assert "--require-hashes" in install_cmd
    assert "--no-deps" in install_cmd
    assert "--no-build-isolation" in install_cmd
    assert install_cmd[-2:] == [
        "-r",
        str(build_package.package_path / "requirements" / "runtime.lock.txt"),
    ]

    # Now verify from a completely different mount root in prebuilt mode with ZERO subprocesses.
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime-state-root"))
    monkeypatch.setenv("AGR_PACKAGE_ENV_BOOTSTRAP_MODE", "prebuilt")
    monkeypatch.setenv("AGR_PREBUILT_PACKAGE_ENVS_DIR", str(prebuilt_root))
    runtime_manager = PackageEnvironmentManager(constraints_path=runtime_constraints)

    def forbid_subprocess(*args, **kwargs):
        raise AssertionError(f"Prebuilt mode must never invoke subprocess: {args} {kwargs}")

    monkeypatch.setattr(subprocess, "run", forbid_subprocess)
    verified = runtime_manager.ensure_environment(runtime_package)

    assert verified.reused is True
    assert verified.mode == PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT
    assert verified.fingerprint == built.fingerprint
    assert verified.venv_dir == prebuilt_root / "demo.runner" / "venv"
    assert verified.python_executable == prebuilt_root / "demo.runner" / "venv" / "bin" / "python"
    assert not (tmp_path / "runtime-state-root" / "state" / "package_runner").exists()


@pytest.mark.parametrize(
    "failure_case,expected_fragment",
    [
        ("missing_dir", "metadata missing or invalid"),
        ("missing_python", "interpreter missing or not executable"),
        ("non_executable_python", "interpreter missing or not executable"),
        ("corrupt_metadata", "metadata missing or invalid"),
        ("prebuilt_false", "not marked prebuilt=true"),
        ("prebuilt_missing", "not marked prebuilt=true"),
        ("wrong_package_id", "package_id mismatch"),
        ("mutated_lock", "fingerprint mismatch"),
        ("mutated_requirements", "fingerprint mismatch"),
        ("mutated_constraints", "fingerprint mismatch"),
        ("symlink_env_escape", "escapes prebuilt root"),
        ("symlink_metadata_escape", "escapes environment directory"),
    ],
)
def test_prebuilt_mode_fails_loudly_without_download_fallback(
    monkeypatch,
    tmp_path: Path,
    failure_case: str,
    expected_fragment: str,
):
    from src.lib.packages.env_manager import PackageEnvironmentBootstrapError

    package = _stage_locked_demo_package(tmp_path / "runtime" / "packages")
    constraints = tmp_path / "requirements.lock.txt"
    constraints.write_text("demo-dependency==1.0.0\n", encoding="utf-8")
    prebuilt_root = tmp_path / "opt" / "package-envs"

    if failure_case != "missing_dir":
        builder = PackageEnvironmentManager(
            host_python=Path(sys.executable),
            constraints_path=constraints,
        )

        def fake_build_run(command, *, package_id, step):
            if step == "create_venv":
                py = prebuilt_root / package_id / "venv" / "bin" / "python"
                py.parent.mkdir(parents=True, exist_ok=True)
                py.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
                py.chmod(0o755)

        monkeypatch.setattr(builder, "_run_command", fake_build_run)
        builder.build_environment(
            package,
            target_dir=prebuilt_root / package.package_id,
        )

    metadata_path = prebuilt_root / "demo.runner" / "environment.json"
    python_path = prebuilt_root / "demo.runner" / "venv" / "bin" / "python"
    if failure_case == "missing_python":
        python_path.unlink()
    elif failure_case == "non_executable_python":
        python_path.chmod(0o644)
    elif failure_case == "corrupt_metadata":
        metadata_path.write_text("{not-json", encoding="utf-8")
    elif failure_case == "prebuilt_false":
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        meta["prebuilt"] = False
        metadata_path.write_text(json.dumps(meta), encoding="utf-8")
    elif failure_case == "prebuilt_missing":
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        meta.pop("prebuilt", None)
        metadata_path.write_text(json.dumps(meta), encoding="utf-8")
    elif failure_case == "wrong_package_id":
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        meta["package_id"] = "other.pkg"
        metadata_path.write_text(json.dumps(meta), encoding="utf-8")
    elif failure_case == "mutated_lock":
        (package.package_path / "requirements" / "runtime.lock.txt").write_text(
            "demo-dependency==2.0.0 \\\n    --hash=sha256:" + ("b" * 64) + "\n",
            encoding="utf-8",
        )
    elif failure_case == "mutated_requirements":
        (package.package_path / "requirements" / "runtime.txt").write_text(
            "demo-dependency==2.0.0\n",
            encoding="utf-8",
        )
    elif failure_case == "mutated_constraints":
        constraints.write_text("demo-dependency==2.0.0\n", encoding="utf-8")
    elif failure_case == "symlink_env_escape":
        escaped_env = tmp_path / "outside-env"
        shutil.move(str(prebuilt_root / "demo.runner"), str(escaped_env))
        (prebuilt_root / "demo.runner").symlink_to(escaped_env)
    elif failure_case == "symlink_metadata_escape":
        escaped_meta = tmp_path / "outside-environment.json"
        shutil.move(str(metadata_path), str(escaped_meta))
        metadata_path.symlink_to(escaped_meta)

    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime-state-root"))
    monkeypatch.setenv("AGR_PACKAGE_ENV_BOOTSTRAP_MODE", "prebuilt")
    monkeypatch.setenv("AGR_PREBUILT_PACKAGE_ENVS_DIR", str(prebuilt_root))
    manager = PackageEnvironmentManager(constraints_path=constraints)

    def forbid_subprocess(*args, **kwargs):
        raise AssertionError(f"Prebuilt mode must never fall back to pip/venv: {args} {kwargs}")

    monkeypatch.setattr(subprocess, "run", forbid_subprocess)
    with pytest.raises(PackageEnvironmentBootstrapError, match=expected_fragment):
        manager.ensure_environment(package)
    assert not (tmp_path / "runtime-state-root" / "state" / "package_runner").exists()


def test_prebuilt_mode_validates_interpreter_symlinks_and_bin_containment(
    monkeypatch,
    tmp_path: Path,
):
    from src.lib.packages.env_manager import (
        PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT,
        PackageEnvironmentBootstrapError,
    )

    package = _stage_locked_demo_package(tmp_path / "runtime" / "packages")
    constraints = tmp_path / "requirements.lock.txt"
    constraints.write_text("demo-dependency==1.0.0\n", encoding="utf-8")
    prebuilt_root = tmp_path / "opt" / "package-envs"

    builder = PackageEnvironmentManager(
        host_python=Path(sys.executable),
        constraints_path=constraints,
    )

    def fake_build_run(command, *, package_id, step):
        if step == "create_venv":
            py = prebuilt_root / package_id / "venv" / "bin" / "python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.symlink_to(Path(sys.executable).resolve(strict=True))

    monkeypatch.setattr(builder, "_run_command", fake_build_run)
    builder.build_environment(
        package,
        target_dir=prebuilt_root / package.package_id,
    )

    monkeypatch.setenv("AGR_PACKAGE_ENV_BOOTSTRAP_MODE", "prebuilt")
    monkeypatch.setenv("AGR_PREBUILT_PACKAGE_ENVS_DIR", str(prebuilt_root))
    manager = PackageEnvironmentManager(constraints_path=constraints)

    # 1. Standard venv symlink to the host/base Python passes and preserves venv-local path.
    verified = manager.ensure_environment(package)
    assert verified.mode == PACKAGE_ENV_BOOTSTRAP_MODE_PREBUILT
    expected_local_python = prebuilt_root / "demo.runner" / "venv" / "bin" / "python"
    assert verified.python_executable == expected_local_python

    # 2. Symlink pointing to an arbitrary executable outside venv is rejected.
    arbitrary_exec = tmp_path / "arbitrary_exec.sh"
    arbitrary_exec.write_text("#!/bin/sh\necho unexpected\n", encoding="utf-8")
    arbitrary_exec.chmod(0o755)
    expected_local_python.unlink()
    expected_local_python.symlink_to(arbitrary_exec)
    with pytest.raises(
        PackageEnvironmentBootstrapError,
        match="resolves outside virtual environment",
    ):
        manager.ensure_environment(package)

    # 3. Symlinked venv/bin directory escaping venv_dir is rejected.
    outside_bin = tmp_path / "outside_bin"
    outside_bin.mkdir()
    outside_py = outside_bin / "python"
    outside_py.symlink_to(Path(sys.executable).resolve(strict=True))
    shutil.rmtree(prebuilt_root / "demo.runner" / "venv" / "bin")
    (prebuilt_root / "demo.runner" / "venv" / "bin").symlink_to(outside_bin)
    with pytest.raises(
        PackageEnvironmentBootstrapError,
        match="interpreter bin directory .* escapes virtual environment",
    ):
        manager.ensure_environment(package)


def test_resolve_requirements_blocks_symlink_escapes_and_empty_declared_lock(
    monkeypatch,
    tmp_path: Path,
):
    from src.lib.packages.env_manager import PackageEnvironmentBootstrapError

    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    constraints = tmp_path / "requirements.lock.txt"
    constraints.write_text("demo-dependency==1.0.0\n", encoding="utf-8")
    manager, commands = _recording_manager(monkeypatch, constraints)

    # Empty declared lockfile with non-empty runtime.txt must fail in dynamic mode too.
    empty_lock_pkg = _stage_locked_demo_package(
        tmp_path / "packages-empty-lock",
        requirements_text="demo-dependency==1.0.0\n",
        lock_text="# intentionally empty\n",
        declare_lock=True,
    )
    with pytest.raises(PackageEnvironmentBootstrapError, match="lockfile .* is empty"):
        manager.ensure_environment(empty_lock_pkg)
    assert commands == []

    # Symlink escape on requirements/runtime.txt must fail before reading outside files.
    outside_req = tmp_path / "outside-runtime.txt"
    outside_req.write_text("demo-dependency==1.0.0\n", encoding="utf-8")
    escape_req_pkg = _stage_locked_demo_package(tmp_path / "packages-escape-req")
    req_file = escape_req_pkg.package_path / "requirements" / "runtime.txt"
    req_file.unlink()
    req_file.symlink_to(outside_req)
    with pytest.raises(PackageEnvironmentBootstrapError, match="escapes package root"):
        manager.ensure_environment(escape_req_pkg)

    # Symlink escape on requirements/runtime.lock.txt must also fail.
    outside_lock = tmp_path / "outside-runtime.lock.txt"
    outside_lock.write_text(
        "demo-dependency==1.0.0 \\\n    --hash=sha256:" + ("a" * 64) + "\n",
        encoding="utf-8",
    )
    escape_lock_pkg = _stage_locked_demo_package(tmp_path / "packages-escape-lock")
    lock_file = escape_lock_pkg.package_path / "requirements" / "runtime.lock.txt"
    lock_file.unlink()
    lock_file.symlink_to(outside_lock)
    with pytest.raises(PackageEnvironmentBootstrapError, match="escapes package root"):
        manager.ensure_environment(escape_lock_pkg)


def test_build_environment_scans_pth_allowlist_before_running_pip_check(
    monkeypatch,
    tmp_path: Path,
):
    from src.lib.packages.env_manager import PackageEnvironmentBootstrapError

    package = _stage_locked_demo_package(tmp_path / "packages")
    constraints = tmp_path / "requirements.lock.txt"
    constraints.write_text("demo-dependency==1.0.0\n", encoding="utf-8")
    target_dir = tmp_path / "opt" / "package-envs" / package.package_id
    manager = PackageEnvironmentManager(
        host_python=Path(sys.executable),
        constraints_path=constraints,
    )
    steps: list[str] = []

    def fake_run(command, *, package_id, step):
        steps.append(step)
        if step == "create_venv":
            py = target_dir / "venv" / "bin" / "python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            py.chmod(0o755)
        elif step == "install_requirements":
            site_pkgs = target_dir / "venv" / "lib" / "python3.11" / "site-packages"
            site_pkgs.mkdir(parents=True, exist_ok=True)
            (site_pkgs / "malicious_startup.pth").write_text("import os\n", encoding="utf-8")
        elif step == "verify_requirements":
            raise AssertionError("pip check must NEVER run when an unknown .pth file is present")

    monkeypatch.setattr(manager, "_run_command", fake_run)
    with pytest.raises(PackageEnvironmentBootstrapError, match="Unknown .pth file") as exc_info:
        manager.build_environment(package, target_dir=target_dir)
    assert exc_info.value.details["step"] == "verify_pth_whitelist"
    assert steps == ["create_venv", "install_requirements"]


@pytest.mark.parametrize(
    "requirements_text,lock_text,constraints_text,expected_match",
    [
        (
            "demo-dependency>=1.0\nextra-lib>=2.0\n",
            "demo-dependency==1.0.0 \\\n    --hash=sha256:" + ("a" * 64) + "\n",
            "demo-dependency==1.0.0\n",
            "missing direct requirement 'extra-lib'",
        ),
        (
            "demo-dependency>=2.0\n",
            "demo-dependency==1.0.0 \\\n    --hash=sha256:" + ("a" * 64) + "\n",
            "demo-dependency==1.0.0\n",
            "does not satisfy 'demo-dependency>=2.0'",
        ),
        (
            "demo-dependency>=1.0\n",
            "demo-dependency==2.0.0 \\\n    --hash=sha256:" + ("a" * 64) + "\n",
            "demo-dependency==1.0.0\n",
            "drifts from backend constraints",
        ),
        (
            "demo-dependency>=1.0\n",
            "demo-dependency>=1.0 \\\n    --hash=sha256:" + ("a" * 64) + "\n",
            "demo-dependency==1.0.0\n",
            "exact '==' version pins only",
        ),
        (
            "demo-dependency>=1.0\n",
            "demo-dependency==1.0.0\n",
            "demo-dependency==1.0.0\n",
            "must include valid --hash=sha256",
        ),
        (
            "requests>=2\n",
            'requests==2.32.5;sys_platform=="win32" \\\n    --hash=sha256:' + ("a" * 64) + "\n",
            "requests==2.32.5\n",
            "missing direct requirement 'requests'",
        ),
        (
            "requests>=2\n",
            'requests==2.32.5 ; sys_platform == "win32" \\\n    --hash=sha256:' + ("a" * 64) + "\n",
            "requests==2.32.5\n",
            "missing direct requirement 'requests'",
        ),
    ],
)
def test_build_environment_rejects_stale_or_invalid_custom_package_lockfile(
    monkeypatch,
    tmp_path: Path,
    requirements_text: str,
    lock_text: str,
    constraints_text: str,
    expected_match: str,
):
    from src.lib.packages.env_manager import PackageEnvironmentBootstrapError

    package = _stage_locked_demo_package(
        tmp_path / "packages",
        requirements_text=requirements_text,
        lock_text=lock_text,
        declare_lock=True,
    )
    constraints = tmp_path / "requirements.lock.txt"
    constraints.write_text(constraints_text, encoding="utf-8")
    manager = PackageEnvironmentManager(
        host_python=Path(sys.executable),
        constraints_path=constraints,
    )

    def forbid_run(*args, **kwargs):
        raise AssertionError("Stale lockfile must be rejected before running venv/pip")

    monkeypatch.setattr(manager, "_run_command", forbid_run)
    with pytest.raises(PackageEnvironmentBootstrapError, match=expected_match):
        manager.build_environment(
            package,
            target_dir=tmp_path / "out" / package.package_id,
        )


def test_dynamic_mode_uses_hashed_lockfile_and_fails_when_declared_lock_is_missing(
    monkeypatch,
    tmp_path: Path,
):
    from src.lib.packages.env_manager import PackageEnvironmentBootstrapError

    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    constraints = tmp_path / "requirements.lock.txt"
    constraints.write_text("demo-dependency==1.0.0\n", encoding="utf-8")
    package = _stage_locked_demo_package(tmp_path / "packages-locked")
    manager, commands = _recording_manager(monkeypatch, constraints)

    env = manager.ensure_environment(package)
    assert env.reused is False
    assert len(commands) == 3
    install_cmd = commands[1]
    assert "--require-hashes" in install_cmd
    assert "--no-deps" in install_cmd
    assert "--no-build-isolation" in install_cmd
    assert install_cmd[-2:] == [
        "-r",
        str(package.package_path / "requirements" / "runtime.lock.txt"),
    ]
    assert commands[2][-3:] == ["-m", "pip", "check"]

    # Missing declared lockfile must fail loudly without falling back to runtime.txt.
    missing_lock_pkg = _stage_locked_demo_package(
        tmp_path / "packages-missing-lock",
        lock_text=None,
        declare_lock=True,
    )
    commands.clear()
    with pytest.raises(PackageEnvironmentBootstrapError, match="Requirements lockfile not found"):
        manager.ensure_environment(missing_lock_pkg)
    assert commands == []


def test_dynamic_mode_validates_lockfile_and_pth_allowlist_on_cache_miss(
    monkeypatch,
    tmp_path: Path,
):
    from src.lib.packages.env_manager import PackageEnvironmentBootstrapError

    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    constraints = tmp_path / "requirements.lock.txt"
    constraints.write_text("demo-dependency==1.0.0\n", encoding="utf-8")

    # 1. Stale/drifting declared lockfile fails before creating a venv or invoking pip.
    stale_pkg = _stage_locked_demo_package(
        tmp_path / "packages-stale",
        requirements_text="demo-dependency>=2.0\n",
        lock_text="demo-dependency==1.0.0 \\\n    --hash=sha256:" + ("a" * 64) + "\n",
        declare_lock=True,
    )
    manager, commands = _recording_manager(monkeypatch, constraints)
    with pytest.raises(PackageEnvironmentBootstrapError, match="does not satisfy 'demo-dependency>=2.0'"):
        manager.ensure_environment(stale_pkg)
    assert commands == []

    # 2. Active environment marker alongside inactive platform marker passes validation.
    marker_pkg = _stage_locked_demo_package(
        tmp_path / "packages-marker-ok",
        requirements_text="demo-dependency>=1.0\n",
        lock_text=(
            'demo-dependency==0.9.0 ; sys_platform == "win32" \\\n'
            "    --hash=sha256:" + ("b" * 64) + "\n"
            'demo-dependency==1.0.0 ; sys_platform != "win32" \\\n'
            "    --hash=sha256:" + ("a" * 64) + "\n"
        ),
        declare_lock=True,
    )
    env = manager.ensure_environment(marker_pkg)
    assert env.reused is False
    assert commands[-1][-3:] == ["-m", "pip", "check"]

    # 3. Unknown .pth file installed during dynamic cache miss aborts BEFORE pip check.
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime-pth"))
    pth_pkg = _stage_locked_demo_package(tmp_path / "packages-pth")
    pth_manager = PackageEnvironmentManager(
        host_python=Path(sys.executable),
        constraints_path=constraints,
    )
    steps: list[str] = []

    def fake_pth_run(command, *, package_id, step):
        steps.append(step)
        if step == "create_venv":
            venv_dir = Path(command[-1])
            py = venv_dir / "bin" / "python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            py.chmod(0o755)
        elif step == "install_requirements":
            venv_dir = Path(command[0]).parent.parent
            site_pkgs = venv_dir / "lib" / "python3.11" / "site-packages"
            site_pkgs.mkdir(parents=True, exist_ok=True)
            (site_pkgs / "unexpected_dev.pth").write_text("import os\n", encoding="utf-8")
        elif step == "verify_requirements":
            raise AssertionError("pip check must not run when an unknown .pth file is present")

    monkeypatch.setattr(pth_manager, "_run_command", fake_pth_run)
    with pytest.raises(PackageEnvironmentBootstrapError, match="Unknown .pth file") as exc_info:
        pth_manager.ensure_environment(pth_pkg)
    assert exc_info.value.details["step"] == "verify_pth_whitelist"
    assert steps == ["create_venv", "install_requirements"]


def test_build_package_environments_cli_requires_hashed_lock_and_skips_non_tool_packages(
    monkeypatch,
    tmp_path: Path,
):
    from src.lib.packages import build_package_envs
    from src.lib.packages.env_manager import PackageEnvironmentBootstrapError

    packages_root = tmp_path / "packages"
    shutil.copytree(REPO_ROOT / "packages" / "core", packages_root / "core")
    _stage_locked_demo_package(
        packages_root,
        requirements_text="demo-dependency==1.0.0\n",
        lock_text=None,
        declare_lock=False,
    )
    constraints = tmp_path / "requirements.lock.txt"
    constraints.write_text("demo-dependency==1.0.0\n", encoding="utf-8")
    output_dir = tmp_path / "out-envs"

    with pytest.raises(PackageEnvironmentBootstrapError, match="requires a hashed requirements_lock_file"):
        build_package_envs.build_package_environments(
            packages_dir=packages_root,
            output_dir=output_dir,
            constraints_path=constraints,
            host_python=Path(sys.executable),
        )

    # Empty requirements + no tool bindings (core) plus empty-requirements tool package succeeds.
    shutil.rmtree(packages_root / "demo_runner")
    _stage_locked_demo_package(
        packages_root,
        requirements_text="# no runtime deps\n",
        lock_text=None,
        declare_lock=False,
    )
    commands: list[tuple[str, list[str]]] = []

    def fake_run(self, command, *, package_id, step):
        commands.append((step, command))
        if step == "create_venv":
            py = output_dir / package_id / "venv" / "bin" / "python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    monkeypatch.setattr(PackageEnvironmentManager, "_run_command", fake_run)
    built = build_package_envs.build_package_environments(
        packages_dir=packages_root,
        output_dir=output_dir,
        constraints_path=constraints,
        host_python=Path(sys.executable),
    )
    assert [env.package_id for env in built] == ["demo.runner"]
    assert not (output_dir / "agr.core").exists()
    assert [step for step, _ in commands] == ["create_venv", "verify_requirements"]


def test_alliance_hashed_lockfile_matches_backend_lock_and_pins_all_entries():
    import re
    from packaging.utils import canonicalize_name
    from packaging.version import Version
    from src.lib.packages.manifest_loader import load_package_manifest

    alliance_dir = REPO_ROOT / "packages" / "alliance"
    manifest = load_package_manifest(alliance_dir / "package.yaml")
    assert manifest.requirements_lock_file == "requirements/runtime.lock.txt"

    lock_path = alliance_dir / manifest.requirements_lock_file
    assert lock_path.is_file()

    # Join backslash-continued logical entries.
    logical_entries: list[str] = []
    current = ""
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            assert not current, f"Unfinished continuation before comment/blank line: {current}"
            continue
        if stripped.endswith("\\"):
            current = f"{current} {stripped[:-1].strip()}".strip()
        else:
            current = f"{current} {stripped}".strip()
            logical_entries.append(current)
            current = ""
    assert not current, f"Dangling line continuation at EOF: {current}"
    assert logical_entries, "runtime.lock.txt must not be empty"

    hash_pattern = re.compile(r"^--hash=sha256:[0-9a-f]{64}$")
    pkg_pins: dict[str, str] = {}
    for entry in logical_entries:
        tokens = entry.split()
        spec_token = tokens[0]
        hash_tokens = tokens[1:]
        assert "==" in spec_token, f"Unpinned entry in runtime.lock.txt: {spec_token}"
        req = Requirement(spec_token)
        assert len(req.specifier) == 1 and next(iter(req.specifier)).operator == "==", (
            f"Entry must use exact == pin: {spec_token}"
        )
        assert req.url is None, f"URL requirement not allowed in hashed lock: {spec_token}"
        assert hash_tokens, f"Missing --hash=sha256:... for {spec_token}"
        for token in hash_tokens:
            assert hash_pattern.fullmatch(token), f"Invalid hash token {token!r} on {spec_token}"
        canonical = canonicalize_name(req.name)
        assert canonical not in pkg_pins, f"Duplicate entry in runtime.lock.txt: {req.name}"
        pkg_pins[canonical] = next(iter(req.specifier)).version

    # Zero version drift against backend/requirements.lock.txt for every shared package.
    backend_pins: dict[str, str] = {}
    for raw_line in (BACKEND_ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        backend_pins[canonicalize_name(req.name)] = next(iter(req.specifier)).version

    drift = {
        name: (pkg_ver, backend_pins[name])
        for name, pkg_ver in pkg_pins.items()
        if name in backend_pins and pkg_ver != backend_pins[name]
    }
    assert drift == {}, f"Shared package version drift vs backend/requirements.lock.txt: {drift}"

    # Every direct requirement in runtime.txt is present in runtime.lock.txt and satisfies its specifier.
    for raw_line in (alliance_dir / "requirements" / "runtime.txt").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        canonical = canonicalize_name(req.name)
        assert canonical in pkg_pins, f"Direct requirement {req.name} missing from runtime.lock.txt"
        assert Version(pkg_pins[canonical]) in req.specifier, (
            f"{req.name}=={pkg_pins[canonical]} violates {req}"
        )
