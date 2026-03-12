"""Unit tests for the production runtime entrypoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.lib.packages.models import ExportKind, PackageExport, PackageManifest
from src.lib.packages.registry import LoadedPackage, PackageRegistry
from src.lib import runtime_entrypoint


@pytest.fixture(autouse=True)
def _clear_runtime_env(monkeypatch):
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
        "AGR_BOOTSTRAP_PACKAGE_ENVS_ON_START",
        "RUN_DB_BOOTSTRAP_ON_START",
        "RUN_DB_MIGRATIONS_ON_START",
        "CURATION_DB_URL",
        "DATABASE_URL",
        "BACKEND_HOST",
        "BACKEND_PORT",
        "BACKEND_WORKERS",
    ):
        monkeypatch.delenv(variable, raising=False)


def test_ensure_runtime_layout_creates_expected_directories(monkeypatch, tmp_path: Path):
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))

    runtime_entrypoint.ensure_runtime_layout()

    assert (runtime_root / "config").is_dir()
    assert (runtime_root / "packages").is_dir()
    assert (runtime_root / "state" / "pdf_storage").is_dir()
    assert (runtime_root / "state" / "pdf_storage" / "pdfx_json").is_dir()
    assert (runtime_root / "state" / "pdf_storage" / "processed_json").is_dir()
    assert (runtime_root / "state" / "file_outputs").is_dir()
    assert (runtime_root / "state" / "identifier_prefixes").is_dir()
    assert (runtime_root / "state" / "package_runner").is_dir()


def test_validate_runtime_packages_requires_loaded_packages(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(tmp_path / "runtime"))
    runtime_entrypoint.ensure_runtime_layout()

    with pytest.raises(RuntimeError, match="No compatible runtime packages were discovered"):
        runtime_entrypoint.validate_runtime_packages()


def test_bootstrap_package_environments_only_targets_tool_packages(monkeypatch, tmp_path: Path):
    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    registry = PackageRegistry(
        packages_dir=package_dir,
        runtime_version="1.0.0",
        supported_package_api_version="1.0.0",
        loaded_packages=(
            _loaded_package("agr.tooling", package_dir / "agr.tooling", has_tool_binding=True),
            _loaded_package("agr.prompts", package_dir / "agr.prompts", has_tool_binding=False),
        ),
        failed_packages=(),
        validation_errors=(),
    )
    monkeypatch.setenv("AGR_BOOTSTRAP_PACKAGE_ENVS_ON_START", "true")

    bootstrapped: list[str] = []

    class FakeEnvironment:
        reused = False

    class FakeManager:
        def ensure_environment(self, package: LoadedPackage):
            bootstrapped.append(package.package_id)
            return FakeEnvironment()

    monkeypatch.setattr(runtime_entrypoint, "PackageEnvironmentManager", lambda: FakeManager())

    runtime_entrypoint.bootstrap_package_environments(registry)

    assert bootstrapped == ["agr.tooling"]


def test_refresh_identifier_prefixes_writes_runtime_state_file(monkeypatch, tmp_path: Path):
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("AGR_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/curation")

    class FakeCursor:
        def __init__(self):
            self._result = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query: str):
            if "crossreference" in query:
                self._result = [("FB",), ("WB",)]
            elif "ontologyterm" in query:
                self._result = [("GO",)]
            else:
                self._result = [("MGI",), ("FB",)]

        def fetchall(self):
            return list(self._result)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(runtime_entrypoint.psycopg2, "connect", lambda *args, **kwargs: FakeConnection())

    refreshed = runtime_entrypoint.refresh_identifier_prefixes()

    assert refreshed is True
    prefix_file = runtime_root / "state" / "identifier_prefixes" / "identifier_prefixes.json"
    assert json.loads(prefix_file.read_text(encoding="utf-8")) == {
        "prefixes": ["FB", "GO", "MGI", "WB"]
    }


def test_write_json_atomically_uses_replace_in_target_directory(monkeypatch, tmp_path: Path):
    destination = tmp_path / "state" / "identifier_prefixes.json"
    destination.parent.mkdir(parents=True)
    destination.write_text('{"prefixes":["OLD"]}\n', encoding="utf-8")

    replace_calls: list[tuple[Path, Path]] = []
    original_replace = runtime_entrypoint.os.replace

    def recording_replace(source: Path | str, target: Path | str) -> None:
        source_path = Path(source)
        target_path = Path(target)
        replace_calls.append((source_path, target_path))
        assert source_path.parent == destination.parent
        assert source_path.exists()
        original_replace(source_path, target_path)

    monkeypatch.setattr(runtime_entrypoint.os, "replace", recording_replace)

    runtime_entrypoint._write_json_atomically(
        destination,
        {"prefixes": ["FB", "WB"]},
    )

    assert len(replace_calls) == 1
    assert replace_calls[0][1] == destination
    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "prefixes": ["FB", "WB"]
    }
    assert list(destination.parent.glob(".*.tmp")) == []


def test_build_default_server_command_uses_production_defaults(monkeypatch):
    monkeypatch.setenv("BACKEND_HOST", "127.0.0.1")
    monkeypatch.setenv("BACKEND_PORT", "9000")
    monkeypatch.setenv("BACKEND_WORKERS", "3")

    command = runtime_entrypoint.build_default_server_command()

    assert command == [
        "uvicorn",
        "main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "9000",
        "--workers",
        "3",
    ]
    assert "--reload" not in command


def test_redact_database_url_keeps_username_and_redacts_password():
    scheme = "postgresql"
    username = "readonly"
    password = "example-password"
    host = "db.example.invalid"
    port = "5432"
    database = "curation"

    database_url = f"{scheme}://{username}:{password}@{host}:{port}/{database}"
    expected = f"{scheme}://{username}:***@{host}:{port}/{database}"

    assert runtime_entrypoint._redact_database_url(database_url) == expected


def _loaded_package(
    package_id: str,
    package_path: Path,
    *,
    has_tool_binding: bool,
) -> LoadedPackage:
    exports = [
        PackageExport(
            kind=ExportKind.PROMPT,
            name=f"{package_id}.system",
            path="agents/demo/prompt.yaml",
            description="prompt",
        )
    ]
    if has_tool_binding:
        exports.append(
            PackageExport(
                kind=ExportKind.TOOL_BINDING,
                name="default",
                path="tools/bindings.yaml",
                description="tools",
            )
        )

    manifest = PackageManifest(
        package_id=package_id,
        display_name=package_id,
        version="1.0.0",
        package_api_version="1.0.0",
        min_runtime_version="1.0.0",
        max_runtime_version="2.0.0",
        python_package_root="python/src/example",
        requirements_file="requirements/runtime.txt",
        exports=exports,
    )
    manifest_path = package_path / "package.yaml"

    return LoadedPackage(
        package_id=package_id,
        display_name=package_id,
        version="1.0.0",
        package_path=package_path,
        manifest_path=manifest_path,
        manifest=manifest,
    )
