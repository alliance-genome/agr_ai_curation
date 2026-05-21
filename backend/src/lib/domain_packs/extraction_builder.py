"""Helpers for YAML-driven extraction builder contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry
from src.schemas.domain_pack_metadata import DomainPackExtractionBuilder


def extraction_builder_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> DomainPackExtractionBuilder | None:
    """Return a typed extraction builder config from domain-pack metadata."""

    if not isinstance(metadata, Mapping):
        return None
    raw_builder = metadata.get("extraction_builder")
    if raw_builder is None:
        return None
    builder = DomainPackExtractionBuilder.model_validate(raw_builder)
    return builder if builder.enabled else None


def extraction_builder_for_domain_pack(
    domain_pack_id: str | None,
    *,
    registries: Mapping[str, DomainPackValidationRegistry],
) -> DomainPackExtractionBuilder | None:
    """Return the enabled extraction builder for a domain pack, if any."""

    if not domain_pack_id:
        return None
    registry = registries.get(domain_pack_id)
    if registry is None:
        return None
    return extraction_builder_from_metadata(registry.domain_pack.metadata.metadata)


def extraction_builder_for_agent(
    agent_id: str,
    *,
    agent_registry: Mapping[str, Mapping[str, Any]],
    registries: Mapping[str, DomainPackValidationRegistry],
) -> DomainPackExtractionBuilder | None:
    """Return the enabled extraction builder declared for one runtime agent."""

    entry = agent_registry.get(agent_id)
    if not isinstance(entry, Mapping):
        return None
    curation = entry.get("curation")
    if not isinstance(curation, Mapping):
        return None
    domain_pack_id = _optional_text(curation.get("domain_pack_id"))
    return extraction_builder_for_domain_pack(domain_pack_id, registries=registries)


def builder_tool_names(builder: DomainPackExtractionBuilder | None) -> set[str]:
    """Return model-facing tool names controlled by a builder config."""

    if builder is None:
        return set()
    return {builder.stage_tool, builder.finalize_tool}


def render_builder_prompt_snippet(builder: DomainPackExtractionBuilder) -> str:
    """Render compact locked prompt instructions from typed builder metadata."""

    validator_targets = list(builder.object_graph.validator_targets)
    target_summary = "; ".join(
        f"{target.object_type}.{target.field_path}"
        for target in validator_targets
    )
    stage_required = [
        name
        for name, field in builder.fields.items()
        if field.required
    ]
    finalize_required = [
        name
        for name, field in builder.finalize_fields.items()
        if field.required
    ]
    hints = [
        f"{name}: {field.hint}"
        for name, field in builder.fields.items()
        if field.hint
    ][:8]
    reason_codes = ", ".join(builder.allowed_exclusion_reason_codes)
    lines = [
        "## Builder Tool Extraction Contract",
        (
            f"- Builder mode is enabled. Do not hand-author final `curatable_objects[]`; "
            f"the backend builds the curation envelope from `{builder.stage_tool}` "
            f"and `{builder.finalize_tool}` tool state."
        ),
        (
            f"- Call `{builder.stage_tool}` once per retained "
            f"{builder.retained_unit} finding after verified `record_evidence`."
        ),
        (
            f"- Call `{builder.finalize_tool}` exactly once when there are no more "
            "retained findings to stage."
        ),
        (
            "- Final response after successful finalization is only a small "
            f"`{builder.model_final_ack_schema}` acknowledgment."
        ),
        (
            f"- Stage required fields: {', '.join(stage_required) if stage_required else 'none'}."
        ),
        (
            "- Finalize required fields: "
            f"{', '.join(finalize_required) if finalize_required else 'none'}."
        ),
        (
            f"- Finalized object graph: {', '.join(builder.object_graph.required_objects)}; "
            f"validator targets {target_summary}."
        ),
    ]
    if hints:
        lines.append("- Stage field hints: " + "; ".join(hints) + ".")
    if reason_codes:
        lines.append(f"- Allowed exclusion reason codes: {reason_codes}.")
    return "\n".join(lines)


def builder_contract_payload(
    builder: DomainPackExtractionBuilder,
    *,
    domain_pack_id: str,
    domain_pack_version: str | None = None,
    detail_level: str = "summary",
) -> dict[str, Any]:
    """Return deterministic get_agent_contract(topic=builder_tools) payload."""

    stage_fields = _field_contracts(builder.fields, detail_level=detail_level)
    finalize_fields = _field_contracts(
        builder.finalize_fields,
        detail_level=detail_level,
    )
    payload: dict[str, Any] = {
        "domain_pack_id": domain_pack_id,
        "domain_pack_version": domain_pack_version,
        "enabled": builder.enabled,
        "stage_tool": builder.stage_tool,
        "finalize_tool": builder.finalize_tool,
        "retained_unit": builder.retained_unit,
        "per_retained_finding": builder.per_retained_finding,
        "model_final_ack_schema": builder.model_final_ack_schema,
        "curation_output_schema": builder.curation_output_schema,
        "stage_required_fields": [
            name for name, field in builder.fields.items() if field.required
        ],
        "finalize_required_fields": [
            name for name, field in builder.finalize_fields.items() if field.required
        ],
        "stage_fields": stage_fields,
        "finalize_fields": finalize_fields,
        "allowed_exclusion_reason_codes": list(builder.allowed_exclusion_reason_codes),
        "allowed_ambiguity_reason_codes": list(builder.allowed_ambiguity_reason_codes),
        "object_graph": {
            "required_objects": list(builder.object_graph.required_objects),
            "validator_targets": [
                target.model_dump(
                    mode="json",
                    exclude_none=True,
                    exclude_defaults=True,
                )
                for target in builder.object_graph.validator_targets
            ],
        },
        "examples": dict(builder.examples),
        "repair_messages": dict(builder.repair_messages),
    }
    if builder.object_graph.validator_target is not None:
        payload["object_graph"]["validator_target"] = (
            builder.object_graph.validator_target.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
            )
        )
    if detail_level == "detail":
        payload["object_graph"]["objects"] = [
            item.model_dump(mode="json", exclude_none=True)
            for item in builder.object_graph.objects
        ]
        payload["stage_description"] = builder.stage_description
        payload["finalize_description"] = builder.finalize_description
    return payload


def _field_contracts(
    fields: Mapping[str, Any],
    *,
    detail_level: str,
) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for name, field in fields.items():
        item = {
            "name": name,
            "json_type": field.json_type,
            "required": field.required,
            "collection": field.collection,
            "min_items": field.min_items,
            "maps_to": field.maps_to,
            "hint": field.hint,
            "examples": list(field.examples),
        }
        if detail_level == "detail":
            item["default"] = field.default
            item["repair_messages"] = dict(field.repair_messages)
        contracts.append(
            {key: value for key, value in item.items() if value is not None}
        )
    return contracts


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


__all__ = [
    "builder_contract_payload",
    "builder_tool_names",
    "extraction_builder_for_agent",
    "extraction_builder_for_domain_pack",
    "extraction_builder_from_metadata",
    "render_builder_prompt_snippet",
]
