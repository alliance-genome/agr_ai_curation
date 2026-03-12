"""Add tool_policies table and seed defaults.

Revision ID: z8a9b0c1d2e3
Revises: y7z8a9b0c1d2
Create Date: 2026-02-23
"""

import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Dict

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
import yaml

DEFAULT_APP_VERSION = "1.0.0"
DEFAULT_RUNTIME_PACKAGE_API_VERSION = "1.0.0"
DEFAULT_RUNTIME_ROOT = Path("/runtime")
_RUNTIME_CONFIG_FILENAMES = (
    "models.yaml",
    "providers.yaml",
    "tool_policy_defaults.yaml",
)

# revision identifiers, used by Alembic.
revision = "z8a9b0c1d2e3"
down_revision = "y7z8a9b0c1d2"
branch_labels = None
depends_on = None


def _normalize_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _resolve_optional_path(
    raw_value: str | None,
    *,
    parent: Path,
    default_name: str,
) -> Path:
    if raw_value is None or not raw_value.strip():
        return _normalize_path(parent / default_name)

    candidate = Path(raw_value)
    if candidate.is_absolute():
        return _normalize_path(candidate)
    if ".." in candidate.parts:
        raise ValueError(
            f"Relative runtime override '{raw_value}' must not traverse parent directories"
        )

    return _normalize_path(parent / candidate)


def _get_runtime_root() -> Path:
    raw_value = os.getenv("AGR_RUNTIME_ROOT")
    if raw_value and raw_value.strip():
        return _normalize_path(Path(raw_value))
    return _normalize_path(DEFAULT_RUNTIME_ROOT)


def _get_runtime_config_dir() -> Path:
    return _resolve_optional_path(
        os.getenv("AGR_RUNTIME_CONFIG_DIR"),
        parent=_get_runtime_root(),
        default_name="config",
    )


def _get_runtime_packages_dir() -> Path:
    return _resolve_optional_path(
        os.getenv("AGR_RUNTIME_PACKAGES_DIR"),
        parent=_get_runtime_root(),
        default_name="packages",
    )


def _find_project_root() -> Path | None:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "docker-compose.test.yml").exists():
            return candidate
        if (candidate / "backend").is_dir() and (candidate / "packages").is_dir():
            return candidate
        config_dir = candidate / "config"
        if (candidate / "packages").is_dir() and any(
            (config_dir / filename).exists()
            for filename in _RUNTIME_CONFIG_FILENAMES
        ):
            return candidate
    return None


def _get_default_packages_dir() -> Path:
    runtime_packages_dir = _get_runtime_packages_dir()
    if runtime_packages_dir.exists():
        return runtime_packages_dir

    project_root = _find_project_root()
    if project_root is not None:
        return _normalize_path(project_root / "packages")

    return runtime_packages_dir


def _resolve_runtime_override_path() -> tuple[Path, bool]:
    env_path = os.getenv("TOOL_POLICY_DEFAULTS_CONFIG_PATH")
    if env_path:
        return _normalize_path(Path(env_path)), True

    runtime_path = _normalize_path(_get_runtime_config_dir() / "tool_policy_defaults.yaml")
    if runtime_path.exists():
        return runtime_path, False

    project_root = _find_project_root()
    if project_root is not None:
        return _normalize_path(project_root / "config" / "tool_policy_defaults.yaml"), False

    return runtime_path, False


def _load_yaml_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {label} at {path}: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{label} at {path} must contain a top-level YAML mapping, found "
            f"{type(data).__name__}"
        )
    return data


