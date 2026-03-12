"""Resolve agent configuration assets from package exports or explicit directories."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from src.lib.packages import ExportKind, LoadedPackage, PackageExport, load_package_registry
from src.lib.packages.paths import get_runtime_packages_dir


def _find_project_root() -> Path | None:
    """Find the repository root by looking for common project markers."""
    current = Path(__file__).resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / "docker-compose.test.yml").exists():
            return candidate
        if (candidate / "backend").is_dir() and (candidate / "packages").is_dir():
            return candidate
        if (candidate / "packages").is_dir() and (candidate / "config" / "agents").is_dir():
            return candidate
    return None


def get_default_agent_search_path() -> Path:
    """Return the default package search root for shipped system agents."""
    env_path = os.environ.get("AGENTS_CONFIG_PATH")
    if env_path:
        return Path(env_path)

    runtime_packages_dir = get_runtime_packages_dir()
    if runtime_packages_dir.exists():
        return runtime_packages_dir

    project_root = _find_project_root()
    if project_root:
        return project_root / "packages"

    return runtime_packages_dir


def _resolve_search_path(search_path: Path | None) -> tuple[Path, bool]:
    """Resolve the configured search path and whether it is the implicit default."""
    if search_path is not None:
        return search_path.expanduser().resolve(strict=False), False

    env_path = os.environ.get("AGENTS_CONFIG_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve(strict=False), False

    return get_default_agent_search_path().expanduser().resolve(strict=False), True


@dataclass(frozen=True)
class AgentConfigSource:
    """Resolved filesystem assets for one agent configuration bundle."""

    folder_name: str
    agent_dir: Path
    agent_yaml: Path | None
    prompt_yaml: Path | None
    schema_py: Path | None
    group_rule_files: tuple[Path, ...]
    package_id: str | None = None
    package_path: Path | None = None

    def source_file_display(self, path: Path) -> str:
        """Return a stable provenance string for an asset path."""
        if self.package_id and self.package_path:
            return f"packages/{self.package_id}/{path.relative_to(self.package_path).as_posix()}"

        project_root = _find_project_root()
        if project_root:
            try:
                return str(path.relative_to(project_root))
            except ValueError:
                pass

        return str(path)


def _looks_like_single_package(path: Path) -> bool:
    return path.is_dir() and (path / "package.yaml").exists()


def _looks_like_packages_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any(child.is_dir() and (child / "package.yaml").exists() for child in path.iterdir())


def _looks_like_agent_directory(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any(
        child.is_dir() and (child / "agent.yaml").exists()
        for child in path.iterdir()
    )


def _resolve_package_agent_sources(package: LoadedPackage) -> tuple[AgentConfigSource, ...]:
    """Resolve agent-owned config assets exported by one runtime package.

    Prompt, schema, and group-rule exports are treated as adjunct assets of an
    AGENT export, not standalone load targets, so orphan exports fail fast.
    """
    agent_exports = {
        export.name: export
        for export in package.manifest.exports
        if export.kind == ExportKind.AGENT
    }
    prompt_exports = {
        export.name[:-len(".system")]: export
        for export in package.manifest.exports
        if export.kind == ExportKind.PROMPT and export.name.endswith(".system")
    }
    schema_exports = {
        export.name[:-len(".schema")]: export
        for export in package.manifest.exports
        if export.kind == ExportKind.SCHEMA and export.name.endswith(".schema")
    }
    group_rule_exports: dict[str, list[PackageExport]] = {}
    for export in package.manifest.exports:
        if export.kind != ExportKind.GROUP_RULE:
            continue
        agent_name, _, _rule_name = export.name.partition(".")
        group_rule_exports.setdefault(agent_name, []).append(export)

    orphan_exports = sorted(
        (
            set(prompt_exports)
            | set(schema_exports)
            | set(group_rule_exports)
        )
        - set(agent_exports)
    )
    if orphan_exports:
        missing = ", ".join(orphan_exports)
        raise ValueError(
            f"Package '{package.package_id}' exports prompt/schema/group rules for "
            f"unknown agent bundle(s): {missing}"
        )

    sources: list[AgentConfigSource] = []
    for folder_name, export in sorted(agent_exports.items()):
        agent_dir = package.package_path / export.path
        if not agent_dir.exists():
            raise FileNotFoundError(
                f"Package '{package.package_id}' agent export '{folder_name}' points to "
                f"missing directory: {agent_dir}"
            )
        if not agent_dir.is_dir():
            raise ValueError(
                f"Package '{package.package_id}' agent export '{folder_name}' must point "
                f"to a directory: {agent_dir}"
            )
        agent_yaml = agent_dir / "agent.yaml"
        if not agent_yaml.exists():
            raise FileNotFoundError(
                f"Package '{package.package_id}' agent bundle '{folder_name}' "
                f"is missing agent.yaml at {agent_yaml}"
            )

        prompt_yaml = None
        prompt_export = prompt_exports.get(folder_name)
        if prompt_export:
            prompt_yaml = package.package_path / prompt_export.path
            if not prompt_yaml.exists():
                raise FileNotFoundError(
                    f"Package '{package.package_id}' prompt export '{prompt_export.name}' "
                    f"points to missing file: {prompt_yaml}"
                )

        schema_py = None
        schema_export = schema_exports.get(folder_name)
        if schema_export:
            schema_py = package.package_path / schema_export.path
            if not schema_py.exists():
                raise FileNotFoundError(
                    f"Package '{package.package_id}' schema export '{schema_export.name}' "
                    f"points to missing file: {schema_py}"
                )

        group_rule_files: list[Path] = []
        for rule_export in sorted(
            group_rule_exports.get(folder_name, ()),
            key=lambda item: item.name,
        ):
            rule_path = package.package_path / rule_export.path
            if not rule_path.exists():
                raise FileNotFoundError(
                    f"Package '{package.package_id}' group rule export '{rule_export.name}' "
                    f"points to missing file: {rule_path}"
                )
            group_rule_files.append(rule_path)

        sources.append(
            AgentConfigSource(
                folder_name=folder_name,
                agent_dir=agent_dir,
                agent_yaml=agent_yaml,
                prompt_yaml=prompt_yaml,
                schema_py=schema_py,
                group_rule_files=tuple(group_rule_files),
                package_id=package.package_id,
                package_path=package.package_path,
            )
        )

    return tuple(sources)


def _resolve_legacy_agent_sources(agents_path: Path) -> tuple[AgentConfigSource, ...]:
    return tuple(
        AgentConfigSource(
            folder_name=folder.name,
            agent_dir=folder,
            agent_yaml=folder / "agent.yaml",
            prompt_yaml=folder / "prompt.yaml",
            schema_py=folder / "schema.py",
            group_rule_files=tuple(
                sorted(
                    path
                    for path in (folder / "group_rules").glob("*.yaml")
                    if path.name != "example.yaml" and not path.name.startswith("_")
                )
            ) if (folder / "group_rules").exists() else (),
        )
        for folder in sorted(agents_path.iterdir())
        if folder.is_dir() and not folder.name.startswith("_")
    )


def resolve_agent_config_sources(
    search_path: Path | None = None,
) -> tuple[AgentConfigSource, ...]:
    """Resolve agent config bundles from a packages root, one package, or a legacy agents dir."""
    resolved_path, used_default_search_path = _resolve_search_path(search_path)
    if not resolved_path.exists():
        raise FileNotFoundError(f"Agent source path not found: {resolved_path}")

    if _looks_like_single_package(resolved_path):
        registry = load_package_registry(
            resolved_path.parent,
            fail_on_validation_error=True,
        )
        package = next(
            (
                item
                for item in registry.loaded_packages
                if item.package_path == resolved_path
            ),
            None,
        )
        if package is None:
            raise FileNotFoundError(
                f"Package directory is not a loaded runtime package: {resolved_path}"
            )
        sources = _resolve_package_agent_sources(package)
    elif _looks_like_packages_root(resolved_path):
        registry = load_package_registry(
            resolved_path,
            fail_on_validation_error=True,
        )
        sources = tuple(
            source
            for package in registry.loaded_packages
            for source in _resolve_package_agent_sources(package)
        )
    elif used_default_search_path:
        raise FileNotFoundError(
            "No runtime packages with package manifests were found in the default "
            f"agent source path: {resolved_path}"
        )
    elif resolved_path.is_dir():
        sources = _resolve_legacy_agent_sources(resolved_path)
    else:
        raise FileNotFoundError(
            "Agent source path must be a runtime packages root, a package directory, "
            f"or a legacy agents directory: {resolved_path}"
        )

    owners: dict[str, AgentConfigSource] = {}
    for source in sources:
        if source.folder_name in owners:
            existing = owners[source.folder_name]
            raise ValueError(
                f"Duplicate agent bundle '{source.folder_name}' discovered in "
                f"{existing.package_id or existing.agent_dir} and {source.package_id or source.agent_dir}"
            )
        owners[source.folder_name] = source

    return tuple(sorted(sources, key=lambda item: item.folder_name))
