"""Resolve the package-owned Agent Studio system prompt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .manifest_loader import load_runtime_overrides
from .models import ExportKind, PackageExport, RuntimeOverrides
from .paths import get_runtime_overrides_path, get_runtime_packages_dir
from .registry import LoadedPackage, PackageRegistry, load_package_registry


class AgentStudioPromptLoadError(ValueError):
    """Raised when the installed packages do not select one usable prompt."""


@dataclass(frozen=True, slots=True)
class AgentStudioPromptSource:
    """Package/export/file provenance for an Agent Studio prompt."""

    package_id: str
    manifest_path: Path
    export_name: str
    prompt_path: Path

    def describe(self) -> str:
        return (
            f"package '{self.package_id}' manifest {self.manifest_path}, "
            f"export '{self.export_name}' at {self.prompt_path}"
        )


@dataclass(frozen=True, slots=True)
class LoadedAgentStudioPrompt:
    """Selected Agent Studio prompt text plus its package provenance."""

    content: str
    source: AgentStudioPromptSource


@dataclass(frozen=True, slots=True)
class _PromptCandidate:
    package: LoadedPackage
    export: PackageExport
    source: AgentStudioPromptSource


def resolve_agent_studio_prompt(
    registry: PackageRegistry,
    *,
    runtime_overrides: RuntimeOverrides | None = None,
) -> LoadedAgentStudioPrompt:
    """Resolve exactly one Agent Studio prompt from the active package profile."""

    disabled_packages = set(
        runtime_overrides.disabled_packages if runtime_overrides else ()
    )
    candidates = tuple(
        _build_candidate(package, export)
        for package in registry.loaded_packages
        if package.package_id not in disabled_packages
        for export in package.manifest.exports
        if export.kind is ExportKind.AGENT_STUDIO_PROMPT
    )

    if not candidates:
        loaded_ids = ", ".join(
            package.package_id
            for package in registry.loaded_packages
            if package.package_id not in disabled_packages
        ) or "none"
        raise AgentStudioPromptLoadError(
            "No active package exports an Agent Studio system prompt "
            f"(loaded packages: {loaded_ids})"
        )

    selected = candidates[0] if len(candidates) == 1 else _select_candidate(
        candidates,
        runtime_overrides=runtime_overrides,
    )
    if selected is None:
        sources = "; ".join(candidate.source.describe() for candidate in candidates)
        raise AgentStudioPromptLoadError(
            "Multiple active packages export an Agent Studio system prompt: "
            f"{sources}. Add one runtime override selection with export_kind "
            "'agent_studio_prompt' to choose the active package export."
        )

    return _read_candidate(selected)


def load_installed_agent_studio_prompt(
    packages_dir: Path | None = None,
    *,
    overrides_path: Path | None = None,
) -> LoadedAgentStudioPrompt:
    """Load the selected Agent Studio prompt from the installed runtime profile."""

    registry = load_package_registry(
        packages_dir or get_runtime_packages_dir(),
        fail_on_validation_error=True,
    )
    resolved_overrides_path = overrides_path or get_runtime_overrides_path()
    runtime_overrides = (
        load_runtime_overrides(resolved_overrides_path)
        if resolved_overrides_path.is_file()
        else None
    )
    return resolve_agent_studio_prompt(
        registry,
        runtime_overrides=runtime_overrides,
    )


def _build_candidate(
    package: LoadedPackage,
    export: PackageExport,
) -> _PromptCandidate:
    prompt_path = (package.package_path / export.path).expanduser().resolve(
        strict=False
    )
    source = AgentStudioPromptSource(
        package_id=package.package_id,
        manifest_path=package.manifest_path,
        export_name=export.name,
        prompt_path=prompt_path,
    )
    try:
        prompt_path.relative_to(package.package_path.resolve(strict=False))
    except ValueError as exc:
        raise AgentStudioPromptLoadError(
            f"Agent Studio prompt {source.describe()} resolves outside its package root"
        ) from exc
    return _PromptCandidate(package=package, export=export, source=source)


def _select_candidate(
    candidates: tuple[_PromptCandidate, ...],
    *,
    runtime_overrides: RuntimeOverrides | None,
) -> _PromptCandidate | None:
    if runtime_overrides is None:
        return None

    matches = tuple(
        candidate
        for candidate in candidates
        if any(
            selection.export_kind is ExportKind.AGENT_STUDIO_PROMPT
            and selection.name == candidate.export.name
            and selection.package_id == candidate.package.package_id
            for selection in runtime_overrides.selections
        )
    )
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        sources = "; ".join(candidate.source.describe() for candidate in matches)
        raise AgentStudioPromptLoadError(
            "Multiple runtime override selections match Agent Studio prompt exports: "
            f"{sources}"
        )
    return None


def _read_candidate(candidate: _PromptCandidate) -> LoadedAgentStudioPrompt:
    source = candidate.source
    if not source.prompt_path.is_file():
        raise AgentStudioPromptLoadError(
            f"Agent Studio prompt {source.describe()} does not exist or is not a file"
        )
    try:
        content = source.prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AgentStudioPromptLoadError(
            f"Failed to read Agent Studio prompt {source.describe()}: {exc}"
        ) from exc
    if not content.strip():
        raise AgentStudioPromptLoadError(
            f"Agent Studio prompt {source.describe()} is empty"
        )
    return LoadedAgentStudioPrompt(content=content, source=source)
