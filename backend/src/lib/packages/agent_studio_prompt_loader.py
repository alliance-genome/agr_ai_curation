"""Resolve the package-owned Agent Studio system prompt."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
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

    selected = _select_candidate(
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

    resolved_packages_dir = (packages_dir or get_runtime_packages_dir()).resolve(
        strict=False
    )
    resolved_overrides_path = (
        overrides_path or get_runtime_overrides_path()
    ).resolve(strict=False)
    return _load_installed_agent_studio_prompt_cached(
        resolved_packages_dir,
        resolved_overrides_path,
    )


@cache
def _load_installed_agent_studio_prompt_cached(
    packages_dir: Path,
    overrides_path: Path,
) -> LoadedAgentStudioPrompt:
    """Load one installed prompt per resolved runtime profile path."""

    registry = load_package_registry(
        packages_dir,
        fail_on_validation_error=True,
    )
    runtime_overrides = (
        load_runtime_overrides(overrides_path)
        if overrides_path.is_file()
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
    selections = tuple(
        selection
        for selection in (runtime_overrides.selections if runtime_overrides else ())
        if selection.export_kind is ExportKind.AGENT_STUDIO_PROMPT
    )
    if len(selections) > 1:
        selected_refs = ", ".join(
            f"'{selection.package_id}:{selection.name}'" for selection in selections
        )
        raise AgentStudioPromptLoadError(
            "Multiple runtime override selections choose Agent Studio prompts: "
            f"{selected_refs}. Configure exactly one selection."
        )

    if selections:
        selection = selections[0]
        for candidate in candidates:
            if (
                selection.name == candidate.export.name
                and selection.package_id == candidate.package.package_id
            ):
                return candidate
        sources = "; ".join(candidate.source.describe() for candidate in candidates)
        raise AgentStudioPromptLoadError(
            "Runtime override selects Agent Studio prompt "
            f"'{selection.package_id}:{selection.name}', but it is not an active "
            f"candidate. Active candidates: {sources}."
        )

    return candidates[0] if len(candidates) == 1 else None


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
