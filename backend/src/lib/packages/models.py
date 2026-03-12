"""Strict schemas for modular runtime package contracts."""

from __future__ import annotations

import re
from enum import Enum
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
PACKAGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SYMBOLIC_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
PYTHON_CALLABLE_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_\.]*:[A-Za-z_][A-Za-z0-9_]*$"
)


def _validate_semver(value: str, field_name: str) -> str:
    if not SEMVER_PATTERN.match(value):
        raise ValueError(
            f"{field_name} must use semantic version format like 1.2.3"
        )
    return value


def _validate_package_id(value: str) -> str:
    if not PACKAGE_ID_PATTERN.match(value):
        raise ValueError(
            "package_id must start with a lowercase letter or digit and only use "
            "lowercase letters, digits, dots, underscores, or hyphens"
        )
    return value


def _validate_symbolic_name(value: str, field_name: str) -> str:
    if not SYMBOLIC_NAME_PATTERN.match(value):
        raise ValueError(
            f"{field_name} must start with a letter or digit and only use "
            "letters, digits, dots, underscores, hyphens, or colons"
        )
    return value


def _validate_relative_package_path(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if "\\" in value:
        raise ValueError(f"{field_name} must use forward slashes")

    normalized = PurePosixPath(value)
    if normalized.is_absolute():
        raise ValueError(f"{field_name} must be relative to the package root")
    if ".." in normalized.parts:
        raise ValueError(f"{field_name} must not traverse parent directories")
    if normalized.parts and normalized.parts[0] == ".":
        raise ValueError(f"{field_name} must not start with './'")

    return str(normalized)


def _core_semver(value: str) -> tuple[int, int, int]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise ValueError(f"Invalid semantic version: {value}")
    major, minor, patch = (int(part) for part in match.groups())
    return (major, minor, patch)


def _require_unique(values: list[str], field_name: str) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        duplicate_list = ", ".join(sorted(duplicates))
        raise ValueError(f"{field_name} contains duplicate entries: {duplicate_list}")
    return values


class ExportKind(str, Enum):
    """Canonical exported content types a package may publish."""

    AGENT = "agent"
    PROMPT = "prompt"
    GROUP_RULE = "group_rule"
    SCHEMA = "schema"
    TOOL_BINDING = "tool_binding"
    MODEL = "model"
    PROVIDER = "provider"
    TOOL_POLICY_DEFAULTS = "tool_policy_defaults"
    CONNECTION = "connection"
    IDENTIFIER_PREFIXES = "identifier_prefixes"


class ToolBindingType(str, Enum):
    """Supported tool binding targets."""

    PYTHON_CALLABLE = "python_callable"


class ToolBindingKind(str, Enum):
    """Supported runtime binding behaviors for exported tools."""

    STATIC = "static"
    CONTEXT_FACTORY = "context_factory"


class PackageExport(BaseModel):
    """One exported runtime artifact declared by a package manifest."""

    model_config = ConfigDict(extra="forbid")

    kind: ExportKind
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    description: str = ""

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _validate_symbolic_name(value, "name")

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _validate_relative_package_path(value, "path")


class AgentBundleSpec(BaseModel):
    """Shorthand description for one agent export bundle in a package manifest."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    agents_dir: str = Field(default="agents", min_length=1)
    has_schema: bool = False
    group_rules: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _validate_symbolic_name(value, "agent_bundles.name")

    @field_validator("agents_dir")
    @classmethod
    def _validate_agents_dir(cls, value: str) -> str:
        return _validate_relative_package_path(value, "agent_bundles.agents_dir")

    @field_validator("group_rules")
    @classmethod
    def _validate_group_rules(cls, value: list[str]) -> list[str]:
        validated = [
            _validate_symbolic_name(item, "agent_bundles.group_rules entry")
            for item in value
        ]
        return _require_unique(validated, "agent_bundles.group_rules")


class PackageManifest(BaseModel):
    """Schema for a runtime package's ``package.yaml`` contract."""

    model_config = ConfigDict(extra="forbid")

    package_id: str
    display_name: str = Field(min_length=1)
    version: str
    package_api_version: str
    min_runtime_version: str
    max_runtime_version: str
    python_package_root: str = Field(min_length=1)
    requirements_file: str = Field(min_length=1)
    exports: list[PackageExport] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _expand_agent_bundles(cls, data: Any) -> Any:
        """Expand ``agent_bundles`` shorthand into flat ``exports`` entries."""
        if not isinstance(data, dict):
            return data

        bundle_payloads = data.pop("agent_bundles", None)
        if not bundle_payloads:
            return data

        expanded_exports: list[dict[str, str]] = []
        for raw_bundle in bundle_payloads:
            bundle = AgentBundleSpec.model_validate(raw_bundle)
            agent_root = f"{bundle.agents_dir}/{bundle.name}"
            expanded_exports.extend(
                [
                    {
                        "kind": ExportKind.AGENT.value,
                        "name": bundle.name,
                        "path": agent_root,
                        "description": f"Built-in {bundle.name} agent definition bundle",
                    },
                    {
                        "kind": ExportKind.PROMPT.value,
                        "name": f"{bundle.name}.system",
                        "path": f"{agent_root}/prompt.yaml",
                        "description": f"Base system prompt for the {bundle.name} agent",
                    },
                ]
            )

            if bundle.has_schema:
                expanded_exports.append(
                    {
                        "kind": ExportKind.SCHEMA.value,
                        "name": f"{bundle.name}.schema",
                        "path": f"{agent_root}/schema.py",
                        "description": f"Package-owned schema file for the {bundle.name} agent",
                    }
                )

            for rule_name in bundle.group_rules:
                rule_label = rule_name.upper()
                expanded_exports.append(
                    {
                        "kind": ExportKind.GROUP_RULE.value,
                        "name": f"{bundle.name}.{rule_label}",
                        "path": f"{agent_root}/group_rules/{rule_name}.yaml",
                        "description": f"Group-specific rules for {bundle.name} ({rule_label})",
                    }
                )

        data = dict(data)
        data["exports"] = list(data.get("exports", [])) + expanded_exports
        return data

    @field_validator("package_id")
    @classmethod
    def _validate_package_id(cls, value: str) -> str:
        return _validate_package_id(value)

    @field_validator("version", "package_api_version", "min_runtime_version", "max_runtime_version")
    @classmethod
    def _validate_versions(cls, value: str, info) -> str:
        return _validate_semver(value, info.field_name)

    @field_validator("python_package_root", "requirements_file")
    @classmethod
    def _validate_manifest_paths(cls, value: str, info) -> str:
        return _validate_relative_package_path(value, info.field_name)

    @model_validator(mode="after")
    def _validate_runtime_range(self) -> "PackageManifest":
        if _core_semver(self.max_runtime_version) < _core_semver(self.min_runtime_version):
            raise ValueError(
                "max_runtime_version must be greater than or equal to min_runtime_version"
            )

        export_keys = [f"{export.kind.value}:{export.name}" for export in self.exports]
        _require_unique(export_keys, "exports")
        return self


class ToolBinding(BaseModel):
    """One tool name to callable binding declared by ``tools/bindings.yaml``."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1)
    binding_kind: ToolBindingKind
    callable: str | None = None
    callable_factory: str | None = None
    binding_type: ToolBindingType = ToolBindingType.PYTHON_CALLABLE
    required_context: list[str] = Field(default_factory=list)
    description: str = ""
    source_file: str | None = None

    @field_validator("tool_id")
    @classmethod
    def _validate_tool_id(cls, value: str) -> str:
        return _validate_symbolic_name(value, "tool_id")

    @field_validator("callable", "callable_factory")
    @classmethod
    def _validate_callable_import(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not PYTHON_CALLABLE_PATTERN.match(value):
            raise ValueError(
                "must use python callable syntax like package.module:function"
            )
        return value

    @field_validator("required_context")
    @classmethod
    def _validate_required_context(cls, value: list[str]) -> list[str]:
        validated = [
            _validate_symbolic_name(item, "required_context entry")
            for item in value
        ]
        return _require_unique(validated, "required_context")

    @field_validator("source_file")
    @classmethod
    def _validate_source_file(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_relative_package_path(value, "source_file")

    @model_validator(mode="after")
    def _validate_import_target(self) -> "ToolBinding":
        if self.binding_type is not ToolBindingType.PYTHON_CALLABLE:
            raise ValueError(
                f"Unsupported binding_type '{self.binding_type.value}'"
            )

        declared_targets = [
            value
            for value in (self.callable, self.callable_factory)
            if value is not None
        ]
        if len(declared_targets) != 1:
            raise ValueError(
                "exactly one of callable or callable_factory must be provided"
            )
        return self


class ToolBindingsManifest(BaseModel):
    """Schema for package-local ``tools/bindings.yaml`` declarations."""

    model_config = ConfigDict(extra="forbid")

    package_id: str
    bindings_api_version: str
    tools: list[ToolBinding] = Field(min_length=1)

    @field_validator("package_id")
    @classmethod
    def _validate_package_id(cls, value: str) -> str:
        return _validate_package_id(value)

    @field_validator("bindings_api_version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        return _validate_semver(value, "bindings_api_version")

    @model_validator(mode="after")
    def _validate_unique_tools(self) -> "ToolBindingsManifest":
        _require_unique([tool.tool_id for tool in self.tools], "tools")
        return self


class RuntimeOverrideSelection(BaseModel):
    """Explicit collision winner for one exported ``kind`` + ``name`` tuple."""

    model_config = ConfigDict(extra="forbid")

    export_kind: ExportKind
    name: str = Field(min_length=1)
    package_id: str
    reason: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _validate_symbolic_name(value, "name")

    @field_validator("package_id")
    @classmethod
    def _validate_package_id(cls, value: str) -> str:
        return _validate_package_id(value)


class RuntimeOverrides(BaseModel):
    """Schema for ``runtime/config/overrides.yaml``.

    ``package_precedence`` is a coarse ordering for packages that do not collide.
    ``selections`` is the explicit collision-resolution map keyed by exported
    content kind plus exported name. ``disabled_packages`` allows operators to
    keep a package on disk while excluding it from a deployment.
    """

    model_config = ConfigDict(extra="forbid")

    overrides_api_version: str
    package_precedence: list[str] = Field(default_factory=list)
    disabled_packages: list[str] = Field(default_factory=list)
    selections: list[RuntimeOverrideSelection] = Field(default_factory=list)

    @field_validator("overrides_api_version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        return _validate_semver(value, "overrides_api_version")

    @field_validator("package_precedence", "disabled_packages")
    @classmethod
    def _validate_package_lists(cls, value: list[str], info) -> list[str]:
        validated = [_validate_package_id(item) for item in value]
        return _require_unique(validated, info.field_name)

    @model_validator(mode="after")
    def _validate_unique_selections(self) -> "RuntimeOverrides":
        selection_keys = [
            f"{selection.export_kind.value}:{selection.name}"
            for selection in self.selections
        ]
        _require_unique(selection_keys, "selections")
        return self
