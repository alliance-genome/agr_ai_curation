"""Tests for package-owned external document-source provider registration."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.lib.packages.document_source_provider_loader import (
    DocumentSourceProviderLoadError,
    build_document_source_provider_catalog,
)
from src.lib.packages.registry import load_package_registry

from . import find_repo_root


REPO_ROOT = find_repo_root(Path(__file__))
ORG_CUSTOM_FIXTURE = Path(__file__).parent / "fixtures" / "org_custom_runtime"


def _copy_custom_package(
    packages_dir: Path,
    *,
    directory_name: str,
    package_id: str,
) -> Path:
    destination = packages_dir / directory_name
    shutil.copytree(ORG_CUSTOM_FIXTURE, destination)
    manifest_path = destination / "package.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "package_id: org.custom",
            f"package_id: {package_id}",
            1,
        ),
        encoding="utf-8",
    )
    return destination


def test_core_only_catalog_has_no_external_document_source_provider(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    shutil.copytree(REPO_ROOT / "packages" / "core", packages_dir / "agr.core")

    registry = load_package_registry(packages_dir)
    catalog = build_document_source_provider_catalog(registry)

    assert catalog.registrations == ()
    assert catalog.get("local_pdf") is None


def test_catalog_rejects_provider_id_collisions_with_both_sources(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    first = _copy_custom_package(
        packages_dir,
        directory_name="first",
        package_id="org.first",
    )
    second = _copy_custom_package(
        packages_dir,
        directory_name="second",
        package_id="org.second",
    )
    registry = load_package_registry(packages_dir)

    with pytest.raises(DocumentSourceProviderLoadError) as exc_info:
        build_document_source_provider_catalog(registry)

    message = str(exc_info.value)
    assert "Document-source provider ID collision 'example_literature'" in message
    assert "package 'org.first'" in message
    assert "package 'org.second'" in message
    assert str(first / "package.yaml") in message
    assert str(second / "package.yaml") in message
    assert "export 'example_literature'" in message


def test_catalog_reports_malformed_registration_with_full_export_provenance(
    tmp_path: Path,
) -> None:
    packages_dir = tmp_path / "packages"
    package_dir = _copy_custom_package(
        packages_dir,
        directory_name="broken",
        package_id="org.broken",
    )
    module_path = package_dir / "python" / "src" / "org_custom" / "document_sources.py"
    module_path.write_text(
        """
from src.lib.packages.document_source_provider_models import (
    DocumentSourceProviderPresentation,
    DocumentSourceProviderRegistration,
)

def get_document_source_provider_registrations():
    return (
        DocumentSourceProviderRegistration(
            provider_id="Broken Provider",
            factory=lambda: object(),
            presentation=DocumentSourceProviderPresentation(display_label="Broken"),
        ),
    )
""".strip()
        + "\n",
        encoding="utf-8",
    )
    registry = load_package_registry(packages_dir)

    with pytest.raises(DocumentSourceProviderLoadError) as exc_info:
        build_document_source_provider_catalog(registry)

    message = str(exc_info.value)
    assert "Failed to enumerate document-source provider" in message
    assert "package 'org.broken'" in message
    assert str(package_dir / "package.yaml") in message
    assert "export 'example_literature'" in message
    assert str(module_path) in message
    assert "provider_id 'Broken Provider'" in message


def test_catalog_rejects_package_registration_for_reserved_local_pdf(
    tmp_path: Path,
) -> None:
    packages_dir = tmp_path / "packages"
    package_dir = _copy_custom_package(
        packages_dir,
        directory_name="reserved",
        package_id="org.reserved",
    )
    module_path = package_dir / "python" / "src" / "org_custom" / "document_sources.py"
    module_path.write_text(
        """
from src.lib.packages.document_source_provider_models import (
    DocumentSourceProviderPresentation,
    DocumentSourceProviderRegistration,
)

def get_document_source_provider_registrations():
    return (
        DocumentSourceProviderRegistration(
            provider_id="local_pdf",
            factory=lambda: object(),
            presentation=DocumentSourceProviderPresentation(display_label="Shadow"),
        ),
    )
""".strip()
        + "\n",
        encoding="utf-8",
    )
    registry = load_package_registry(packages_dir)

    with pytest.raises(DocumentSourceProviderLoadError) as exc_info:
        build_document_source_provider_catalog(registry)

    message = str(exc_info.value)
    assert "provider ID 'local_pdf' is reserved" in message
    assert "package 'org.reserved'" in message
    assert str(package_dir / "package.yaml") in message
    assert "export 'example_literature'" in message
    assert str(module_path) in message
