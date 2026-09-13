"""Capture system-agent settings without constructing a model or tool client."""

from collections.abc import Mapping
from typing import Any
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from .frozen_flow import freeze_json, thaw_json


class FrozenSystemAgent(BaseModel):
    """Source settings, separate from the experiment's selected model route.

    Tool implementation, package schema, and current access are not frozen by
    this record. The execution boundary must check those dependencies as well.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    agent_key: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_temperature: float = Field(allow_inf_nan=False)
    model_reasoning: str | None
    tool_ids: tuple[str, ...]
    group_tool_policy: Mapping[str, Any]
    output_schema_key: str | None
    prompt_layer_manifest: Mapping[str, Any]
    output_schema_definition: Mapping[str, Any] | None = None
    curation_metadata: Mapping[str, Any] | None = None
    structured_finalization: Mapping[str, Any] | None = None

    @field_validator("agent_key")
    @classmethod
    def require_system_source(cls, value):
        if value.startswith("ca_"):
            raise ValueError("Custom agents require their saved execution receipt")
        return value

    @field_validator("group_tool_policy", "prompt_layer_manifest", "output_schema_definition",
                     "curation_metadata", "structured_finalization")
    @classmethod
    def freeze_objects(cls, value):
        return freeze_json(value)

    @field_validator("prompt_layer_manifest")
    @classmethod
    def require_source_only_prompt(cls, value, info):
        from src.lib.prompts.assembly import prompt_bundle_from_manifest

        bundle = prompt_bundle_from_manifest(thaw_json(value))
        if bundle.agent_id != info.data.get("agent_key"):
            raise ValueError("Frozen system prompt belongs to another agent")
        if any(layer.kind == "runtime_context" for layer in bundle.layers):
            raise ValueError("Document and request context cannot be frozen as source prompts")
        return value

    @field_serializer("group_tool_policy", "prompt_layer_manifest", "output_schema_definition",
                      "curation_metadata", "structured_finalization")
    def serialize_objects(self, value):
        return thaw_json(value)


def capture_system_agent(row, *, active_groups: tuple[str, ...]) -> FrozenSystemAgent:
    """Caller must supply a currently authorized system row.

    Read only named configuration fields: ORM state, connection URLs, provider
    credentials and per-paper context are deliberately never serialized.
    """
    from src.lib.prompts.assembly import build_agent_prompt_layers
    from src.lib.agent_studio import catalog_service
    from src.lib.config.agent_loader import get_agent_definition, get_agent_by_folder

    if row.visibility != "system":
        raise ValueError("System snapshot capture requires an authorized system agent")
    definition = get_agent_definition(row.agent_key) or get_agent_by_folder(row.agent_key)
    if definition is None:
        raise ValueError("System agent package is unavailable")
    curation_definition = (
        definition if catalog_service._launchable_curation_metadata_from_definition(definition) is not None
        else catalog_service._inherited_curation_definition_for_db_agent(row)
    )
    schema = catalog_service._resolve_output_schema(row.output_schema_key) if row.output_schema_key else None
    if row.output_schema_key and schema is None:
        raise ValueError("System output schema is unavailable")
    bundle = build_agent_prompt_layers(
        row.agent_key,
        group_id=list(active_groups) if row.group_rules_enabled else [],
    )
    return FrozenSystemAgent(
        agent_key=row.agent_key, model_id=row.model_id,
        model_temperature=row.model_temperature, model_reasoning=row.model_reasoning,
        tool_ids=tuple(row.tool_ids or ()), group_tool_policy=row.group_tool_policy or {},
        output_schema_key=row.output_schema_key, prompt_layer_manifest=bundle.to_manifest(),
        output_schema_definition=schema.model_json_schema() if schema is not None else None,
        curation_metadata=catalog_service._curation_metadata_from_definition(curation_definition),
        structured_finalization=getattr(definition, "structured_finalization", None) if schema is not None else None,
    )


def system_runtime_prompt(snapshot: FrozenSystemAgent, runtime_context: str):
    """Render frozen source layers plus fresh document context, with no lookup."""
    from src.lib.prompts.assembly import _bundle, _make_layer, prompt_bundle_from_manifest

    bundle = prompt_bundle_from_manifest(thaw_json(snapshot.prompt_layer_manifest))
    layers = list(bundle.layers)
    if runtime_context.strip():
        layers.append(_make_layer(
            layer_id=f"{bundle.agent_id}:runtime_context", kind="runtime_context",
            title="Runtime context", content=runtime_context, provenance="runtime_context",
            editable=False, locked=True, source_ref="request:runtime_context",
        ))
    return _bundle(bundle.agent_id, layers)


def authorized_system_snapshot_row(row, snapshot: FrozenSystemAgent, *, active_groups):
    """Use saved settings only after current group and tool-policy checks."""
    from sqlalchemy import select
    from src.lib.agent_studio.catalog_service import resolve_group_tool_policy
    from src.models.sql.database import SessionLocal
    from src.models.sql.tool_policy import ToolPolicy

    if row.visibility != "system" or row.agent_key != snapshot.agent_key:
        raise ValueError("Frozen system source does not match the authorized agent")
    selected = resolve_group_tool_policy(
        snapshot.tool_ids, thaw_json(snapshot.group_tool_policy), active_groups,
    ).tool_ids
    current = resolve_group_tool_policy(row.tool_ids or [], row.group_tool_policy or {}, active_groups).tool_ids
    if set(selected) - set(current):
        raise ValueError("A frozen system tool is no longer allowed for this curator")
    if selected:
        with SessionLocal() as db:
            policies = db.execute(select(ToolPolicy).where(ToolPolicy.tool_key.in_(selected))).scalars().all()
            if set(selected) - {policy.tool_key for policy in policies if policy.allow_execute}:
                raise ValueError("A frozen system tool is no longer executable")
    return SimpleNamespace(
        name=row.name, agent_key=row.agent_key, visibility="system",
        model_id=snapshot.model_id, model_temperature=snapshot.model_temperature,
        model_reasoning=snapshot.model_reasoning, tool_ids=list(snapshot.tool_ids),
        group_tool_policy=thaw_json(snapshot.group_tool_policy), output_schema_key=snapshot.output_schema_key,
    )
