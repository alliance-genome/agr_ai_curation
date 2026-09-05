"""Capture executable configuration once; render saved prompts without live reads."""

from copy import deepcopy
from dataclasses import replace
import hashlib
from typing import Iterable

from src.lib.prompts.assembly import (
    CORE_STATIC_PROMPT,
    PromptLayer,
    PromptLayerBundle,
    _build_core_generated_content,
    _build_group_rules_layer,
    _bundle,
    _make_layer,
    prompt_bundle_from_manifest,
)
from src.schemas.agent_execution_revision import (
    AgentExecutionSnapshot,
    AgentOutputContract,
)


def capture_execution_snapshot(
    db, agent, output: AgentOutputContract, *, active_group_ids: list[str] | None = None,
) -> AgentExecutionSnapshot:
    """Resolve template-owned inputs at curator save time, not when a pin runs."""
    from src.lib.agent_studio import catalog_service
    from src.lib.agent_studio.custom_agent_service import (
        _system_managed_tool_ids,
        custom_main_prompt_for_parent,
    )
    from src.lib.config.agent_loader import (
        AgentDefinition,
        CurationConfig,
        get_agent_by_folder,
        get_agent_definition,
    )
    from src.lib.config.groups_loader import get_valid_group_ids
    from src.lib.prompts.cache import get_all_active_prompts

    parent = agent.template_source or agent.group_rules_component
    definition = None
    if parent:
        definition = get_agent_definition(parent) or get_agent_by_folder(parent)
        if definition is None:
            raise ValueError("Cannot snapshot an unavailable template")
    instructions = custom_main_prompt_for_parent(parent, agent.instructions)
    tools = list(agent.tool_ids or [])
    from src.lib.agent_studio.domain_output_contract import require_no_output_without_builder_tools

    require_no_output_without_builder_tools(output, tools)
    curation_definition = catalog_service._inherited_curation_definition_for_db_agent(
        agent
    )
    curation = catalog_service._curation_metadata_from_definition(curation_definition)
    contract_definition = definition
    inherited_groups = list(agent.inherited_allowed_group_ids)
    if output.domain_extraction_ref is not None:
        from src.lib.agent_studio.domain_output_contract import (
            resolve_domain_extraction_definition,
            validate_domain_extraction_selection,
        )

        contract_definition = resolve_domain_extraction_definition(output.domain_extraction_ref)
        validate_domain_extraction_selection(
            contract_definition, tool_ids=tools,
            allowed_group_ids=list(agent.allowed_group_ids),
            group_tool_policy=agent.group_tool_policy or {},
            active_group_ids=active_group_ids,
        )
        curation = catalog_service._curation_metadata_from_definition(contract_definition)
        selected_groups = contract_definition.access.allowed_group_ids
        if selected_groups:
            inherited_groups = (
                [group for group in inherited_groups if group in selected_groups]
                if inherited_groups else list(selected_groups)
            )
    if output.output_state == "none":
        curation = None
    elif output.output_mode in {"profile_bound_generic", "unprofiled_generic"}:
        curation = {"adapter_key": "generic", "domain_pack_id": "generic", "launchable": True}
    finalization = None
    if (
        contract_definition is not None
        and output.output_mode == "domain"
        and output.output_schema_key == contract_definition.output_schema
    ):
        finalization = deepcopy(contract_definition.structured_finalization)

    # Generate the locked contract from this custom agent's selected tools/output,
    # not a parent's broader tool set or a packaged schema the curator cleared.
    effective = (
        replace(
            contract_definition,
            tools=tools,
            output_schema=output.output_schema_key,
            structured_finalization=finalization,
            curation=CurationConfig(**curation) if curation is not None else CurationConfig(),
        )
        if contract_definition
        else AgentDefinition(
            folder_name=agent.agent_key,
            agent_id=agent.agent_key,
            name=agent.name,
            tools=tools,
            output_schema=output.output_schema_key,
            curation=CurationConfig(**curation) if curation is not None else CurationConfig(),
        )
    )
    layers = [
        _make_layer(
            layer_id=f"{agent.agent_key}:core_static",
            kind="core_static",
            title="Platform runtime contract",
            content=CORE_STATIC_PROMPT,
            provenance="backend_static",
            editable=False,
            locked=True,
            source_ref="src.lib.prompts.assembly:CORE_STATIC_PROMPT",
        )
    ]
    generated = _build_core_generated_content(effective)
    if generated:
        layers.append(
            _make_layer(
                layer_id=f"{agent.agent_key}:core_generated",
                kind="core_generated",
                title="Saved execution contract",
                content=generated,
                provenance="custom_agent:execution_snapshot",
                editable=False,
                locked=True,
                source_ref=f"agents:{agent.id}:saved_configuration",
            )
        )
    layers.append(
        _make_layer(
            layer_id=f"{agent.agent_key}:base_prompt",
            kind="base_prompt",
            title="Custom agent main prompt",
            content=instructions,
            provenance="custom_agent:execution_snapshot",
            editable=True,
            locked=False,
            source_ref=f"agents:{agent.id}:instructions",
        )
    )
    manifest = _bundle(agent.agent_key, layers).to_manifest()
    group_layers = {}
    overrides = dict(agent.group_prompt_overrides or {})
    if agent.group_rules_enabled:
        cache = get_all_active_prompts() if parent else {}
        for group_id in sorted(set(get_valid_group_ids()) | set(overrides)):
            layer = _build_group_rules_layer(
                cache,
                canonical_agent_id=parent or agent.agent_key,
                group_ids=(group_id,),
                overrides=overrides,
            )
            if layer is not None:
                group_layers[group_id] = _bundle(agent.agent_key, [layer]).to_manifest()
    return AgentExecutionSnapshot(
        model_id=agent.model_id,
        model_temperature=agent.model_temperature,
        model_reasoning=agent.model_reasoning,
        instructions=instructions,
        instructions_hash="sha256:"
        + hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        prompt_layer_manifest=manifest,
        group_prompt_layers=group_layers,
        tool_ids=tools,
        system_managed_tool_ids=_system_managed_tool_ids(db, tools) if tools else [],
        group_tool_policy=deepcopy(agent.group_tool_policy or {}),
        allowed_group_ids=list(agent.allowed_group_ids),
        inherited_allowed_group_ids=inherited_groups,
        group_rules_enabled=agent.group_rules_enabled,
        group_rules_component=agent.group_rules_component,
        group_prompt_overrides=overrides,
        template_source=agent.template_source,
        output_contract=output,
        curation=curation,
        structured_finalization=finalization,
    )


def saved_runtime_prompt_bundle(
    snapshot: AgentExecutionSnapshot,
    *,
    active_groups: Iterable[str] = (),
    runtime_context: str = "",
) -> PromptLayerBundle:
    """Combine saved layers with explicitly per-run context; no template lookup."""
    saved = AgentExecutionSnapshot.model_validate(snapshot.model_dump(mode="json"))
    bundle = prompt_bundle_from_manifest(saved.prompt_layer_manifest)
    layers: list[PromptLayer] = list(bundle.layers)
    if saved.group_rules_enabled:
        for group in sorted({str(value).strip().upper() for value in active_groups}):
            manifest = saved.group_prompt_layers.get(group)
            if manifest is not None:
                layers.extend(prompt_bundle_from_manifest(manifest).layers)
    if runtime_context.strip():
        layers.append(
            _make_layer(
                layer_id=f"{bundle.agent_id}:runtime_context",
                kind="runtime_context",
                title="Runtime context",
                content=runtime_context,
                provenance="runtime_context",
                editable=False,
                locked=True,
                source_ref="request:runtime_context",
            )
        )
    return _bundle(bundle.agent_id, layers)
