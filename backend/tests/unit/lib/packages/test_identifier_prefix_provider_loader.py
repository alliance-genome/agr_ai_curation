"""Tests for package-owned identifier-prefix providers."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.lib.packages.identifier_prefix_provider_loader import (
    IdentifierPrefixProviderLoadError,
    build_identifier_prefix_provider_catalog,
)
from src.lib.packages.registry import load_package_registry

from . import find_repo_root


REPO_ROOT = find_repo_root(Path(__file__))


def _copy_shipped_packages(packages_dir: Path) -> Path:
    shutil.copytree(REPO_ROOT / "packages" / "core", packages_dir / "core")
    alliance_dir = packages_dir / "alliance"
    shutil.copytree(REPO_ROOT / "packages" / "alliance", alliance_dir)
    return alliance_dir


def test_core_only_catalog_has_no_identifier_prefix_provider(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    shutil.copytree(REPO_ROOT / "packages" / "core", packages_dir / "core")

    catalog = build_identifier_prefix_provider_catalog(
        load_package_registry(packages_dir)
    )

    assert catalog.providers == ()


def test_alliance_provider_owns_current_schema_queries(tmp_path: Path, monkeypatch) -> None:
    packages_dir = tmp_path / "packages"
    _copy_shipped_packages(packages_dir)
    catalog = build_identifier_prefix_provider_catalog(
        load_package_registry(packages_dir)
    )
    assert len(catalog.providers) == 1
    loaded = catalog.providers[0]
    assert loaded.source.package_id == "agr.alliance"

    queries: list[str] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query: str) -> None:
            queries.append(query)

        def fetchall(self):
            return [("FB",), ("MGI",)]

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(
        loaded.provider.__globals__["psycopg2"],
        "connect",
        lambda url: FakeConnection(),
    )

    assert loaded.provider("postgresql://curation") == ["FB", "MGI"] * 3
    assert len(queries) == 3
    assert "crossreference" in queries[0]
    assert "ontologyterm" in queries[1]
    assert "biologicalentity" in queries[2]


def test_catalog_rejects_missing_provider_module_with_provenance(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    alliance_dir = _copy_shipped_packages(packages_dir)
    manifest_path = alliance_dir / "package.yaml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            "python/src/agr_ai_curation_alliance/identifier_prefixes.py",
            "python/src/agr_ai_curation_alliance/missing.py",
        ),
        encoding="utf-8",
    )

    with pytest.raises(IdentifierPrefixProviderLoadError) as exc_info:
        build_identifier_prefix_provider_catalog(load_package_registry(packages_dir))

    message = str(exc_info.value)
    assert "does not exist" in message
    assert "package 'agr.alliance'" in message
    assert "export 'curation_database'" in message


def test_catalog_rejects_provider_without_required_callable(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    alliance_dir = _copy_shipped_packages(packages_dir)
    provider_path = (
        alliance_dir
        / "python"
        / "src"
        / "agr_ai_curation_alliance"
        / "identifier_prefixes.py"
    )
    provider_path.write_text("NOT_A_PROVIDER = True\n", encoding="utf-8")

    with pytest.raises(IdentifierPrefixProviderLoadError, match="get_identifier_prefixes"):
        build_identifier_prefix_provider_catalog(load_package_registry(packages_dir))
