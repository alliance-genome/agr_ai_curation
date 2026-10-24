"""Freeze the normal flow supervisor's generated prompt and settings."""

from types import SimpleNamespace
from typing import cast, Literal, Any
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, field_serializer

from .frozen_flow import FrozenBenchmarkFlow, thaw_json, freeze_json
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
    review_runtime_version: Literal["supervisor-review/v2"]
    review_runtime_contract: Mapping[str, Any]
    review_policy_layers: tuple[Mapping[str, Any], ...]

    @field_validator("review_runtime_contract")
    @classmethod
    def freeze_runtime(cls, value):
        return freeze_json(value)

    @field_validator("review_policy_layers")
    @classmethod
    def freeze_policy(cls, value):
        return tuple(freeze_json(layer) for layer in value)

    @field_serializer("review_runtime_contract", "review_policy_layers")
    def serialize_review(self, value):
        return thaw_json(value)


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
    from src.lib.prompts.assembly import build_agent_prompt_layers
    policy = build_agent_prompt_layers("supervisor", group_id=list(curator.active_groups))
    return FrozenFlowSupervisor(
        model=config.model, temperature=config.temperature, reasoning=config.reasoning,
        review_runtime_version="supervisor-review/v2",
        review_runtime_contract=review_runtime_contract(),
        review_policy_layers=tuple(layer.to_manifest() for layer in policy.layers
            if layer.kind in {"group_rules", "curator_overlay"}),
        parallel_tool_calls=get_flow_supervisor_parallel_tool_calls_enabled(),
        requires_document=executor.flow_requires_document(
            flow, db_user_id=curator.db_user_id, active_groups=list(curator.active_groups),
        ),
        available_tools=tuple(names),
        instructions=executor.build_supervisor_instructions(
            flow, available_tools=set(names),
            db_user_id=curator.db_user_id, active_groups=list(curator.active_groups),
        ),
        instructions_with_document=executor.build_supervisor_instructions(
            flow, has_document=True, available_tools=set(names),
            db_user_id=curator.db_user_id, active_groups=list(curator.active_groups),
        ),
    )


def review_runtime_contract():
    from src.lib.validation_review.protocol import REVIEW_PROTOCOL, RECONSIDERATION_PROTOCOL, TARGETED_EXTRACTION_PROTOCOL
    from src.lib.validation_review.policy import ReviewLimits
    from src.lib.validation_review.tools import runtime_tool_contract
    return {"review_protocol": REVIEW_PROTOCOL, "validator_protocol": RECONSIDERATION_PROTOCOL,
            "extractor_protocol": TARGETED_EXTRACTION_PROTOCOL, "tools": runtime_tool_contract(),
            "limits": ReviewLimits.configured().snapshot()}


def require_current_review_runtime(snapshot):
    if thaw_json(snapshot.review_runtime_contract) != review_runtime_contract():
        raise ValueError("Frozen supervisor review protocol, tools or limits changed; explicitly repin this benchmark source")
