"""Tests for package-owned persisted-flow migration declarations."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.lib.packages.persisted_flow_migration_loader import (
    PersistedFlowMigrationLoadError,
    build_persisted_flow_migration_catalog,
)
from src.lib.packages.registry import load_package_registry

from . import find_repo_root


REPO_ROOT = find_repo_root(Path(__file__))


def _write_package(
    packages_dir: Path,
    package_id: str,
    migration_yaml: str,
) -> None:
    package_dir = packages_dir / package_id
    (package_dir / "config").mkdir(parents=True)
    (package_dir / "requirements").mkdir()
    (package_dir / "python" / "src" / package_id.replace(".", "_")).mkdir(parents=True)
    (package_dir / "requirements" / "runtime.txt").write_text("", encoding="utf-8")
    (package_dir / "package.yaml").write_text(
        "\n".join(
            [
                f"package_id: {package_id}",
                f"display_name: {package_id}",
                "version: 1.0.0",
                "package_api_version: 1.0.0",
                "min_runtime_version: 1.0.0",
                "max_runtime_version: 2.0.0",
                f"python_package_root: python/src/{package_id.replace('.', '_')}",
                "requirements_file: requirements/runtime.txt",
                "exports:",
                "  - kind: persisted_flow_migrations",
                "    name: default",
                "    path: config/migrations.yaml",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (package_dir / "config" / "migrations.yaml").write_text(
        migration_yaml,
        encoding="utf-8",
    )


def _manifest(migration_id: str, attachment_id: str) -> str:
    return f"""\
persisted_flow_migrations_api_version: 1.0.0
migrations:
  - migration_id: {migration_id}
    retired_binding_id: retired_validator
    retired_attachments:
      - attachment_id: {attachment_id}
        validator_binding_id: retired_validator
"""


def test_shipped_package_declares_exact_retired_flow_repair():
    catalog = build_persisted_flow_migration_catalog(
        load_package_registry(REPO_ROOT / "packages")
    )

    assert len(catalog.migrations) == 1
    migration = catalog.migrations[0]
    assert migration.migration_id == (
        "2026-09-03.remove-allele-pending-envelope-validator"
    )
    assert len(migration.retired_attachments) == 6
    assert (
        sum(
            attachment.validator_binding_id is None
            for attachment in migration.retired_attachments
        )
        == 1
    )


def test_package_profile_without_migration_exports_has_empty_catalog(tmp_path):
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    (packages_dir / "core").symlink_to(
        REPO_ROOT / "packages" / "core",
        target_is_directory=True,
    )

    catalog = build_persisted_flow_migration_catalog(
        load_package_registry(packages_dir)
    )

    assert catalog.migrations == ()


def test_invalid_export_reports_package_and_source_path(tmp_path):
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    _write_package(
        packages_dir,
        "org.invalid",
        _manifest("bad-migration", "duplicate")
        + """\
      - attachment_id: duplicate
        validator_binding_id: retired_validator
""",
    )

    with pytest.raises(PersistedFlowMigrationLoadError) as exc:
        build_persisted_flow_migration_catalog(load_package_registry(packages_dir))

    message = str(exc.value)
    assert "org.invalid" in message
    assert "migrations.yaml" in message
    assert "duplicate attachment IDs" in message


def test_cross_package_migration_id_collision_is_rejected(tmp_path):
    packages_dir = tmp_path / "packages"
    packages_dir.mkdir()
    _write_package(
        packages_dir,
        "org.one",
        _manifest("shared-migration", "org.one:retired"),
    )
    _write_package(
        packages_dir,
        "org.two",
        _manifest("shared-migration", "org.two:retired"),
    )

    with pytest.raises(PersistedFlowMigrationLoadError, match="ID collision"):
        build_persisted_flow_migration_catalog(load_package_registry(packages_dir))
