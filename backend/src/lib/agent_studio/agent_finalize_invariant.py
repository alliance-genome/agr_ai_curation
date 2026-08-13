"""Agent output-schema versus builder-finalize-tool invariant checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class AgentFinalizeInvariantViolation:
    """One agent output/finalize configuration invariant violation."""

    agent_key: str
    reason: str
    detail: str

    @property
    def message(self) -> str:
        return f"{self.agent_key}: {self.detail}"


def _normalize_tool_ids(tool_ids: Sequence[str] | None) -> list[str]:
    normalized: list[str] = []
    for raw_tool_id in tool_ids or ():
        tool_id = str(raw_tool_id or "").strip()
        if tool_id and tool_id not in normalized:
            normalized.append(tool_id)
    return normalized


def _registry_builder_finalization_tool_names() -> frozenset[str]:
    from src.lib.openai_agents.streaming_tools import (
        builder_finalization_tool_names as registry_builder_finalization_tool_names,
    )

    return registry_builder_finalization_tool_names()


def _is_extractor_agent_category(category: str | None) -> bool:
    return str(category or "").strip().casefold() == "extraction"


def validate_agent_finalize_tool_invariant(
    *,
    agent_key: str,
    category: str | None,
    output_schema_key: str | None,
    tool_ids: Sequence[str] | None,
) -> list[AgentFinalizeInvariantViolation]:
    """Validate the envelope-output versus builder-finalize-tool contract.

    Validation agents use an envelope ``output_schema`` and no builder finalize
    tool. Builder/extractor agents use no ``output_schema`` because the backend
    materializes their envelope from staged builder state, and they must expose
    one registry-declared builder finalize tool.
    """

    normalized_agent_key = str(agent_key or "").strip() or "<unknown-agent>"
    normalized_tool_ids = _normalize_tool_ids(tool_ids)
    output_schema = str(output_schema_key or "").strip()
    finalization_tool_names = _registry_builder_finalization_tool_names()
    builder_finalize_tools = sorted(set(normalized_tool_ids) & set(finalization_tool_names))
    has_finalize_tool = bool(builder_finalize_tools)
    is_extractor_agent = _is_extractor_agent_category(category)

    violations: list[AgentFinalizeInvariantViolation] = []
    if is_extractor_agent and output_schema:
        violations.append(
            AgentFinalizeInvariantViolation(
                agent_key=normalized_agent_key,
                reason="builder_output_schema_forbidden",
                detail=(
                    "builder/extractor agent declares output_schema "
                    f"'{output_schema}', but builder-materializer agents must set "
                    "output_schema to null because backend builder finalization "
                    "materializes the canonical envelope"
                ),
            )
        )

    if output_schema and has_finalize_tool:
        violations.append(
            AgentFinalizeInvariantViolation(
                agent_key=normalized_agent_key,
                reason="output_schema_with_finalize_tool",
                detail=(
                    "declares both output_schema "
                    f"'{output_schema}' and builder finalize tool(s): "
                    f"{', '.join(builder_finalize_tools)}; envelope output schemas "
                    "and builder finalization are mutually exclusive"
                ),
            )
        )

    if is_extractor_agent and not has_finalize_tool:
        expected_tools = ", ".join(sorted(finalization_tool_names))
        violations.append(
            AgentFinalizeInvariantViolation(
                agent_key=normalized_agent_key,
                reason="extractor_finalize_tool_missing",
                detail=(
                    "extractor agent is missing a builder finalize tool; expected "
                    f"one of the registry-derived builder finalization tools: {expected_tools}"
                ),
            )
        )

    return violations
