"""Load declarative Agent Studio flow recipes from runtime packages."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
import re
from string import Formatter
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.lib.agent_access import normalize_allowed_group_ids
from src.lib.flow_edge_roles import SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS

from .models import ExportKind
from .registry import PackageRegistry, load_package_registry
from .tool_registry import resolve_default_packages_dir


_FLOW_AGENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def _validate_flow_agent_id(value: str) -> str:
    if _FLOW_AGENT_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "agent ID must start with a letter or digit and contain only "
            "letters, digits, dots, underscores, hyphens, or colons"
        )
    return value


class FlowRecipeStep(BaseModel):
    """One simplified source step consumed by the flow recipe compiler."""

    model_config = ConfigDict(extra="forbid", strict=True)

    agent_id: str = Field(min_length=1)
    step_goal: str | None = None
    custom_instructions: str | None = None
    source_steps: list[int] | None = None
    output_filename_template: str | None = None

    @field_validator("agent_id")
    @classmethod
    def _validate_agent_id(cls, value: str) -> str:
        return _validate_flow_agent_id(value)


class FlowRecipeAccess(BaseModel):
    """Provider-neutral availability restrictions for one flow recipe."""

    model_config = ConfigDict(extra="forbid", strict=True)

    allowed_group_ids: list[str] = Field(default_factory=list)

    @field_validator("allowed_group_ids")
    @classmethod
    def _validate_allowed_group_ids(cls, value: list[str]) -> list[str]:
        return normalize_allowed_group_ids(
            value,
            field_name="flow recipe access.allowed_group_ids",
        )


class FlowRecipe(BaseModel):
    """One package-advertised flow recipe."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    access: FlowRecipeAccess = Field(default_factory=FlowRecipeAccess)
    steps: list[FlowRecipeStep] = Field(min_length=1)


class FlowAgentEquivalenceGroup(BaseModel):
    """Aliases and canonical IDs that represent one flow-facing capability."""

    model_config = ConfigDict(extra="forbid", strict=True)

    agent_ids: list[str] = Field(min_length=2)

    @field_validator("agent_ids")
    @classmethod
    def _require_unique_agent_ids(cls, value: list[str]) -> list[str]:
        value = [_validate_flow_agent_id(agent_id) for agent_id in value]
        if len(set(value)) != len(value):
            raise ValueError("agent_ids must not contain duplicates")
        return value


