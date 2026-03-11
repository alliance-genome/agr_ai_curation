"""Merged runtime registry for package-declared tool bindings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .manifest_loader import load_runtime_overrides
from .models import ExportKind, RuntimeOverrides, ToolBinding, ToolBindingKind
from .paths import get_runtime_overrides_path
from .registry import PackageRegistry, load_package_registry
from .tool_bindings_loader import (
    LoadedToolBindingExport,
    ToolBindingLoadError,
    load_package_tool_binding_exports,
)


@dataclass(frozen=True)
class ToolBindingSource:
    """Package metadata for one winning tool binding."""

    package_id: str
    package_version: str
    package_display_name: str
    package_path: Path
    export_name: str
    export_description: str
    bindings_path: Path
    source_file: str | None


@dataclass(frozen=True)
class RegisteredToolBinding:
    """One runtime tool binding entry after package loading and merge."""

    tool_id: str
    binding_kind: ToolBindingKind
    import_path: str
    import_attribute_kind: str
    required_context: tuple[str, ...]
    description: str
    source: ToolBindingSource


@dataclass(frozen=True)
class ToolBindingCollision:
    """One conflicting tool ID with all candidate bindings."""

    tool_id: str
    candidates: tuple[RegisteredToolBinding, ...]
    selected: RegisteredToolBinding | None = None


class ToolRegistryValidationError(ValueError):
    """Raised when the merged tool registry is unsafe to consume."""


@dataclass(frozen=True)
class ToolRegistry:
    """In-memory merged tool-binding registry with diagnostics."""

    package_registry: PackageRegistry
    runtime_overrides: RuntimeOverrides | None
    loaded_binding_exports: tuple[LoadedToolBindingExport, ...]
    bindings: tuple[RegisteredToolBinding, ...]
    collisions: tuple[ToolBindingCollision, ...]
    validation_errors: tuple[str, ...] = ()

    @property
    def bindings_by_tool_id(self) -> dict[str, RegisteredToolBinding]:
        """Return winning bindings keyed by tool ID."""
        return {binding.tool_id: binding for binding in self.bindings}

    def get(self, tool_id: str) -> RegisteredToolBinding | None:
        """Return one merged tool binding by tool ID."""
        return self.bindings_by_tool_id.get(tool_id)

    def raise_for_validation_errors(self) -> None:
        """Raise one actionable validation error when merge failed."""
        if not self.validation_errors:
            return
        raise ToolRegistryValidationError("; ".join(self.validation_errors))


def load_tool_registry(
    packages_dir: Path | None = None,
    *,
    overrides_path: Path | None = None,
    runtime_version: str | None = None,
    supported_package_api_version: str | None = None,
    fail_on_validation_error: bool = True,
) -> ToolRegistry:
    """Build the merged runtime tool registry from loaded packages."""
    package_registry = load_package_registry(
        packages_dir,
        runtime_version=runtime_version,
        supported_package_api_version=supported_package_api_version,
        fail_on_validation_error=fail_on_validation_error,
    )
    runtime_overrides = _load_optional_runtime_overrides(overrides_path)
    registry = build_tool_registry(
        package_registry,
        runtime_overrides=runtime_overrides,
        fail_on_validation_error=fail_on_validation_error,
    )
    return registry


def build_tool_registry(
    package_registry: PackageRegistry,
    *,
    runtime_overrides: RuntimeOverrides | None = None,
    fail_on_validation_error: bool = True,
) -> ToolRegistry:
    """Merge package-declared tool binding exports into one registry."""
    eligible_packages = tuple(
        package
        for package in package_registry.loaded_packages
        if package.package_id
        not in set(runtime_overrides.disabled_packages if runtime_overrides else ())
    )

    loaded_binding_exports: list[LoadedToolBindingExport] = []
    validation_errors = list(package_registry.validation_errors)

    for package in eligible_packages:
        try:
            loaded_binding_exports.extend(load_package_tool_binding_exports(package))
        except ToolBindingLoadError as exc:
            validation_errors.append(str(exc))

    collisions: list[ToolBindingCollision] = []
    merged_bindings: list[RegisteredToolBinding] = []
    binding_groups: dict[str, list[RegisteredToolBinding]] = {}

    for binding_export in loaded_binding_exports:
        for binding in binding_export.manifest.tools:
            merged_binding = _build_registered_binding(binding_export, binding)
            binding_groups.setdefault(merged_binding.tool_id, []).append(merged_binding)

    for tool_id, candidates in sorted(binding_groups.items()):
        ordered_candidates = tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.source.package_id,
                    item.source.export_name,
                    str(item.source.bindings_path),
                ),
            )
        )
        if len(ordered_candidates) == 1:
            merged_bindings.append(ordered_candidates[0])
            continue

        selected, override_error = _select_override_winner(
            tool_id,
            ordered_candidates,
            runtime_overrides=runtime_overrides,
        )
        if override_error is not None:
            validation_errors.append(override_error)
        collisions.append(
            ToolBindingCollision(
                tool_id=tool_id,
                candidates=ordered_candidates,
                selected=selected,
            )
        )
        if selected is None:
            validation_errors.append(
                _format_collision_error(tool_id, ordered_candidates)
            )
            continue

        merged_bindings.append(selected)

    registry = ToolRegistry(
        package_registry=package_registry,
        runtime_overrides=runtime_overrides,
        loaded_binding_exports=tuple(
            sorted(
                loaded_binding_exports,
                key=lambda item: (
                    item.package_id,
                    item.export_name,
                    str(item.bindings_path),
                ),
            )
        ),
        bindings=tuple(sorted(merged_bindings, key=lambda item: item.tool_id)),
        collisions=tuple(collisions),
        validation_errors=tuple(validation_errors),
    )
    if fail_on_validation_error:
        registry.raise_for_validation_errors()
    return registry


def _build_registered_binding(
    binding_export: LoadedToolBindingExport,
    binding: ToolBinding,
) -> RegisteredToolBinding:
    import_path = binding.callable or binding.callable_factory
    import_attribute_kind = "callable" if binding.callable else "callable_factory"
    assert import_path is not None

    return RegisteredToolBinding(
        tool_id=binding.tool_id,
        binding_kind=binding.binding_kind,
        import_path=import_path,
        import_attribute_kind=import_attribute_kind,
        required_context=tuple(binding.required_context),
        description=binding.description or binding_export.export_description,
        source=ToolBindingSource(
            package_id=binding_export.package_id,
            package_version=binding_export.package_version,
            package_display_name=binding_export.package_display_name,
            package_path=binding_export.package_path,
            export_name=binding_export.export_name,
            export_description=binding_export.export_description,
            bindings_path=binding_export.bindings_path,
            source_file=binding.source_file,
        ),
    )


def _select_override_winner(
    tool_id: str,
    candidates: tuple[RegisteredToolBinding, ...],
    *,
    runtime_overrides: RuntimeOverrides | None,
) -> tuple[RegisteredToolBinding | None, str | None]:
    if runtime_overrides is None:
        return None, None

    matching_candidates = [
        candidate
        for candidate in candidates
        if any(
            selection.export_kind is ExportKind.TOOL_BINDING
            and selection.name == candidate.source.export_name
            and selection.package_id == candidate.source.package_id
            for selection in runtime_overrides.selections
        )
    ]
    if len(matching_candidates) == 1:
        return matching_candidates[0], None
    if len(matching_candidates) > 1:
        return None, (
            f"Multiple override selections match conflicting tool '{tool_id}': "
            + ", ".join(
                f"{candidate.source.package_id}:{candidate.source.export_name}"
                for candidate in matching_candidates
            )
        )
    return None, None


def _format_collision_error(
    tool_id: str,
    candidates: tuple[RegisteredToolBinding, ...],
) -> str:
    sources = ", ".join(
        f"{candidate.source.package_id}:{candidate.source.export_name}"
        for candidate in candidates
    )
    return (
        f"Conflicting tool binding '{tool_id}' exported by {sources}; "
        "add a runtime override selection for export_kind 'tool_binding' to pick a winner"
    )


def _load_optional_runtime_overrides(
    overrides_path: Path | None,
) -> RuntimeOverrides | None:
    resolved_path = overrides_path
    if resolved_path is None:
        candidate = get_runtime_overrides_path()
        if not candidate.exists():
            return None
        resolved_path = candidate

    return load_runtime_overrides(resolved_path)
