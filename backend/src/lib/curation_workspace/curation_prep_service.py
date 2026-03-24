"""Service layer for invoking the curation prep agent and persisting raw output."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from agents import Runner, RunConfig
from sqlalchemy.orm import Session

from src.lib.curation_workspace.extraction_results import persist_extraction_result
from src.lib.openai_agents.agents.curation_prep_agent import (
    CURATION_PREP_AGENT_ID,
    create_curation_prep_agent,
    get_curation_prep_agent_definition,
)
from src.schemas.curation_prep import (
    CurationPrepAgentInput,
    CurationPrepAgentOutput,
    CurationPrepRunMetadata,
    CurationPrepTokenUsage,
)
from src.schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)


@dataclass(frozen=True)
class CurationPrepPersistenceContext:
    """Optional persistence metadata overrides for prep-agent execution."""

    document_id: str | None = None
    source_kind: CurationExtractionSourceKind | None = None
    origin_session_id: str | None = None
    trace_id: str | None = None
    flow_run_id: str | None = None
    user_id: str | None = None
    conversation_summary: str | None = None


async def run_curation_prep(
    agent_input: CurationPrepAgentInput,
    *,
    db: Session | None = None,
    persistence_context: CurationPrepPersistenceContext | None = None,
) -> CurationPrepAgentOutput:
    """Run the curation prep agent, persist its raw output, and return the enriched result."""

    persistence_context = persistence_context or CurationPrepPersistenceContext()
    primary_extraction_result = _resolve_primary_extraction_result(agent_input.extraction_results)
    document_id = _resolve_document_id(agent_input.extraction_results, persistence_context)
    adapter_key = _resolve_required_adapter_key(agent_input)
    agent_definition = get_curation_prep_agent_definition()
    agent = create_curation_prep_agent()

    run_config = RunConfig(
        workflow_name="Curation prep",
        group_id=_resolve_group_id(primary_extraction_result, persistence_context),
        trace_metadata=_build_trace_metadata(agent_input, persistence_context),
    )

    result = await Runner.run(
        agent,
        _build_agent_input_message(agent_input),
        run_config=run_config,
    )

    raw_output = _coerce_final_output(result.final_output)
    final_output = _apply_runtime_run_metadata(
        raw_output,
        model_name=agent_definition.model_config.model,
        result=result,
    )

    persist_extraction_result(
        _build_persistence_request(
            agent_input,
            adapter_key=adapter_key,
            result=result,
            raw_output=raw_output,
            final_output=final_output,
            persistence_context=persistence_context,
            document_id=document_id,
            primary_extraction_result=primary_extraction_result,
        ),
        db=db,
    )

    return final_output


def _build_agent_input_message(agent_input: CurationPrepAgentInput) -> str:
    """Serialize the prep input into a single model-facing prompt."""

    payload = json.dumps(agent_input.model_dump(mode="json"), indent=2, ensure_ascii=True)
    return (
        "Produce a CurationPrepAgentOutput for the following "
        "CurationPrepAgentInput JSON payload.\n\n"
        f"{payload}"
    )


def _coerce_final_output(final_output: Any) -> CurationPrepAgentOutput:
    """Validate the runner's final output as the strict prep output contract."""

    if final_output is None:
        raise RuntimeError("Curation prep agent did not produce a structured output.")

    if isinstance(final_output, CurationPrepAgentOutput):
        return final_output

    return CurationPrepAgentOutput.model_validate(final_output)


def _apply_runtime_run_metadata(
    raw_output: CurationPrepAgentOutput,
    *,
    model_name: str,
    result: Any,
) -> CurationPrepAgentOutput:
    """Replace placeholder run metadata with actual runtime token accounting."""

    updated_run_metadata = CurationPrepRunMetadata(
        model_name=model_name,
        token_usage=_extract_token_usage(result),
        processing_notes=list(raw_output.run_metadata.processing_notes),
        warnings=list(raw_output.run_metadata.warnings),
    )
    return raw_output.model_copy(update={"run_metadata": updated_run_metadata})


