"""Deterministic fingerprints for lossless Agent Studio authoring drafts."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from typing import Any, Mapping


def _canonical_value(value: Any) -> Any:
    """Use language-neutral ordering and IEEE-754 encoding for hash input."""

    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Draft fingerprints require finite numbers")
        if number == 0:
            number = 0.0
        return {"__authoring_float64__": struct.pack(">d", number).hex()}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _canonical_value(value[key])
            for key in sorted(value, key=lambda item: item.encode("utf-8"))
        }
    raise TypeError(f"Unsupported draft fingerprint value: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
    )


def _fingerprint(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _normalized_flow_definition(flow_definition: Any) -> dict[str, Any]:
    definition = flow_definition.model_dump(mode="json", exclude_none=True, exclude_unset=True)
    for node in definition.get("nodes", []):
        if "validation_attachments" in node:
            node["validation_attachments"] = sorted(
                node["validation_attachments"],
                key=lambda item: str(item.get("attachment_id", "")).encode("utf-8"),
            )
        if "validation_groups" in node:
            node["validation_groups"] = sorted(
                node["validation_groups"],
                key=lambda item: str(item.get("group_id", "")).encode("utf-8"),
            )
    definition["nodes"] = sorted(
        definition.get("nodes", []),
        key=lambda node: str(node.get("id", "")).encode("utf-8"),
    )
    definition["edges"] = sorted(
        definition.get("edges", []),
        key=lambda edge: str(edge.get("id", "")).encode("utf-8"),
    )
    return definition


def flow_draft_fingerprint(context: Any) -> str:
    """Hash the exact save-equivalent flow value and its baseline identity."""

    return _fingerprint(
        {
            "version": 1,
            "artifact_kind": "flow",
            "artifact_id": context.flow_id,
            "baseline_updated_at": context.flow_updated_at,
            "draft": {
                "name": context.flow_name or "",
                "description": context.flow_description or "",
                "definition": _normalized_flow_definition(context.flow_definition),
            },
        }
    )


def workshop_draft_fingerprint(workshop: Any) -> str:
    """Hash every authorable Workshop field and its saved baseline identity."""

    return _fingerprint(
        {
            "version": 1,
            "artifact_kind": "custom_agent",
            "artifact_id": workshop.custom_agent_id,
            "baseline_updated_at": workshop.custom_agent_updated_at,
            "draft": {
                "getting_started_mode": workshop.getting_started_mode or "scratch",
                "template_source": workshop.template_source,
                "clone_source_agent_id": workshop.clone_source_agent_id,
                "clone_source_updated_at": workshop.clone_source_updated_at,
                "name": workshop.draft_name or "",
                "description": workshop.draft_description or "",
                "icon": workshop.draft_icon or "",
                "visibility": workshop.draft_visibility or "private",
                "allowed_group_ids": sorted(
                    workshop.draft_allowed_group_ids or [], key=lambda item: item.encode("utf-8")
                ),
                "inherited_allowed_group_ids": sorted(
                    workshop.inherited_allowed_group_ids or [], key=lambda item: item.encode("utf-8")
                ),
                "prompt": workshop.prompt_draft or "",
                "group_prompt_overrides": workshop.group_prompt_overrides or {},
                "include_group_rules": bool(workshop.include_group_rules),
                "model_id": workshop.draft_model_id or "",
                "model_reasoning": workshop.draft_model_reasoning or "",
                "tool_ids": sorted(
                    workshop.draft_tool_ids or [], key=lambda item: item.encode("utf-8")
                ),
                "output_schema_key": workshop.draft_output_schema_key or "",
                "output_draft": workshop.draft_output,
            },
        }
    )


def workshop_authoring_metadata(workshop: Any) -> dict[str, Any]:
    """Return exact non-prompt authoring metadata for bounded provider access."""

    return {
        "getting_started_mode": workshop.getting_started_mode or "scratch",
        "template_source": workshop.template_source,
        "clone_source_agent_id": workshop.clone_source_agent_id,
        "clone_source_updated_at": workshop.clone_source_updated_at,
        "template_name": workshop.template_name,
        "custom_agent_id": workshop.custom_agent_id,
        "custom_agent_name": workshop.custom_agent_name,
        "custom_agent_updated_at": workshop.custom_agent_updated_at,
        "draft_name": workshop.draft_name or "",
        "draft_description": workshop.draft_description or "",
        "draft_icon": workshop.draft_icon or "",
        "draft_visibility": workshop.draft_visibility or "private",
        "draft_allowed_group_ids": workshop.draft_allowed_group_ids or [],
        "inherited_allowed_group_ids": workshop.inherited_allowed_group_ids or [],
        "include_group_rules": bool(workshop.include_group_rules),
        "selected_group_id": workshop.selected_group_id,
        "group_prompt_override_ids": sorted(
            (workshop.group_prompt_overrides or {}).keys(),
            key=lambda item: item.encode("utf-8"),
        ),
        "draft_is_dirty": bool(workshop.draft_is_dirty),
        "draft_fingerprint": workshop.draft_fingerprint,
        "draft_tool_ids": workshop.draft_tool_ids or [],
        "draft_model_id": workshop.draft_model_id,
        "draft_model_reasoning": workshop.draft_model_reasoning,
        "draft_output_schema_key": workshop.draft_output_schema_key,
        "draft_output": workshop.draft_output,
    }


def workshop_authoring_metadata_json(workshop: Any) -> str:
    """Serialize exact Workshop metadata for previewing or chunked retrieval."""

    return json.dumps(
        workshop_authoring_metadata(workshop),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
