"""Tool policy default loader with package-first merge semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.lib.packages import ExportKind

from .package_default_sources import (
    load_optional_runtime_yaml_source,
    load_package_yaml_sources,
)


@dataclass
class ToolPolicyDefault:
    """One merged tool policy default entry."""

    tool_key: str
    display_name: str
    description: str = ""
    category: str = "General"
    curator_visible: bool = True
    allow_attach: bool = True
    allow_execute: bool = True
    config: dict[str, Any] = field(default_factory=dict)
    source_label: str | None = None

    @classmethod
    def from_yaml(
        cls,
        tool_key: str,
        data: dict[str, Any],
        *,
        source_label: str,
    ) -> "ToolPolicyDefault":
        if not isinstance(data, dict):
            raise ValueError(
                f"Tool policy '{tool_key}' in {source_label} must be a mapping"
            )

        raw_config = data.get("config", {})
        if raw_config is None:
            raw_config = {}
        if not isinstance(raw_config, dict):
            raise ValueError(
                f"Tool policy '{tool_key}' in {source_label} field 'config' must be a mapping"
            )

        return cls(
            tool_key=tool_key,
            display_name=str(data.get("display_name", tool_key)).strip() or tool_key,
            description=str(data.get("description", "")).strip(),
            category=str(data.get("category", "General")).strip() or "General",
            curator_visible=bool(data.get("curator_visible", True)),
            allow_attach=bool(data.get("allow_attach", True)),
            allow_execute=bool(data.get("allow_execute", True)),
            config=dict(raw_config),
            source_label=source_label,
        )


def load_tool_policy_defaults(
    tool_policies_path: Path | None = None,
    *,
    packages_dir: Path | None = None,
) -> dict[str, ToolPolicyDefault]:
    """Load tool policy defaults from package exports plus runtime overrides."""
    sources = list(
        load_package_yaml_sources(
            export_kind=ExportKind.TOOL_POLICY_DEFAULTS,
            packages_dir=packages_dir,
        )
    )
    runtime_source = load_optional_runtime_yaml_source(
        explicit_path=tool_policies_path,
        env_var="TOOL_POLICY_DEFAULTS_CONFIG_PATH",
        filename="tool_policy_defaults.yaml",
    )
    if runtime_source is not None:
        sources.append(runtime_source)

    if not sources:
        raise FileNotFoundError(
            "No tool policy defaults were found in runtime packages or runtime override config"
        )

    registry: dict[str, ToolPolicyDefault] = {}
    for source in sources:
        raw_policies = source.payload.get("tool_policies")
        if not isinstance(raw_policies, dict):
            raise ValueError(
                f"{source.describe()} must define a top-level 'tool_policies' mapping"
            )

        for tool_key, raw_policy in raw_policies.items():
            clean_key = str(tool_key or "").strip()
            if not clean_key:
                raise ValueError(f"{source.describe()} contains an empty tool policy key")
            registry[clean_key] = ToolPolicyDefault.from_yaml(
                clean_key,
                raw_policy,
                source_label=source.describe(),
            )

    return registry