def _extract_token_usage(result: Any) -> CurationPrepTokenUsage:
    """Read aggregate token usage from the Agents SDK run context."""

    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
    total_tokens = max(total_tokens, input_tokens + output_tokens)

    return CurationPrepTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _build_persistence_request(
    agent_input: CurationPrepAgentInput,
    *,
    adapter_key: str,
    result: Any,
    raw_output: CurationPrepAgentOutput,
    final_output: CurationPrepAgentOutput,
    persistence_context: CurationPrepPersistenceContext,
    document_id: str,
    primary_extraction_result: CurationExtractionResultRecord,
) -> CurationExtractionPersistenceRequest:
    """Translate prep execution into the existing extraction-result persistence contract."""

    return CurationExtractionPersistenceRequest(
        document_id=document_id,
        agent_key=CURATION_PREP_AGENT_ID,
        source_kind=persistence_context.source_kind or primary_extraction_result.source_kind,
        adapter_key=adapter_key,
        profile_key=_resolve_profile_key(agent_input),
        domain_key=_resolve_domain_key(agent_input),
        origin_session_id=(
            persistence_context.origin_session_id
            or primary_extraction_result.origin_session_id
        ),
        trace_id=persistence_context.trace_id or primary_extraction_result.trace_id,
        flow_run_id=persistence_context.flow_run_id or primary_extraction_result.flow_run_id,
        user_id=persistence_context.user_id or primary_extraction_result.user_id,
        candidate_count=len(raw_output.candidates),
        conversation_summary=_resolve_conversation_summary(agent_input, persistence_context),
        payload_json=raw_output.model_dump(mode="json"),
        metadata={
            "final_run_metadata": final_output.run_metadata.model_dump(mode="json"),
            "raw_response_ids": _extract_raw_response_ids(getattr(result, "raw_responses", None)),
            "adapter_metadata": [
                metadata.model_dump(mode="json")
                for metadata in agent_input.adapter_metadata
            ],
            "scope_confirmed": agent_input.scope_confirmation.confirmed,
            "scope_adapter_keys": list(agent_input.scope_confirmation.adapter_keys),
            "scope_profile_keys": list(agent_input.scope_confirmation.profile_keys),
            "scope_domain_keys": list(agent_input.scope_confirmation.domain_keys),
        },
    )


def _resolve_primary_extraction_result(
    extraction_results: Sequence[CurationExtractionResultRecord],
) -> CurationExtractionResultRecord:
    """Return the first extraction result while making the non-empty contract explicit."""

    if not extraction_results:
        raise ValueError("Curation prep requires at least one extraction result.")

    return extraction_results[0]


def _resolve_document_id(
    extraction_results: Sequence[CurationExtractionResultRecord],
    persistence_context: CurationPrepPersistenceContext,
) -> str:
    """Resolve the single document identifier for the prep execution."""
    document_ids = _unique_non_empty(result.document_id for result in extraction_results)
    if len(document_ids) != 1:
        raise ValueError(
            "Curation prep persistence requires extraction_results for exactly one document."
        )

    resolved_document_id = document_ids[0]
    if (
        persistence_context.document_id is not None
        and persistence_context.document_id != resolved_document_id
    ):
        raise ValueError(
            "Curation prep persistence_context.document_id must match the single "
            "document_id present in extraction_results."
        )

    return resolved_document_id


def _resolve_adapter_key(agent_input: CurationPrepAgentInput) -> str | None:
    return _resolve_single_value(
        [
            *agent_input.scope_confirmation.adapter_keys,
            *(record.adapter_key for record in agent_input.extraction_results if record.adapter_key),
        ]
    )


def _resolve_required_adapter_key(agent_input: CurationPrepAgentInput) -> str:
    """Require prep input to resolve to a single adapter owner before execution."""

    adapter_key = _resolve_adapter_key(agent_input)
    if adapter_key is None:
        raise ValueError("Curation prep requires extraction results for exactly one adapter key.")
    return adapter_key


