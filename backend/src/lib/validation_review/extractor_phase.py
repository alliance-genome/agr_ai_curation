"""A narrow phase of the saved extractor, separate from full extraction capture."""
import json
from copy import copy

from pydantic_core import to_jsonable_python

from src.lib.openai_agents.model_settings_contract import ModelSettingsContractError, model_settings_contract
from src.lib.validation_history import store
from src.schemas.validation_followup import TargetedFollowupResult
from .protocol import TARGETED_EXTRACTION_PROTOCOL
from .specialist_context import create_history_reader


# Authenticated ownership, group access and revision authorization are supplied
# afresh by the adapter. These are context/model selections from the original
# configured invocation, never arguments accepted by the supervisor's tool.
_RECONSTRUCTION_KEYS = frozenset({
    "document_name", "hierarchy", "sections", "abstract", "additional_runtime_context",
    "model_id_override", "model_provider_override", "model_reasoning_override",
    "model_temperature_override", "cost_node_id",
})


def reconstruction_context(kwargs):
    return to_jsonable_python({key: value for key, value in kwargs.items() if key in _RECONSTRUCTION_KEYS})


def source_tools(agent):
    surface = getattr(agent, "tool_surface", None)
    return surface.source_tools if surface is not None and agent.tools is surface.tools else agent.tools


def contract(agent, *, max_turns):
    """Pin the executable extractor contract before its initial invocation."""
    from src.lib.observability.cost_context import get_agent_cost_identity
    if not isinstance(agent.instructions, str):
        raise store.HistoryConflict("Extractor instructions must be resolved before admission")
    try:
        settings = model_settings_contract(agent.model_settings)
    except ModelSettingsContractError as exc:
        raise store.HistoryConflict("Extractor settings have an unidentified retry policy") from exc
    return to_jsonable_python({
        "identity": get_agent_cost_identity(agent),
        "execution_receipt": getattr(agent, "execution_receipt", None),
        "model": agent.model if isinstance(agent.model, str) else getattr(agent.model, "model", None),
        "settings": settings, "instructions": agent.instructions,
        "max_turns": max_turns,
        "reconstruction_context": getattr(agent, "review_reconstruction_context", {}),
        "tools": [{"name": tool.name, "description": tool.description,
                   "parameters": tool.params_json_schema} for tool in source_tools(agent)],
        "followup_protocol": TARGETED_EXTRACTION_PROTOCOL,
    })


def create_phase(agent, *, scope, artifact_id, packet, packet_bytes, evidence_records):
    """Retain only configured paper tools; no staging, full finalizer or dispatch."""
    from src.lib.packages.tool_roles import tool_metadata_by_name
    from src.lib.openai_agents.tool_surface import apply_tool_surface
    from src.lib.observability.cost_context import get_agent_cost_identity, attach_agent_cost_identity

    metadata = tool_metadata_by_name()
    paper_tools = [tool for tool in source_tools(agent)
                   if metadata.get(tool.name, {}).get("paper_source") is True
                   and not metadata.get(tool.name, {}).get("builder_run_state")
                   and not metadata.get(tool.name, {}).get("builder_finalization")]
    from src.lib.openai_agents.evidence_summary import build_record_evidence_summary_record
    from src.lib.openai_agents.streaming_tools import _append_live_evidence_record

    def capture_evidence(tool):
        # The nonstreaming SDK runner does not perform streaming_tools' output
        # collection. Retain the same verified tool result here, before return.
        wrapped = copy(tool)
        async def invoke(context, arguments):
            result = await tool.on_invoke_tool(context, arguments)
            record = build_record_evidence_summary_record(tool_name=tool.name,
                tool_input=arguments, tool_output=result)
            if record is not None:
                _append_live_evidence_record(evidence_records, record)
            return result
        wrapped.on_invoke_tool = invoke
        return wrapped

    paper_tools = [capture_evidence(tool) for tool in paper_tools]
    history_reader = create_history_reader(scope, artifact_id,
        records=packet["history"]["records"], packet_bytes=packet_bytes)
    instructions = (agent.instructions + "\n\n"
        "This invocation uses the application-owned targeted follow-up phase below. "
        "Its scoped task and result contract replace initial full-extraction staging/finalization "
        "instructions. Retain your domain-specific extraction policy.\n"
        + TARGETED_EXTRACTION_PROTOCOL)
    phase = agent.clone(name=agent.name + " Targeted Follow-up", instructions=instructions,
        tools=[*paper_tools, history_reader], handoffs=[], output_type=TargetedFollowupResult,
        output_guardrails=[], tool_use_behavior="run_llm_again")
    # A full extractor may force its builder finalizer. This phase uses the
    # structured proposal contract and cannot call that full-extraction tool.
    phase.model_settings = agent.model_settings.resolve(type(agent.model_settings)(tool_choice="auto"))
    phase.tool_surface_base_instructions = instructions
    phase.tool_surface_runtime = "extractor_followup"
    attach_agent_cost_identity(phase, get_agent_cost_identity(agent))
    apply_tool_surface(phase, runtime="extractor_followup")
    from .phase_observability import record_phase
    record_phase(phase, phase="extractor_followup", original_instructions=agent.instructions,
        metadata={"artifact_id": artifact_id})
    return phase


async def invoke(agent, *, scope, artifact_id, packet, packet_bytes, max_turns, evidence_records):
    """Caller owns the admitted budget, lease and verified paper-tool context."""
    from src.lib.openai_agents.runner import run_agent_with_owned_openai_resources
    phase = create_phase(agent, scope=scope, artifact_id=artifact_id,
                         packet=packet, packet_bytes=packet_bytes, evidence_records=evidence_records)
    from .phase_observability import emit_phase
    emit_phase(phase)
    result = await run_agent_with_owned_openai_resources(
        phase, input_value=json.dumps(packet, ensure_ascii=True, sort_keys=True), max_turns=max_turns)
    return TargetedFollowupResult.model_validate(result.final_output)
