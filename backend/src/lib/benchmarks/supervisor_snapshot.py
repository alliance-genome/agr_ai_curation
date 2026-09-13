"""Freeze the normal flow supervisor's generated prompt and settings."""

from types import SimpleNamespace
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from .frozen_flow import FrozenBenchmarkFlow, thaw_json
from src.models.sql.curation_flow import CurationFlow


class FrozenFlowSupervisor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    model: str = Field(min_length=1)
    temperature: float | None = Field(allow_inf_nan=False)
    reasoning: str | None
    parallel_tool_calls: bool
    requires_document: bool
    available_tools: tuple[str, ...]
    instructions: str
    instructions_with_document: str


def capture_flow_supervisor(frozen: FrozenBenchmarkFlow, curator) -> FrozenFlowSupervisor:
    from src.lib.flows import executor
    from src.lib.openai_agents.config import get_agent_config, get_flow_supervisor_parallel_tool_calls_enabled

    flow = cast(CurationFlow, SimpleNamespace(
        id=frozen.source_id, name=frozen.title, flow_definition=thaw_json(frozen.definition),
    ))
    counts = executor._count_agent_ids(flow)
    names = []
    for step, node in enumerate(executor._get_ordered_executable_nodes(flow), 1):
        key = node["data"]["agent_id"]
        segment = executor._tool_safe_agent_id(key)
        names.append(f"ask_{segment}_step{step}_specialist" if counts.get(key, 0) > 1 else f"ask_{segment}_specialist")
    config = get_agent_config("supervisor")
    return FrozenFlowSupervisor(
        model=config.model, temperature=config.temperature, reasoning=config.reasoning,
        parallel_tool_calls=get_flow_supervisor_parallel_tool_calls_enabled(),
        requires_document=executor.flow_requires_document(
            flow, db_user_id=curator.db_user_id, active_groups=list(curator.active_groups),
        ),
        available_tools=tuple(names),
        instructions=executor.build_supervisor_instructions(flow, available_tools=set(names)),
        instructions_with_document=executor.build_supervisor_instructions(
            flow, has_document=True, available_tools=set(names),
        ),
    )
