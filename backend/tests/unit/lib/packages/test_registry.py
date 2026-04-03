"""Unit tests for runtime package discovery and registry building."""

import re
from pathlib import Path

import pytest

from . import find_repo_root
from src.lib.packages.health import build_package_health_report
from src.lib.packages.models import ExportKind
from src.lib.packages.registry import (
    PackageRegistryValidationError,
    load_package_registry,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
REPO_ROOT = find_repo_root(Path(__file__))


def _fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _write_package(packages_dir: Path, directory_name: str, manifest_text: str | None) -> Path:
    package_dir = packages_dir / directory_name
    package_dir.mkdir(parents=True)
    if manifest_text is not None:
        (package_dir / "package.yaml").write_text(manifest_text, encoding="utf-8")
    return package_dir


def test_load_package_registry_discovers_compatible_packages(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_package(packages_dir, "agr.base", _fixture_text("valid_package.yaml"))

    registry = load_package_registry(packages_dir, runtime_version="1.5.0")

    assert registry.validation_errors == ()
    assert len(registry.loaded_packages) == 1
    assert len(registry.failed_packages) == 0
    loaded_package = registry.loaded_packages[0]
    assert loaded_package.package_id == "agr.base"
    assert loaded_package.display_name == "AGR Base Package"
    assert registry.get_package("agr.base") == loaded_package


def test_load_package_registry_uses_runtime_version_from_config_by_default(tmp_path, monkeypatch):
    packages_dir = tmp_path / "packages"
    _write_package(packages_dir, "agr.base", _fixture_text("valid_package.yaml"))
    monkeypatch.setenv("APP_VERSION", "2.1.0")

    registry = load_package_registry(packages_dir, fail_on_validation_error=False)

    assert registry.runtime_version == "2.1.0"
    assert registry.loaded_packages == ()
    assert len(registry.failed_packages) == 1
    assert "Runtime version '2.1.0' is outside supported range" in registry.failed_packages[0].reason


def test_load_package_registry_reports_manifest_load_failures(tmp_path):
    packages_dir = tmp_path / "packages"
    package_dir = _write_package(packages_dir, "broken.package", _fixture_text("invalid_package.yaml"))

    registry = load_package_registry(packages_dir, fail_on_validation_error=False)

    assert registry.loaded_packages == ()
    assert len(registry.failed_packages) == 1
    failure = registry.failed_packages[0]
    assert failure.package_id == "broken.package"
    assert failure.package_path == package_dir
    assert "invalid_package.yaml" not in failure.reason
    assert "package_id" in failure.reason
    assert "version" in failure.reason


def test_load_package_registry_reports_missing_agent_bundle_manifest_registration(tmp_path):
    packages_dir = tmp_path / "packages"
    package_dir = _write_package(
        packages_dir,
        "demo.core",
        "\n".join(
            [
                "package_id: demo.core",
                "display_name: Demo Core",
                "version: 1.0.0",
                "package_api_version: 1.0.0",
                "min_runtime_version: 1.0.0",
                "max_runtime_version: 2.0.0",
                "python_package_root: python/src/demo_core",
                "requirements_file: requirements/runtime.txt",
                "agent_bundles:",
                "  - name: gene",
            ]
        )
        + "\n",
    )
    for agent_name in ("gene", "missing_manifest"):
        agent_dir = package_dir / "agents" / agent_name
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent.yaml").write_text(f"agent_id: {agent_name}\n", encoding="utf-8")

    registry = load_package_registry(packages_dir, fail_on_validation_error=False)

    assert registry.loaded_packages == ()
    assert len(registry.failed_packages) == 1
    assert "agent_bundles is missing package-owned agent directories with agent.yaml" in (
        registry.failed_packages[0].reason
    )
    assert "agents/missing_manifest" in registry.failed_packages[0].reason


def test_load_package_registry_reports_missing_agent_bundle_manifest_registration_when_key_omitted(
    tmp_path,
):
    packages_dir = tmp_path / "packages"
    package_dir = _write_package(
        packages_dir,
        "demo.core",
        "\n".join(
            [
                "package_id: demo.core",
                "display_name: Demo Core",
                "version: 1.0.0",
                "package_api_version: 1.0.0",
                "min_runtime_version: 1.0.0",
                "max_runtime_version: 2.0.0",
                "python_package_root: python/src/demo_core",
                "requirements_file: requirements/runtime.txt",
                "exports:",
                "  - kind: tool_binding",
                "    name: default",
                "    path: tools/bindings.yaml",
                "    description: Default bindings",
            ]
        )
        + "\n",
    )
    agent_dir = package_dir / "agents" / "missing_manifest"
    agent_dir.mkdir(parents=True)
    (agent_dir / "agent.yaml").write_text("agent_id: missing_manifest\n", encoding="utf-8")

    registry = load_package_registry(packages_dir, fail_on_validation_error=False)

    assert registry.loaded_packages == ()
    assert len(registry.failed_packages) == 1
    assert "agent_bundles is missing package-owned agent directories with agent.yaml" in (
        registry.failed_packages[0].reason
    )
    assert "agents/missing_manifest" in registry.failed_packages[0].reason


def test_load_package_registry_reports_runtime_incompatibility(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_package(packages_dir, "agr.base", _fixture_text("valid_package.yaml"))

    registry = load_package_registry(
        packages_dir,
        runtime_version="2.1.0",
        fail_on_validation_error=False,
    )

    assert registry.loaded_packages == ()
    assert len(registry.failed_packages) == 1
    assert registry.failed_packages[0].package_id == "agr.base"
    assert "2.1.0" in registry.failed_packages[0].reason
    assert "1.0.0' - '2.0.0" in registry.failed_packages[0].reason


def test_load_package_registry_reports_package_api_mismatch(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_package(packages_dir, "agr.base", _fixture_text("valid_package.yaml"))

    registry = load_package_registry(
        packages_dir,
        supported_package_api_version="2.0.0",
        fail_on_validation_error=False,
    )

    assert registry.loaded_packages == ()
    assert len(registry.failed_packages) == 1
    assert "Unsupported package_api_version '1.0.0'" in registry.failed_packages[0].reason
    assert "runtime supports '2.0.0'" in registry.failed_packages[0].reason


def test_load_package_registry_duplicate_ids_fail_validation(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_package(packages_dir, "package-a", _fixture_text("valid_package.yaml"))
    _write_package(packages_dir, "package-b", _fixture_text("valid_package.yaml"))

    with pytest.raises(PackageRegistryValidationError) as exc_info:
        load_package_registry(packages_dir)

    message = str(exc_info.value)
    assert "Duplicate package_id 'agr.base'" in message
    assert "package-a/package.yaml" in message
    assert "package-b/package.yaml" in message


def test_build_package_health_report_marks_duplicates_unhealthy(tmp_path):
    packages_dir = tmp_path / "packages"
    _write_package(packages_dir, "package-a", _fixture_text("valid_package.yaml"))
    _write_package(packages_dir, "package-b", _fixture_text("valid_package.yaml"))

    report = build_package_health_report(packages_dir)

    assert report["status"] == "unhealthy"
    assert report["summary"]["loaded_count"] == 0
    assert report["summary"]["failed_count"] == 2
    assert report["summary"]["validation_error_count"] == 1
    assert len(report["validation_errors"]) == 1
    assert "Duplicate package_id 'agr.base'" in report["validation_errors"][0]


def test_build_package_health_report_uses_configured_runtime_versions_by_default(tmp_path, monkeypatch):
    packages_dir = tmp_path / "packages"
    _write_package(packages_dir, "agr.base", _fixture_text("valid_package.yaml"))
    monkeypatch.setenv("APP_VERSION", "1.5.0")
    monkeypatch.setenv("AGR_RUNTIME_PACKAGE_API_VERSION", "1.0.0")

    report = build_package_health_report(packages_dir)

    assert report["status"] == "healthy"
    assert report["runtime_version"] == "1.5.0"
    assert report["supported_package_api_version"] == "1.0.0"
    assert report["summary"]["loaded_count"] == 1


def test_repo_shipped_packages_are_discoverable_and_compatible():
    packages_dir = REPO_ROOT / "packages"

    registry = load_package_registry(packages_dir)

    assert len(registry.loaded_packages) == 2

    core_package = registry.get_package("agr.core")
    assert core_package is not None
    assert core_package.package_path == packages_dir / "core"
    assert core_package.display_name == "Alliance Core"
    assert core_package.manifest.python_package_root == "python/src/agr_ai_curation_core"
    assert core_package.manifest.requirements_file == "requirements/runtime.txt"
    core_export_kinds = {export.kind for export in core_package.manifest.exports}
    assert core_export_kinds == {
        ExportKind.AGENT,
        ExportKind.PROMPT,
        ExportKind.GROUP_RULE,
        ExportKind.MODEL,
        ExportKind.PROVIDER,
        ExportKind.TOOL_POLICY_DEFAULTS,
    }

    alliance_package = registry.get_package("agr.alliance")
    assert alliance_package is not None
    assert alliance_package.package_path == packages_dir / "alliance"
    assert alliance_package.display_name == "Alliance Defaults"
    assert (
        alliance_package.manifest.python_package_root
        == "python/src/agr_ai_curation_alliance"
    )
    assert alliance_package.manifest.requirements_file == "requirements/runtime.txt"
    alliance_export_kinds = {
        export.kind for export in alliance_package.manifest.exports
    }
    assert alliance_export_kinds == {
        ExportKind.AGENT,
        ExportKind.CURATION_ADAPTER,
        ExportKind.PROMPT,
        ExportKind.GROUP_RULE,
        ExportKind.SCHEMA,
        ExportKind.TOOL_BINDING,
    }


def test_alliance_runtime_requirements_include_noctua_distribution():
    requirements_path = (
        REPO_ROOT / "packages" / "alliance" / "requirements" / "runtime.txt"
    )
    requirement_lines = requirements_path.read_text(encoding="utf-8").splitlines()

    requirement_names = [
        re.split(r"[<>=!~\\[]", line.split("#", 1)[0].strip().lower(), 1)[0]
        for line in requirement_lines
        if line.split("#", 1)[0].strip()
    ]

    assert "noctua" in requirement_names