def _parse_core_semver(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise ValueError(f"Invalid semantic version: {value}")
    return tuple(int(part) for part in match.groups())


def _runtime_version_is_compatible(
    runtime_version: str,
    *,
    min_runtime_version: str,
    max_runtime_version: str,
) -> bool:
    runtime_core = _parse_core_semver(runtime_version)
    return (
        _parse_core_semver(min_runtime_version)
        <= runtime_core
        <= _parse_core_semver(max_runtime_version)
    )


def _validate_relative_export_path(
    raw_value: Any,
    *,
    package_id: str,
    export_name: str,
) -> Path:
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(
            f"Tool policy export '{export_name}' in package '{package_id}' must define a path"
        )

    normalized = PurePosixPath(raw_value)
    if normalized.is_absolute():
        raise ValueError(
            f"Tool policy export '{export_name}' in package '{package_id}' must be relative"
        )
    if ".." in normalized.parts:
        raise ValueError(
            f"Tool policy export '{export_name}' in package '{package_id}' must not traverse parent directories"
        )
    if normalized.parts and normalized.parts[0] == ".":
        raise ValueError(
            f"Tool policy export '{export_name}' in package '{package_id}' must not start with './'"
        )

    return Path(*normalized.parts)


def _load_package_tool_policy_sources() -> list[tuple[str, dict[str, Any]]]:
    packages_dir = _get_default_packages_dir()
    if not packages_dir.exists():
        return []

    discovered_packages: list[tuple[str, Path, Path, dict[str, Any]]] = []
    package_ids_to_paths: dict[str, list[Path]] = {}

    for package_dir in sorted(
        (path for path in packages_dir.iterdir() if path.is_dir()),
        key=lambda item: item.name,
    ):
        manifest_path = package_dir / "package.yaml"
        if not manifest_path.exists():
            continue

        manifest = _load_yaml_mapping(
            manifest_path,
            label=f"package manifest '{package_dir.name}'",
        )
        package_id = str(manifest.get("package_id", "")).strip()
        if not package_id:
            raise ValueError(f"Package manifest at {manifest_path} must define 'package_id'")

        discovered_packages.append((package_id, package_dir, manifest_path, manifest))
        package_ids_to_paths.setdefault(package_id, []).append(manifest_path)

    duplicate_errors = [
        (
            package_id,
            ", ".join(str(path) for path in sorted(paths))
        )
        for package_id, paths in sorted(package_ids_to_paths.items())
        if len(paths) > 1
    ]
    if duplicate_errors:
        raise ValueError(
            "; ".join(
                f"Duplicate package_id '{package_id}' discovered at: {paths}"
                for package_id, paths in duplicate_errors
            )
        )

    runtime_version = os.getenv("APP_VERSION", DEFAULT_APP_VERSION)
    supported_package_api_version = os.getenv(
        "AGR_RUNTIME_PACKAGE_API_VERSION",
        DEFAULT_RUNTIME_PACKAGE_API_VERSION,
    )
    sources: list[tuple[str, dict[str, Any]]] = []

    for package_id, package_dir, manifest_path, manifest in sorted(
        discovered_packages,
        key=lambda item: item[0],
    ):
        package_api_version = str(manifest.get("package_api_version", "")).strip()
        if not package_api_version:
            raise ValueError(
                f"Package manifest at {manifest_path} must define 'package_api_version'"
            )
        if package_api_version != supported_package_api_version:
            continue

        min_runtime_version = str(manifest.get("min_runtime_version", "")).strip()
        max_runtime_version = str(manifest.get("max_runtime_version", "")).strip()
        if not min_runtime_version or not max_runtime_version:
            raise ValueError(
                f"Package manifest at {manifest_path} must define runtime compatibility bounds"
            )
        if not _runtime_version_is_compatible(
            runtime_version,
            min_runtime_version=min_runtime_version,
            max_runtime_version=max_runtime_version,
        ):
            continue

        raw_exports = manifest.get("exports", [])
        if raw_exports is None:
            raw_exports = []
        if not isinstance(raw_exports, list):
            raise ValueError(f"Package manifest at {manifest_path} field 'exports' must be a list")

        package_exports: list[tuple[str, Path]] = []
        for raw_export in raw_exports:
            if not isinstance(raw_export, dict):
                raise ValueError(
                    f"Package manifest at {manifest_path} field 'exports' must contain mappings"
                )
            if raw_export.get("kind") != "tool_policy_defaults":
                continue

            export_name = str(raw_export.get("name", "")).strip() or "unnamed"
            export_path = _validate_relative_export_path(
                raw_export.get("path"),
                package_id=package_id,
                export_name=export_name,
            )
            package_exports.append((export_name, export_path))

        for export_name, export_path in sorted(
            package_exports,
            key=lambda item: (item[0], item[1].as_posix()),
        ):
            label = f"package default '{package_id}' export '{export_name}'"
            payload = _load_yaml_mapping(
                _normalize_path(package_dir / export_path),
                label=label,
            )
            sources.append((f"{label} at {_normalize_path(package_dir / export_path)}", payload))

    return sources


def _load_tool_policy_sources() -> list[tuple[str, dict[str, Any]]]:
    sources = _load_package_tool_policy_sources()

    runtime_path, explicitly_configured = _resolve_runtime_override_path()
    if runtime_path.exists():
        label = "runtime override 'tool_policy_defaults.yaml'"
        payload = _load_yaml_mapping(runtime_path, label=label)
        sources.append((f"{label} at {runtime_path}", payload))
    elif explicitly_configured:
        raise FileNotFoundError(f"Runtime override file not found: {runtime_path}")

    if not sources:
        raise FileNotFoundError(
            "No tool policy defaults were found in runtime packages or runtime override config"
        )

    return sources


def _load_default_tool_policies() -> Dict[str, Dict[str, Any]]:
    """Load seed tool policy defaults from package exports plus runtime overrides."""
    registry: Dict[str, Dict[str, Any]] = {}

    for source_label, payload in _load_tool_policy_sources():
        raw_policies = payload.get("tool_policies")
        if not isinstance(raw_policies, dict):
            raise ValueError(
                f"{source_label} must define a top-level 'tool_policies' mapping"
            )

        for tool_key, raw_policy in raw_policies.items():
            clean_key = str(tool_key or "").strip()
            if not clean_key:
                raise ValueError(f"{source_label} contains an empty tool policy key")
            if not isinstance(raw_policy, dict):
                raise ValueError(
                    f"Tool policy '{clean_key}' in {source_label} must be a mapping"
                )

            raw_config = raw_policy.get("config", {})
            if raw_config is None:
                raw_config = {}
            if not isinstance(raw_config, dict):
                raise ValueError(
                    f"Tool policy '{clean_key}' in {source_label} field 'config' must be a mapping"
                )

            registry[clean_key] = {
                "display_name": str(raw_policy.get("display_name", clean_key)).strip()
                or clean_key,
                "description": str(raw_policy.get("description", "")).strip(),
                "category": str(raw_policy.get("category", "General")).strip() or "General",
                "curator_visible": bool(raw_policy.get("curator_visible", True)),
                "allow_attach": bool(raw_policy.get("allow_attach", True)),
                "allow_execute": bool(raw_policy.get("allow_execute", True)),
                "config": dict(raw_config),
            }

    return registry


def upgrade() -> None:
    op.create_table(
        "tool_policies",
        sa.Column("tool_key", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=100), nullable=False, server_default="General"),
        sa.Column("curator_visible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("allow_attach", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("allow_execute", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("tool_key"),
    )
    op.create_index("ix_tool_policies_category", "tool_policies", ["category"], unique=False)
    op.create_index(
        "ix_tool_policies_curator_visible",
        "tool_policies",
        ["curator_visible"],
        unique=False,
    )

    connection = op.get_bind()
    policies = _load_default_tool_policies()
    for tool_key, data in policies.items():
        connection.execute(
            sa.text(
                """
                INSERT INTO tool_policies (
                    tool_key,
                    display_name,
                    description,
                    category,
                    curator_visible,
                    allow_attach,
                    allow_execute,
                    config
                ) VALUES (
                    :tool_key,
                    :display_name,
                    :description,
                    :category,
                    :curator_visible,
                    :allow_attach,
                    :allow_execute,
                    CAST(:config AS jsonb)
                )
                ON CONFLICT (tool_key) DO NOTHING
                """
            ),
            {
                "tool_key": str(tool_key),
                "display_name": str(data.get("display_name", tool_key)),
                "description": str(data.get("description", "")),
                "category": str(data.get("category", "General")),
                "curator_visible": bool(data.get("curator_visible", True)),
                "allow_attach": bool(data.get("allow_attach", True)),
                "allow_execute": bool(data.get("allow_execute", True)),
                "config": json.dumps(dict(data.get("config", {}) or {})),
            },
        )


def downgrade() -> None:
    op.drop_index("ix_tool_policies_curator_visible", table_name="tool_policies")
    op.drop_index("ix_tool_policies_category", table_name="tool_policies")
    op.drop_table("tool_policies")