class FlowSuggestionRule(BaseModel):
    """Declarative package-owned guidance for composing related flow agents."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1)
    when_present: list[str] = Field(min_length=1)
    when_absent: list[str] = Field(min_length=1)
    suggested_agent_id: str = Field(min_length=1)
    placement: Literal["first", "after"]
    message: str = Field(min_length=1)

    @field_validator("when_present", "when_absent")
    @classmethod
    def _require_unique_rule_agent_ids(cls, value: list[str]) -> list[str]:
        value = [_validate_flow_agent_id(agent_id) for agent_id in value]
        if len(set(value)) != len(value):
            raise ValueError("agent IDs must not contain duplicates")
        return value

    @field_validator("suggested_agent_id")
    @classmethod
    def _validate_suggested_agent_id(cls, value: str) -> str:
        return _validate_flow_agent_id(value)

    @field_validator("message")
    @classmethod
    def _validate_message_placeholders(cls, value: str) -> str:
        allowed = {"suggested_agent_id", "trigger_agent_id"}
        parsed_fields = [
            (field_name, format_spec, conversion)
            for _, field_name, format_spec, conversion in Formatter().parse(value)
            if field_name is not None
        ]
        fields = {field_name for field_name, _, _ in parsed_fields}
        unsupported = sorted(fields - allowed)
        if unsupported:
            raise ValueError(
                "message contains unsupported placeholders: " + ", ".join(unsupported)
            )
        if any(format_spec or conversion for _, format_spec, conversion in parsed_fields):
            raise ValueError(
                "message placeholders must not use format specifications or conversions"
            )
        return value


class FlowRecipeManifest(BaseModel):
    """Strict package-local contract for recipes and domain composition hints."""

    model_config = ConfigDict(extra="forbid", strict=True)

    flow_recipes_api_version: Literal["1.0.0"]
    recipes: list[FlowRecipe] = Field(default_factory=list)
    equivalence_groups: list[FlowAgentEquivalenceGroup] = Field(default_factory=list)
    suggestions: list[FlowSuggestionRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_unique_local_keys(self) -> "FlowRecipeManifest":
        recipe_names = [recipe.name for recipe in self.recipes]
        suggestion_names = [suggestion.name for suggestion in self.suggestions]
        for label, values in (
            ("recipe names", recipe_names),
            ("suggestion names", suggestion_names),
        ):
            duplicates = sorted({value for value in values if values.count(value) > 1})
            if duplicates:
                raise ValueError(f"{label} contain duplicates: {', '.join(duplicates)}")

        members: set[str] = set()
        for group in self.equivalence_groups:
            overlap = members.intersection(group.agent_ids)
            if overlap:
                raise ValueError(
                    "equivalence_groups assign agent IDs more than once: "
                    + ", ".join(sorted(overlap))
                )
            members.update(group.agent_ids)
        return self


@dataclass(frozen=True)
class LoadedFlowRecipeManifest:
    """One validated package contribution with source provenance."""

    package_id: str
    export_name: str
    source_path: Path
    manifest: FlowRecipeManifest


@dataclass(frozen=True)
class FlowRecipeCatalog:
    """Merged flow recipe contributions from all loaded runtime packages."""

    contributions: tuple[LoadedFlowRecipeManifest, ...]

    @property
    def recipes(self) -> tuple[FlowRecipe, ...]:
        return tuple(
            recipe
            for contribution in self.contributions
            for recipe in contribution.manifest.recipes
        )

    @property
    def equivalence_groups(self) -> tuple[FlowAgentEquivalenceGroup, ...]:
        return tuple(
            group
            for contribution in self.contributions
            for group in contribution.manifest.equivalence_groups
        )

    @property
    def suggestions(self) -> tuple[FlowSuggestionRule, ...]:
        return tuple(
            suggestion
            for contribution in self.contributions
            for suggestion in contribution.manifest.suggestions
        )


class FlowRecipeLoadError(ValueError):
    """Raised when package flow-recipe metadata is invalid or ambiguous."""


def _load_yaml_mapping(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise FlowRecipeLoadError(f"Flow recipe contract not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise FlowRecipeLoadError(f"Invalid YAML in flow recipe contract {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FlowRecipeLoadError(f"Flow recipe contract {path} must contain a YAML mapping")
    return data


def _format_validation_error(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'model'}: {item['msg']}"
        for item in exc.errors()
    )


def _validation_recipe_context(raw_data: dict, exc: ValidationError) -> str:
    """Return recipe names implicated by schema errors when they are available."""

    raw_recipes = raw_data.get("recipes")
    if not isinstance(raw_recipes, list):
        return ""
    indices = {
        item["loc"][1]
        for item in exc.errors()
        if len(item["loc"]) >= 2
        and item["loc"][0] == "recipes"
        and isinstance(item["loc"][1], int)
    }
    names = []
    for index in sorted(indices):
        if index >= len(raw_recipes) or not isinstance(raw_recipes[index], dict):
            continue
        name = raw_recipes[index].get("name")
        if isinstance(name, str) and name.strip():
            names.append(name)
    if not names:
        return ""
    return " for recipe " + ", ".join(repr(name) for name in names)


def _load_contributions(registry: PackageRegistry) -> tuple[LoadedFlowRecipeManifest, ...]:
    loaded: list[LoadedFlowRecipeManifest] = []
    for package in registry.loaded_packages:
        for export in package.manifest.exports:
            if export.kind is not ExportKind.FLOW_RECIPES:
                continue
            source_path = (package.package_path / export.path).resolve(strict=False)
            try:
                raw_data = _load_yaml_mapping(source_path)
            except FlowRecipeLoadError as exc:
                raise FlowRecipeLoadError(
                    f"Invalid flow recipe export '{export.name}' from package "
                    f"'{package.package_id}' at {source_path}: {exc}"
                ) from exc
            try:
                manifest = FlowRecipeManifest.model_validate(raw_data)
            except ValidationError as exc:
                recipe_context = _validation_recipe_context(raw_data, exc)
                raise FlowRecipeLoadError(
                    f"Invalid flow recipe export '{export.name}' from package "
                    f"'{package.package_id}' at {source_path}{recipe_context}: "
                    f"{_format_validation_error(exc)}"
                ) from exc
            loaded.append(
                LoadedFlowRecipeManifest(
                    package_id=package.package_id,
                    export_name=export.name,
                    source_path=source_path,
                    manifest=manifest,
                )
            )
    return tuple(loaded)


def build_flow_recipe_catalog(registry: PackageRegistry) -> FlowRecipeCatalog:
    """Load and merge package recipe exports with deterministic collision errors."""

    contributions = _load_contributions(registry)
    recipe_owners: dict[str, LoadedFlowRecipeManifest] = {}
    suggestion_owners: dict[str, LoadedFlowRecipeManifest] = {}
    equivalence_owners: dict[str, LoadedFlowRecipeManifest] = {}

    for contribution in contributions:
        for recipe in contribution.manifest.recipes:
            existing = recipe_owners.get(recipe.name)
            if existing is not None:
                raise FlowRecipeLoadError(
                    f"Flow recipe name collision '{recipe.name}' between "
                    f"{existing.package_id}:{existing.source_path} and "
                    f"{contribution.package_id}:{contribution.source_path}"
                )
            recipe_owners[recipe.name] = contribution
        for suggestion in contribution.manifest.suggestions:
            existing = suggestion_owners.get(suggestion.name)
            if existing is not None:
                raise FlowRecipeLoadError(
                    f"Flow suggestion name collision '{suggestion.name}' between "
                    f"{existing.package_id}:{existing.source_path} and "
                    f"{contribution.package_id}:{contribution.source_path}"
                )
            suggestion_owners[suggestion.name] = contribution
        for group in contribution.manifest.equivalence_groups:
            for agent_id in group.agent_ids:
                if agent_id in SUPPORTED_OUTPUT_FORMATTER_AGENT_IDS:
                    raise FlowRecipeLoadError(
                        "Invalid flow agent equivalence group from package "
                        f"'{contribution.package_id}' at "
                        f"{contribution.source_path}: formatter ID '{agent_id}' "
                        "is core-owned and cannot be redefined"
                    )
                existing = equivalence_owners.get(agent_id)
                if existing is not None:
                    raise FlowRecipeLoadError(
                        f"Flow agent equivalence collision '{agent_id}' between "
                        f"{existing.package_id}:{existing.source_path} and "
                        f"{contribution.package_id}:{contribution.source_path}"
                    )
                equivalence_owners[agent_id] = contribution

    return FlowRecipeCatalog(contributions=contributions)


@cache
def _load_flow_recipe_catalog_for_path(packages_dir: Path) -> FlowRecipeCatalog:
    """Cache immutable package metadata for one runtime package root."""

    registry = load_package_registry(packages_dir, fail_on_validation_error=True)
    return build_flow_recipe_catalog(registry)


def load_flow_recipe_catalog() -> FlowRecipeCatalog:
    """Load recipe contributions from the active package directory."""

    packages_dir = resolve_default_packages_dir().resolve(strict=False)
    return _load_flow_recipe_catalog_for_path(packages_dir)