def _resolve_profile_key(agent_input: CurationPrepAgentInput) -> str | None:
    return _resolve_single_value(
        [
            *agent_input.scope_confirmation.profile_keys,
            *(metadata.profile_key for metadata in agent_input.adapter_metadata if metadata.profile_key),
            *(record.profile_key for record in agent_input.extraction_results if record.profile_key),
        ]
    )


def _resolve_domain_key(agent_input: CurationPrepAgentInput) -> str | None:
    return _resolve_single_value(
        [
            *agent_input.scope_confirmation.domain_keys,
            *(record.domain_key for record in agent_input.extraction_results if record.domain_key),
        ]
    )


def _resolve_group_id(
    primary_extraction_result: CurationExtractionResultRecord,
    persistence_context: CurationPrepPersistenceContext,
) -> str | None:
    """Choose a stable tracing group id when one is available."""

    if persistence_context.origin_session_id:
        return persistence_context.origin_session_id
    if persistence_context.flow_run_id:
        return persistence_context.flow_run_id

    return primary_extraction_result.origin_session_id or primary_extraction_result.flow_run_id


def _build_trace_metadata(
    agent_input: CurationPrepAgentInput,
    persistence_context: CurationPrepPersistenceContext,
) -> dict[str, str]:
    """Assemble lightweight run metadata for SDK tracing."""

    metadata = {
        "agent_id": CURATION_PREP_AGENT_ID,
        "scope_confirmed": str(agent_input.scope_confirmation.confirmed).lower(),
        "adapter_count": str(len(agent_input.adapter_metadata)),
        "extraction_result_count": str(len(agent_input.extraction_results)),
        "evidence_record_count": str(len(agent_input.evidence_records)),
    }

    if persistence_context.trace_id:
        metadata["external_trace_id"] = persistence_context.trace_id
    if persistence_context.flow_run_id:
        metadata["flow_run_id"] = persistence_context.flow_run_id

    return metadata


def _resolve_conversation_summary(
    agent_input: CurationPrepAgentInput,
    persistence_context: CurationPrepPersistenceContext,
) -> str | None:
    """Prefer explicit summaries and fall back to a compact message digest."""

    if persistence_context.conversation_summary is not None:
        summary = persistence_context.conversation_summary.strip()
        return summary or None

    summaries = _unique_non_empty(
        record.conversation_summary for record in agent_input.extraction_results
    )
    if summaries:
        return " ".join(summaries)

    if not agent_input.conversation_history:
        return None

    message_digest = " | ".join(
        f"{message.role.value}: {message.content}"
        for message in agent_input.conversation_history[-3:]
    )
    return message_digest[:2000] or None


def _extract_raw_response_ids(raw_responses: Any) -> list[str]:
    """Collect stable response ids from raw model responses when available."""

    if not raw_responses:
        return []

    response_ids: list[str] = []
    for raw_response in raw_responses:
        response_id = getattr(raw_response, "response_id", None) or getattr(raw_response, "id", None)
        response_text = str(response_id or "").strip()
        if response_text:
            response_ids.append(response_text)

    return response_ids


def _resolve_single_value(values: Iterable[Optional[str]]) -> str | None:
    """Return the only distinct non-empty value, or None when scope is mixed."""

    distinct_values = _unique_non_empty(values)
    if len(distinct_values) == 1:
        return distinct_values[0]
    return None


def _unique_non_empty(values: Iterable[Optional[str]]) -> list[str]:
    """Deduplicate non-empty string values while preserving their first-seen order."""

    distinct_values: list[str] = []
    seen_values: set[str] = set()

    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen_values:
            continue
        distinct_values.append(normalized)
        seen_values.add(normalized)

    return distinct_values


__all__ = [
    "CurationPrepPersistenceContext",
    "run_curation_prep",
]
