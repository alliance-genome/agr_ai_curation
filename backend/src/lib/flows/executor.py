"""Flow execution engine for curation flows.

Provides functions to execute user-defined agent workflows with
streaming tool wrappers for full audit visibility.

Key concepts:
- Streaming tools: Uses _create_streaming_tool() to capture internal agent tool calls
- Flow supervisor: A custom supervisor configured for the specific flow
- Streaming execution: Delegates to run_agent_streamed() for rich audit events

Architecture:
    execute_flow() creates a flow supervisor with streaming-wrapped tools, then
    delegates to run_agent_streamed() to get the same rich audit events as
    regular chat (SUPERVISOR_START, AGENT_GENERATING, CREW_START, etc.)
    plus Langfuse tracing, prompt logging, and document metadata.

    Unlike the old as_tool() approach, streaming tools use run_specialist_with_events()
    to capture internal tool calls (read_section, search_document, etc.) and emit
    events for the audit panel and PDF highlighting.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict, List, Mapping, Optional, Set
from uuid import uuid4

from agents import Agent, Runner, function_tool
from pydantic import ValidationError

from src.lib.context import (
    get_current_trace_id,
    reset_current_output_filename_stem,
    set_current_output_filename_stem,
)
from src.lib.curation_workspace import (
    CurationPrepPersistenceContext,
    ExtractionEnvelopeCandidate,
    build_extraction_envelope_candidate_with_evidence,
    persist_extraction_results,
    run_curation_prep,
)
from src.lib.curation_workspace.adapter_registry import resolve_curation_domain_pack_by_id
from src.lib.curation_workspace.curation_prep_service import (
    build_flow_scope_confirmation as _build_flow_scope_confirmation,
    ensure_domain_envelope_materialization,
)
from src.lib.curation_workspace.bootstrap_service import run_flow_curation_handoff
from src.lib.curation_workspace.extraction_results import list_extraction_results
from src.lib.curation_workspace.curation_prep_constants import (
    CURATION_PREP_AGENT_ID,
)
from src.lib.curation_workspace.models import DomainEnvelopeModel
from src.lib.domain_envelopes.persistence import (
    DomainEnvelopeCheckpointRequest,
    write_domain_envelope_checkpoint,
)
from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.materialization import (
    ValidatorResultMaterializationInput,
    materialize_validator_results_into_envelope,
)
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
    ValidatorBindingMatch,
)
from src.lib.domain_packs.validation_findings import append_validation_findings_to_envelope
from src.lib.domain_packs.validator_dispatch import (
    preflight_unresolved_validator_result,
    run_package_scoped_validator_agent,
    unresolved_validator_result_for_dispatch_problem,
    validator_request_payload_for_agent,
    validator_result_from_agent_output,
)
from src.lib.file_outputs import sanitize_output_descriptor
from src.lib.flows.output_projection import (
    FlowOutputArtifactBundle,
    FlowOutputProjectionPlan,
    FlowOutputProjectionResult,
    build_flow_output_artifact_bundle,
    default_projection_plan,
    finalize_output_projection,
    inspect_output_artifacts,
    projection_plan_allows_empty_bundle,
    preview_output_projection,
)
from src.lib.flows.validation_attachments import validation_schedule_from_node_data
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.database import SessionLocal
from src.lib.agent_studio.catalog_service import (
    get_agent_by_id,
    get_agent_metadata,
)
from src.lib.openai_agents.config import (
    get_agent_config,
    get_model_for_agent,
    build_model_settings,
    get_max_turns,
    resolve_model_provider,
)
from src.lib.runtime_payload_budget import provider_context_preflight
from src.lib.openai_agents.evidence_summary import _EvidenceRegistry
from src.lib.openai_agents.event_types import INTERNAL_EXTRACTION_RESULT_EVENT_TYPE
from src.lib.openai_agents.agents.supervisor_agent import _create_streaming_tool
from src.lib.document_context import DocumentContext
from src.schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)
from src.schemas.domain_envelope import DomainEnvelope, ValidationFinding
from src.schemas.domain_validator import DomainValidationRequest, ValidatorAgentRef
from src.schemas.flows import DEFAULT_FLOW_EDGE_ROLE, VALIDATION_ATTACHMENT_EDGE_ROLE

logger = logging.getLogger(__name__)

_FLOW_STEP_OUTPUT_PREVIEW_CHARS = 800
_FLOW_STEP_EVIDENCE_PREVIEW_LIMIT = 10
_FLOW_TEMPLATE_VARIABLE_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
_FLOW_TEMPLATE_DEFAULT_INPUT_FILENAME = "input"
_FLOW_TEMPLATE_DEFAULT_TRACE_ID = "trace"
_FLOW_TSV_FORMATTER_AGENT_IDS = {"tsv_formatter", "tsv_output_formatter"}
_FLOW_CSV_FORMATTER_AGENT_IDS = {"csv_formatter", "csv_output_formatter"}
_FLOW_JSON_FORMATTER_AGENT_IDS = {"json_formatter", "json_output_formatter"}
_FLOW_CHAT_FORMATTER_AGENT_IDS = {"chat_output", "chat_output_formatter"}
_FLOW_OUTPUT_FORMATTER_AGENT_IDS_BY_FORMAT = {
    "csv": _FLOW_CSV_FORMATTER_AGENT_IDS,
    "tsv": _FLOW_TSV_FORMATTER_AGENT_IDS,
    "json": _FLOW_JSON_FORMATTER_AGENT_IDS,
    "chat": _FLOW_CHAT_FORMATTER_AGENT_IDS,
}
_FLOW_OUTPUT_PROJECTION_PLANNER_TOOL_NAMES = frozenset(
    {
        "inspect_output_artifacts",
        "preview_output_projection",
        "finalize_output_projection",
    }
)
_FLOW_OUTPUT_PROJECTION_CUSTOMIZATION_HINT_PATTERN = re.compile(
    r"\b("
    r"artifact|object|objects|evidence|validation|finding|findings|row|rows|"
    r"column|columns|header|headers|rename|omit|exclude|include|filter|"
    r"sort|order|group|grouped|bundle|json|table|section|sections|bullet|"
    r"derived|derive|combine|concat|join|count|map|label|limit|max"
    r")\b",
    re.IGNORECASE,
)
_FLOW_OUTPUT_PROJECTION_CURATOR_REQUEST_HINT_PATTERN = re.compile(
    r"\b("
    r"artifact rows?|object rows?|evidence rows?|validation findings?|"
    r"row source|rows?|columns?|headers?|rename|call .* column|omit|exclude|"
    r"skip|filter|only include|include only|validated rows?|sort|order by|"
    r"before|after|first|last|precede|follows?|group|grouped|bundle|table|sections?|bullets?|derived|derive|"
    r"combine|concat|join|count|map|limit|max"
    r")\b",
    re.IGNORECASE,
)
_FLOW_OUTPUT_PROJECTION_PLANNER_PREVIEW_LIMIT = 5
CURATION_HANDOFF_AGENT_ID = "curation_handoff"
CURATION_HANDOFF_READY_EVENT = "CURATION_HANDOFF_READY"
FLOW_EXTRACTION_HANDOFF_AUDIT_EVENT = "FLOW_EXTRACTION_HANDOFF_AUDIT"


class FlowTemplateConfigurationError(ValueError):
    """Raised when a flow step template is explicitly configured but invalid."""


class FlowTerminalOutputProjectionError(RuntimeError):
    """Raised when a flow terminal formatter cannot project runtime artifacts."""


def _now_iso() -> str:
    """Return current UTC time in ISO format for audit events."""
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started_at: float) -> int:
    """Return elapsed monotonic time in milliseconds."""

    return int((time.monotonic() - started_at) * 1000)


def _emit_flow_runtime_event(event: dict[str, Any]) -> None:
    """Best-effort emission into the current live specialist event stream."""

    try:
        from src.lib.openai_agents.streaming_tools import add_specialist_event

        add_specialist_event(event)
    except Exception:
        logger.debug("Failed to emit flow runtime event", exc_info=True)


def _tool_safe_agent_id(agent_id: str) -> str:
    """Normalize agent_id into a valid Python identifier segment for tool names."""
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", str(agent_id or "")).strip("_")
    return normalized or "agent"


def _normalize_metadata_value(value: Any) -> str:
    """Normalize freeform catalog metadata for case-insensitive matching."""

    return str(value or "").strip().lower()


def _matches_metadata_classification(value: str, candidates: list[str]) -> bool:
    """Return True when a normalized metadata value matches any classifier token."""

    return any(candidate in value for candidate in candidates)


def _is_output_formatter_entry(entry: Optional[dict[str, Any]]) -> bool:
    """Return whether an agent entry represents an output/formatter step."""

    if not isinstance(entry, dict):
        return False

    category = _normalize_metadata_value(entry.get("category"))
    subcategory = _normalize_metadata_value(entry.get("subcategory"))
    return (
        _matches_metadata_classification(category, ["output"])
        or _matches_metadata_classification(subcategory, ["output", "format"])
    )


def _resolve_flow_step_include_evidence(
    *,
    entry: Optional[dict[str, Any]],
    raw_include_evidence: Any,
) -> Optional[bool]:
    """Resolve effective include_evidence semantics for one flow step."""

    if not _is_output_formatter_entry(entry):
        return None
    return raw_include_evidence is not False


def _stringify_tool_output(value: Any) -> str:
    """Convert tool output to a stable text representation."""

    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value or "")


def _capture_internal_extraction_event_cursor() -> dict[str, Any]:
    """Capture current specialist-event positions before invoking a flow step."""

    try:
        from src.lib.openai_agents.streaming_tools import (
            get_collected_events,
            get_live_event_list,
        )
    except Exception:
        return {}

    collected_events = get_collected_events()
    live_events = get_live_event_list()
    return {
        "collected_events": collected_events,
        "collected_index": len(collected_events),
        "live_events": live_events,
        "live_index": len(live_events) if live_events is not None else None,
    }


def _unique_non_empty_values(values: List[Any]) -> List[str]:
    """Return distinct non-empty string values in first-seen order."""

    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _internal_extraction_tool_output_with_audit_since(
    cursor: Mapping[str, Any],
    *,
    tool_name: str,
) -> tuple[Any | None, dict[str, Any]]:
    """Return the latest full structured extraction payload plus lookup audit data."""

    normalized_tool_name = str(tool_name or "").strip()
    audit: dict[str, Any] = {
        "internalEventEmitted": False,
        "internalEventMatchedTool": False,
        "internalEventFoundByFlow": False,
        "internalPayloadFound": False,
        "internalPayloadSource": None,
        "internalEventSources": [],
        "internalEventToolNames": [],
        "builderFinalizationSeen": False,
    }
    if not normalized_tool_name:
        return None, audit

    sources: list[tuple[str, Any, int]] = []
    live_events = cursor.get("live_events")
    live_index = cursor.get("live_index")
    if isinstance(live_events, list) and isinstance(live_index, int):
        sources.append(("live_events", live_events, live_index))

    collected_events = cursor.get("collected_events")
    collected_index = cursor.get("collected_index")
    if isinstance(collected_events, list) and isinstance(collected_index, int):
        sources.append(("collected_events", collected_events, collected_index))

    internal_event_sources: list[str] = []
    internal_event_tool_names: list[str] = []
    for source_name, events, start_index in sources:
        for event in reversed(events[start_index:]):
            if not isinstance(event, Mapping):
                continue
            if event.get("type") != INTERNAL_EXTRACTION_RESULT_EVENT_TYPE:
                continue
            audit["internalEventEmitted"] = True
            internal_event_sources.append(source_name)
            details = event.get("details") or {}
            if not isinstance(details, Mapping):
                continue
            event_tool_name = str(details.get("toolName") or "").strip()
            internal_event_tool_names.append(event_tool_name)
            if event_tool_name != normalized_tool_name:
                continue
            audit["internalEventMatchedTool"] = True
            audit["internalEventFoundByFlow"] = True
            internal = event.get("internal") or {}
            if isinstance(internal, Mapping):
                audit["builderFinalizationSeen"] = bool(
                    internal.get("builder_finalization")
                    or details.get("builderFinalization")
                )
                if "tool_output" in internal and internal.get("tool_output") is not None:
                    audit["internalPayloadFound"] = True
                    audit["internalPayloadSource"] = source_name
                    audit["internalEventSources"] = _unique_non_empty_values(
                        internal_event_sources
                    )
                    audit["internalEventToolNames"] = _unique_non_empty_values(
                        internal_event_tool_names
                    )
                    return internal.get("tool_output"), audit

    audit["internalEventSources"] = _unique_non_empty_values(internal_event_sources)
    audit["internalEventToolNames"] = _unique_non_empty_values(internal_event_tool_names)
    return None, audit


def _internal_extraction_tool_output_since(
    cursor: Mapping[str, Any],
    *,
    tool_name: str,
) -> Any | None:
    """Return the latest full structured extraction payload emitted by a step."""

    payload, _audit = _internal_extraction_tool_output_with_audit_since(
        cursor,
        tool_name=tool_name,
    )
    return payload


def _truncate_tool_output(value: Any, max_chars: int = _FLOW_STEP_OUTPUT_PREVIEW_CHARS) -> str:
    """Generate a bounded preview string for accumulated flow context."""

    text = _stringify_tool_output(value).strip()
    if len(text) <= max_chars:
        return text
    overflow = len(text) - max_chars
    return f"{text[:max_chars]}... [truncated {overflow} chars]"


@dataclass
class _FlowOutputProjectionPlannerState:
    final_plan: FlowOutputProjectionPlan | None = None
    final_result: FlowOutputProjectionResult | None = None
    final_summary: dict[str, Any] | None = None
    errors: list[str] | None = None
    finalize_attempt_count: int = 0

    def record_error(self, message: str) -> None:
        if self.errors is None:
            self.errors = []
        self.errors.append(message)


def _projection_tool_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _projection_plan_from_tool_payload(
    plan_json: str | Mapping[str, Any],
    *,
    output_format: str,
) -> FlowOutputProjectionPlan:
    raw_plan: Any
    if isinstance(plan_json, str):
        raw_text = plan_json.strip()
        if not raw_text:
            raise ValueError("Projection plan JSON is empty.")
        try:
            raw_plan = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Projection plan is not valid JSON: {exc.msg}") from exc
    elif isinstance(plan_json, Mapping):
        raw_plan = dict(plan_json)
    else:
        raise ValueError("Projection plan must be a JSON object or encoded JSON object.")

    if isinstance(raw_plan, Mapping) and isinstance(raw_plan.get("plan"), Mapping):
        raw_plan = raw_plan["plan"]
    if not isinstance(raw_plan, Mapping):
        raise ValueError("Projection plan must decode to a JSON object.")

    try:
        plan = FlowOutputProjectionPlan.model_validate(raw_plan)
    except ValidationError as exc:
        raise ValueError(f"Projection plan schema is invalid: {exc}") from exc
    return plan.model_copy(update={"format": output_format})


def _projection_result_summary(
    result: FlowOutputProjectionResult,
) -> dict[str, Any]:
    warnings = [
        _truncate_tool_output(warning, 240)
        for warning in result.warnings[:20]
    ]
    if len(result.warnings) > 20:
        warnings.append(f"... [truncated {len(result.warnings) - 20} warnings]")
    return {
        "status": "ok",
        "format": result.format,
        "row_source": result.row_source,
        "columns": [
            column.model_dump(mode="json")
            for column in result.columns
        ],
        "total_count": result.total_count,
        "row_count": len(result.rows),
        "truncated": result.truncated,
        "group_by": result.group_by,
        "warnings": warnings,
    }


def _build_output_projection_planner_tools(
    *,
    bundle: FlowOutputArtifactBundle,
    output_format: str,
    state: _FlowOutputProjectionPlannerState,
) -> list[Any]:
    """Build bounded projection-planning tools for a terminal flow formatter."""

    @function_tool(
        name_override="inspect_output_artifacts",
        description_override=(
            "Inspect bounded row-source counts, default columns, field refs, "
            "and compact examples for the completed flow artifacts."
        ),
        strict_mode=False,
    )
    async def _inspect_output_artifacts() -> str:
        return _projection_tool_json(
            {
                "status": "ok",
                "inventory": inspect_output_artifacts(bundle),
            }
        )

    @function_tool(
        name_override="preview_output_projection",
        description_override=(
            "Validate a proposed projection plan and return a bounded preview. "
            "Pass the plan as encoded JSON in plan_json."
        ),
        strict_mode=False,
    )
    async def _preview_output_projection(plan_json: str) -> str:
        try:
            plan = _projection_plan_from_tool_payload(
                plan_json,
                output_format=output_format,
            )
            preview = preview_output_projection(
                bundle,
                plan,
                limit=_FLOW_OUTPUT_PROJECTION_PLANNER_PREVIEW_LIMIT,
            )
            return _projection_tool_json(
                {
                    "status": preview.status,
                    "preview": preview.model_dump(mode="json"),
                }
            )
        except Exception as exc:
            message = str(exc)
            state.record_error(message)
            return _projection_tool_json(
                {
                    "status": "invalid",
                    "errors": [message],
                }
            )

    @function_tool(
        name_override="finalize_output_projection",
        description_override=(
            "Finalize the validated projection plan. This records only the plan "
            "and a summary; the runtime saves or renders the output."
        ),
        strict_mode=False,
    )
    async def _finalize_output_projection(plan_json: str) -> str:
        state.finalize_attempt_count += 1
        try:
            plan = _projection_plan_from_tool_payload(
                plan_json,
                output_format=output_format,
            )
            result = finalize_output_projection(bundle, plan)
        except Exception as exc:
            message = str(exc)
            state.record_error(message)
            return _projection_tool_json(
                {
                    "status": "invalid",
                    "errors": [message],
                    "attempt": state.finalize_attempt_count,
                }
            )

        state.final_plan = plan
        state.final_result = result
        state.final_summary = _projection_result_summary(result)
        return _projection_tool_json(state.final_summary)

    return [
        _inspect_output_artifacts,
        _preview_output_projection,
        _finalize_output_projection,
    ]


def _projection_planner_inventory_summary(
    bundle: FlowOutputArtifactBundle,
) -> dict[str, Any]:
    row_sources = {
        row_source: len(bundle.rows_for_source(row_source))  # type: ignore[arg-type]
        for row_source in ("artifact", "object", "evidence", "validation_finding")
    }
    fields = [
        {
            "ref": field.ref,
            "label": field.label,
            "row_source": field.row_source,
            "value_type": field.value_type,
            "non_empty_count": field.non_empty_count,
            "examples": field.examples[:2],
        }
        for field in bundle.field_catalog[:80]
    ]
    warnings = [
        _truncate_tool_output(warning, 240)
        for warning in bundle.warnings[:20]
    ]
    if len(bundle.warnings) > 20:
        warnings.append(f"... [truncated {len(bundle.warnings) - 20} warnings]")
    return {
        "flow_name": bundle.flow_name,
        "flow_run_id": bundle.flow_run_id,
        "document_id": bundle.document_id,
        "artifact_count": len(bundle.artifacts),
        "default_row_source": bundle.default_row_source,
        "row_sources": row_sources,
        "fields": fields,
        "warnings": warnings,
    }


def _build_output_projection_planner_instructions(
    *,
    output_format: str,
    agent_name: str,
) -> str:
    return (
        f"You are the flow-output projection planner for {agent_name}. "
        f"The terminal output format is {output_format}.\n\n"
        "Your job is to choose a small FlowOutputProjectionPlan that satisfies "
        "the curator's output-shaping request using only the fields exposed by "
        "the projection tools. You must not write CSV, TSV, JSON, markdown, or "
        "file contents yourself. You must not call or request save-file tools. "
        "The runtime will save or render the final output after your plan is "
        "validated.\n\n"
        "Use inspect_output_artifacts when you need row-source counts, fields, "
        "or examples. Use preview_output_projection to check columns, filters, "
        "sorting, grouping, and transforms. Always call finalize_output_projection "
        "with the final valid plan. If a tool returns validation errors, correct "
        "the plan once using the available field refs and finalize again."
    )


def _build_output_projection_planner_input(
    *,
    bundle: FlowOutputArtifactBundle,
    output_format: str,
    default_plan: FlowOutputProjectionPlan,
    agent_id: str,
    agent_name: str,
    node_data: Mapping[str, Any] | None,
    resolved_query: str | None,
) -> str:
    node_data = node_data or {}
    payload = {
        "terminal_format": output_format,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "default_plan": default_plan.model_dump(mode="json"),
        "artifact_inventory_summary": _projection_planner_inventory_summary(bundle),
        "curator_output_request": {
            "step_goal": _truncate_tool_output(node_data.get("step_goal"), 1200),
            "custom_instructions": _truncate_tool_output(
                node_data.get("custom_instructions"),
                1200,
            ),
            "flow_step_query": _truncate_tool_output(resolved_query, 1600),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _build_output_projection_planner_retry_input(
    *,
    previous_input: str,
    state: _FlowOutputProjectionPlannerState,
) -> str:
    errors = state.errors or []
    payload = {
        "retry_reason": (
            "No valid projection was finalized. Correct the projection plan and "
            "call finalize_output_projection exactly once with a valid plan."
        ),
        "previous_errors": errors[-5:],
        "previous_request": previous_input,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _flow_output_default_row_source_is_ambiguous(
    *,
    bundle: FlowOutputArtifactBundle,
    output_format: str,
) -> bool:
    if output_format == "tsv":
        return False
    richer_sources = [
        row_source
        for row_source in ("object", "evidence", "validation_finding")
        if bundle.rows_for_source(row_source)  # type: ignore[arg-type]
    ]
    return bundle.default_row_source == "artifact" and len(richer_sources) > 1


def _flow_query_section_text(resolved_query: str | None, heading: str) -> str:
    text = str(resolved_query or "")
    marker = f"{heading}:\n"
    start = text.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = text.find("\n\n", start)
    if end < 0:
        end = len(text)
    return text[start:end].strip()


def _has_projection_customization_hint(
    text: str | None,
    *,
    curator_request: bool = False,
) -> bool:
    pattern = (
        _FLOW_OUTPUT_PROJECTION_CURATOR_REQUEST_HINT_PATTERN
        if curator_request
        else _FLOW_OUTPUT_PROJECTION_CUSTOMIZATION_HINT_PATTERN
    )
    return bool(
        text
        and pattern.search(str(text))
    )


def _flow_output_should_run_projection_planner(
    *,
    bundle: FlowOutputArtifactBundle,
    output_format: str,
    node_data: Mapping[str, Any] | None,
    resolved_query: str | None = None,
) -> bool:
    node_data = node_data or {}
    custom_instructions = str(node_data.get("custom_instructions") or "").strip()
    if custom_instructions:
        return True

    step_goal = str(node_data.get("step_goal") or "").strip()
    if _has_projection_customization_hint(step_goal):
        return True

    curator_run_request = _flow_query_section_text(
        resolved_query,
        "Curator run request",
    )
    if _has_projection_customization_hint(curator_run_request, curator_request=True):
        return True

    return _flow_output_default_row_source_is_ambiguous(
        bundle=bundle,
        output_format=output_format,
    )


def _projection_planner_failure_message(
    state: _FlowOutputProjectionPlannerState,
) -> str:
    errors = state.errors or []
    if errors:
        return "; ".join(errors[-5:])
    return "planner did not call finalize_output_projection with a valid plan"


async def _run_output_projection_planner(
    *,
    bundle: FlowOutputArtifactBundle,
    output_format: str,
    default_plan: FlowOutputProjectionPlan,
    agent_id: str,
    agent_name: str,
    node_data: Mapping[str, Any] | None,
    resolved_query: str | None,
) -> FlowOutputProjectionResult:
    """Ask a dedicated planner to finalize a projection plan for custom output."""

    state = _FlowOutputProjectionPlannerState()
    tools = _build_output_projection_planner_tools(
        bundle=bundle,
        output_format=output_format,
        state=state,
    )

    config = get_agent_config(agent_id)
    provider = resolve_model_provider(config.model)
    model = get_model_for_agent(config.model, provider_override=provider)
    model_settings = build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=config.reasoning,
        tool_choice=config.tool_choice,
        parallel_tool_calls=False,
        provider_override=provider,
    )
    planner_agent = Agent(
        name=f"{agent_name} Projection Planner",
        instructions=_build_output_projection_planner_instructions(
            output_format=output_format,
            agent_name=agent_name,
        ),
        model=model,
        model_settings=model_settings,
        tools=tools,
    )

    planner_input = _build_output_projection_planner_input(
        bundle=bundle,
        output_format=output_format,
        default_plan=default_plan,
        agent_id=agent_id,
        agent_name=agent_name,
        node_data=node_data,
        resolved_query=resolved_query,
    )
    max_turns = max(4, min(get_max_turns(), 8))

    for attempt in range(2):
        run_input = (
            planner_input
            if attempt == 0
            else _build_output_projection_planner_retry_input(
                previous_input=planner_input,
                state=state,
            )
        )
        await Runner.run(planner_agent, run_input, max_turns=max_turns)
        if state.final_result is not None:
            logger.info(
                "[Flow Executor] Projection planner finalized %s output for '%s' "
                "(row_source=%s, rows=%s, attempts=%s)",
                output_format.upper(),
                agent_id,
                state.final_result.row_source,
                state.final_result.total_count,
                attempt + 1,
            )
            return state.final_result

    raise RuntimeError(
        "Flow output projection planner did not finalize a valid plan: "
        f"{_projection_planner_failure_message(state)}"
    )


def _build_flow_step_instruction_prefix(
    *,
    custom_instructions: Optional[str],
    include_evidence: Optional[bool],
) -> str:
    """Build step-local instructions for the selected agent's runtime layer."""

    sections: List[str] = []

    if custom_instructions and custom_instructions.strip():
        sections.append(
            "## CUSTOM INSTRUCTIONS (from flow configuration)\n\n"
            "The following instructions were provided by the user for this specific flow step. "
            "They take the HIGHEST PRIORITY and MUST be followed above all other guidelines. "
            "Treat these as direct requirements from the curator.\n\n"
            + custom_instructions.strip()
        )

    if include_evidence is True:
        sections.append(
            "## OUTPUT EVIDENCE REQUIREMENT (from flow configuration)\n\n"
            "When producing the final output for this flow step, include supporting evidence "
            "from earlier steps whenever it is available. Keep that evidence clearly tied to "
            "the corresponding output item, preserve concrete quote or location details when "
            "present, and never invent evidence or citations. If no supporting evidence is "
            "available for a result, say that plainly instead of fabricating it."
        )
    elif include_evidence is False:
        sections.append(
            "## OUTPUT EVIDENCE EXCLUSION (from flow configuration)\n\n"
            "When producing the final output for this flow step, do NOT include supporting "
            "evidence, quote columns, citation fields, or source-location details in the "
            "formatted result. Keep the output focused on the extracted entities or summaries "
            "only, and do not invent placeholder evidence text."
        )

    if not sections:
        return ""

    return "\n\n---\n\n".join(sections) + "\n\n---\n\n"


def _build_flow_conversation_summary(
    flow: CurationFlow,
    user_query: Optional[str],
) -> str:
    """Choose the best available summary string for flow persistence and prep context."""

    for candidate in (user_query, get_task_instructions(flow)):
        text = str(candidate or "").strip()
        if text:
            return text
    return f"Run flow '{flow.name}'"


def _extract_flow_input_filename(document_name: Optional[str]) -> str:
    """Return the input filename basename or a safe fallback when no document is loaded."""

    candidate = str(document_name or "").strip()
    if not candidate:
        return _FLOW_TEMPLATE_DEFAULT_INPUT_FILENAME
    basename = Path(candidate.replace("\\", "/")).name.strip()
    return basename or _FLOW_TEMPLATE_DEFAULT_INPUT_FILENAME


def _extract_flow_input_filename_stem(document_name: Optional[str]) -> str:
    """Return the input filename stem or a safe fallback when no document is loaded."""

    return Path(_extract_flow_input_filename(document_name)).stem or _FLOW_TEMPLATE_DEFAULT_INPUT_FILENAME


def _format_flow_template_timestamp(now: Optional[datetime] = None) -> str:
    """Return the canonical UTC timestamp used by flow template variables."""

    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _build_flow_builtin_template_variables(
    *,
    document_name: Optional[str],
    flow_run_id: Optional[str],
    timestamp: Optional[str] = None,
) -> dict[str, str]:
    """Assemble bounded built-in variables for formatter filename descriptors."""

    return {
        "input_filename": _extract_flow_input_filename(document_name),
        "input_filename_stem": _extract_flow_input_filename_stem(document_name),
        "trace_id": (
            get_current_trace_id()
            or str(flow_run_id or "").strip()
            or _FLOW_TEMPLATE_DEFAULT_TRACE_ID
        ),
        "timestamp": timestamp or _format_flow_template_timestamp(),
    }


def _render_flow_template(
    template: Optional[str],
    template_variables: dict[str, str],
) -> str:
    """Render {{variable}} placeholders using the provided flow variable map."""

    if not isinstance(template, str):
        return ""

    unresolved_variables: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in template_variables:
            return template_variables[key]
        unresolved_variables.add(key)
        return ""

    rendered = _FLOW_TEMPLATE_VARIABLE_PATTERN.sub(_replace, template)
    if unresolved_variables:
        logger.warning(
            "[Flow Executor] Unresolved flow template variables %s in template %r",
            sorted(unresolved_variables),
            template,
        )
    return rendered


def _resolve_output_filename_descriptor(
    *,
    output_filename_template: Optional[str],
    template_variables: dict[str, str],
) -> Optional[str]:
    """Resolve and sanitize the output filename descriptor override for formatter steps."""

    if not isinstance(output_filename_template, str) or not output_filename_template.strip():
        return None

    rendered = _render_flow_template(output_filename_template, template_variables).strip()
    if not rendered:
        raise FlowTemplateConfigurationError(
            "output_filename_template rendered empty after variable substitution; "
            "check the configured template variables."
        )

    return sanitize_output_descriptor(rendered)


def _append_flow_query_section(
    sections: list[str],
    heading: str,
    value: Any,
) -> None:
    normalized = str(value or "").strip()
    if normalized:
        sections.append(f"{heading}:\n{normalized}")


def _build_flow_step_query(
    *,
    flow: CurationFlow,
    node_data: Mapping[str, Any],
    step_number: int,
    agent_name: str,
    user_query: Optional[str],
    document_id: Optional[str],
    document_name: Optional[str],
) -> str:
    """Build the bounded prompt passed to one configured flow step.

    The supervisor's tool-call argument is intentionally ignored. Flow step input
    comes from the authored task, loaded document identity, and step-local
    configuration; completed step artifacts remain in runtime state.
    """

    sections: list[str] = []
    _append_flow_query_section(sections, "Flow task", get_task_instructions(flow))
    _append_flow_query_section(sections, "Curator run request", user_query)

    document_bits = []
    if document_name:
        document_bits.append(f"name={document_name}")
    if document_id:
        document_bits.append(f"id={document_id}")
    if document_bits:
        sections.append(
            "Loaded document:\n"
            + ", ".join(document_bits)
            + "\nUse the document context and document tools already attached to this specialist."
        )

    _append_flow_query_section(sections, "Configured step", f"{step_number}. {agent_name}")
    _append_flow_query_section(sections, "Step goal", node_data.get("step_goal"))
    _append_flow_query_section(
        sections,
        "Step-local custom instructions",
        node_data.get("custom_instructions"),
    )

    sections.append(
        "Runtime artifact policy:\n"
        "Run only this configured step. Do not rely on, request, or receive full "
        "previous-step output in this prompt. The flow runtime stores completed "
        "artifacts separately for review, validation, export, and final handoff."
    )

    if not sections:
        return f"Run step {step_number} of the '{flow.name}' curation flow."
    return "\n\n".join(sections)


def _resolve_flow_terminal_output_format(agent_id: str) -> Optional[str]:
    normalized_agent_id = str(agent_id or "").strip()
    for output_format, agent_ids in _FLOW_OUTPUT_FORMATTER_AGENT_IDS_BY_FORMAT.items():
        if normalized_agent_id in agent_ids:
            return output_format
    return None


def _flow_terminal_projection_error(agent_id: str, reason: str) -> FlowTerminalOutputProjectionError:
    return FlowTerminalOutputProjectionError(
        "Flow terminal formatter "
        f"'{agent_id}' could not project runtime-owned output: {reason}. "
        "Flow terminal formatter steps cannot fall back to ordinary formatter "
        "models or model-written file contents."
    )


async def _save_projected_file_output(
    *,
    output_format: str,
    projection: FlowOutputProjectionResult,
    descriptor: str,
) -> dict[str, Any]:
    data_rows = projection.rows
    if output_format == "csv":
        from src.lib.openai_agents.tools.file_output_tools import _save_csv_impl

        return await _save_csv_impl(
            data_json=json.dumps(data_rows, ensure_ascii=False),
            filename=descriptor,
            columns=json.dumps(
                [column.key for column in projection.columns],
                ensure_ascii=False,
            ),
        )
    if output_format == "tsv":
        from src.lib.openai_agents.tools.file_output_tools import _save_tsv_impl

        data_rows = [
            {
                key: str(value or "").strip()
                for key, value in row.items()
            }
            for row in projection.rows
        ]
        return await _save_tsv_impl(
            data_json=json.dumps(data_rows, ensure_ascii=False),
            filename=descriptor,
            columns=json.dumps(
                [column.key for column in projection.columns],
                ensure_ascii=False,
            ),
        )
    if output_format == "json":
        from src.lib.openai_agents.tools.file_output_tools import _save_json_impl

        json_data = projection.json_data if projection.json_data is not None else projection.rows
        return await _save_json_impl(
            data_json=json.dumps(json_data, ensure_ascii=False),
            filename=descriptor,
            pretty=True,
        )
    raise ValueError(f"Unsupported projected file output format: {output_format}")


async def _try_project_terminal_flow_output(
    *,
    agent_id: str,
    completed_steps: list[dict[str, Any]],
    flow_name: str,
    agent_name: str | None = None,
    flow_run_id: str | None = None,
    document_id: str | None = None,
    projection_plan: Mapping[str, Any] | None = None,
    node_data: Mapping[str, Any] | None = None,
    resolved_query: str | None = None,
    output_filename_descriptor: str | None = None,
) -> Optional[str]:
    """Deterministically project terminal flow output from completed artifacts."""

    output_format = _resolve_flow_terminal_output_format(agent_id)
    if output_format is None:
        return None

    try:
        plan_for_empty_check: FlowOutputProjectionPlan | None = None
        if projection_plan is not None:
            plan_for_empty_check = FlowOutputProjectionPlan.model_validate(projection_plan).model_copy(
                update={"format": output_format}
            )
        bundle = build_flow_output_artifact_bundle(
            completed_steps=completed_steps,
            flow_name=flow_name,
            flow_run_id=flow_run_id,
            document_id=document_id,
            output_format=output_format,  # type: ignore[arg-type]
        )
        if not bundle.artifacts and not (
            plan_for_empty_check is not None
            and projection_plan_allows_empty_bundle(plan_for_empty_check)
        ):
            raise _flow_terminal_projection_error(
                agent_id,
                "no completed structured artifacts were available before the terminal formatter",
            )

        default_plan = default_projection_plan(bundle, output_format=output_format)  # type: ignore[arg-type]
        if plan_for_empty_check is not None:
            plan = plan_for_empty_check
            projection = finalize_output_projection(bundle, plan)
        elif _flow_output_should_run_projection_planner(
            bundle=bundle,
            output_format=output_format,
            node_data=node_data,
            resolved_query=resolved_query,
        ):
            projection = await _run_output_projection_planner(
                bundle=bundle,
                output_format=output_format,
                default_plan=default_plan,
                agent_id=agent_id,
                agent_name=agent_name or agent_id,
                node_data=node_data,
                resolved_query=resolved_query,
            )
        else:
            projection = finalize_output_projection(bundle, default_plan)
    except Exception as exc:
        raise _flow_terminal_projection_error(agent_id, str(exc)) from exc

    if output_format == "chat":
        logger.info(
            "[Flow Executor] Rendered chat formatter flow artifact output directly for '%s' (%s rows)",
            agent_id,
            projection.total_count,
        )
        return projection.chat_output or "No rows matched the requested output projection."

    descriptor = sanitize_output_descriptor(
        output_filename_descriptor or f"{flow_name}_{output_format}_export"
    )
    result = await _save_projected_file_output(
        output_format=output_format,
        projection=projection,
        descriptor=descriptor,
    )
    logger.info(
        "[Flow Executor] Saved %s formatter flow artifact output directly for '%s' (%s rows)",
        output_format.upper(),
        agent_id,
        projection.total_count,
    )
    return json.dumps(result)


def _resolve_flow_candidate_adapter_key(candidate: ExtractionEnvelopeCandidate) -> Optional[str]:
    """Return the adapter-owned key already persisted on the extraction envelope."""

    normalized = str(candidate.adapter_key or "").strip()
    return normalized or None


def _flow_candidate_persistence_key(candidate: ExtractionEnvelopeCandidate) -> str:
    """Return the deterministic persistence key for one flow extraction step."""

    metadata = candidate.metadata or {}
    key_parts = [
        str(metadata.get("flow_id") or "").strip(),
        str(metadata.get("step") or "").strip(),
        str(metadata.get("tool_name") or "").strip(),
        str(candidate.agent_key or "").strip(),
    ]
    return ":".join(part for part in key_parts if part)


def _flow_record_persistence_key(record: CurationExtractionResultRecord) -> str:
    metadata = record.metadata or {}
    explicit_key = str(metadata.get("flow_step_key") or "").strip()
    if explicit_key:
        return explicit_key
    key_parts = [
        str(metadata.get("flow_id") or "").strip(),
        str(metadata.get("step") or "").strip(),
        str(metadata.get("tool_name") or "").strip(),
        str(record.agent_key or "").strip(),
    ]
    return ":".join(part for part in key_parts if part)


def _unique_non_empty_scope_values(values: list[Optional[str]]) -> list[str]:
    """Return distinct non-empty scope values in first-seen order."""

    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _build_flow_prep_extraction_results(
    *,
    completed_steps: list[dict[str, Any]],
    document_id: str,
    user_id: str,
    session_id: str,
    flow_run_id: Optional[str],
    conversation_summary: str,
) -> list[CurationExtractionResultRecord]:
    """Convert completed flow extraction steps into prep-service input records."""

    created_at = datetime.now(timezone.utc)
    trace_id = get_current_trace_id()
    extraction_results: list[CurationExtractionResultRecord] = []

    for step in completed_steps:
        candidate = step.get("candidate")
        if not isinstance(candidate, ExtractionEnvelopeCandidate):
            continue
        if candidate.agent_key == CURATION_PREP_AGENT_ID:
            continue

        step_number = int(step.get("step") or 0)
        extraction_results.append(
            CurationExtractionResultRecord.model_validate(
                {
                    "extraction_result_id": (
                        f"flow:{session_id}:step:{step_number}:{candidate.agent_key}"
                    ),
                    "document_id": document_id,
                    "adapter_key": candidate.adapter_key,
                    "agent_key": candidate.agent_key,
                    "source_kind": CurationExtractionSourceKind.FLOW,
                    "origin_session_id": session_id,
                    "trace_id": trace_id,
                    "flow_run_id": flow_run_id,
                    "user_id": user_id,
                    "candidate_count": candidate.candidate_count,
                    "conversation_summary": candidate.conversation_summary or conversation_summary,
                    "payload_json": candidate.payload_json,
                    "created_at": created_at,
                    "metadata": dict(candidate.metadata),
                }
            )
        )

    return extraction_results


def _accumulate_step_evidence(
    registry: _EvidenceRegistry,
    evidence_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge one step's evidence into the flow registry and preserve raw per-step counts."""

    if not evidence_records:
        return {"evidence_records": [], "evidence_count": 0}

    step_registry = _EvidenceRegistry()
    step_registry.add_many(evidence_records)
    step_records = step_registry.records()

    registry.add_many(step_records)

    return {
        "evidence_records": step_records,
        "evidence_count": len(step_records),
    }


def _find_completed_step_by_tool_name(
    completed_steps: list[dict[str, Any]],
    tool_name: str,
) -> Optional[dict[str, Any]]:
    """Return the completed-step entry for a tool invocation."""

    normalized_tool_name = str(tool_name or "").strip()
    if not normalized_tool_name:
        return None

    for step in reversed(completed_steps):
        if str(step.get("tool_name") or "").strip() == normalized_tool_name:
            return step
    return None


def _collect_completed_step_candidates(
    completed_steps: list[dict[str, Any]],
) -> list[ExtractionEnvelopeCandidate]:
    """Collect persistable extraction candidates from completed flow steps."""

    candidates: list[ExtractionEnvelopeCandidate] = []
    for step in completed_steps:
        candidate = step.get("candidate")
        if isinstance(candidate, ExtractionEnvelopeCandidate):
            candidates.append(candidate)
    return candidates


def _plain_validation_group(raw_group: Any) -> dict[str, Any]:
    if hasattr(raw_group, "model_dump"):
        return raw_group.model_dump()
    if isinstance(raw_group, Mapping):
        return dict(raw_group)
    raise ValueError(f"Unexpected validation group type: {type(raw_group).__name__}")


def _validation_groups_from_node_data(node_data: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        group
        for group in (
            _plain_validation_group(raw_group)
            for raw_group in node_data.get("validation_groups") or []
        )
        if group.get("state") in {"automatic", "replaced", "supplemental", "skipped"}
    ]


def _binding_id_from_group(group: Mapping[str, Any]) -> str | None:
    binding_id = str(group.get("binding_id") or group.get("validator_binding_id") or "").strip()
    return binding_id or None


def _groups_by_state(groups: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for group in groups:
        grouped.setdefault(str(group.get("state") or ""), []).append(group)
    return grouped


def _flow_node_by_id(flow: CurationFlow) -> dict[str, dict[str, Any]]:
    return {
        str(node.get("id")): node
        for node in (flow.flow_definition or {}).get("nodes", []) or []
        if node.get("id")
    }


def _validation_matches_by_binding(
    matches: tuple[ValidatorBindingMatch, ...],
) -> dict[str, list[ValidatorBindingMatch]]:
    by_binding: dict[str, list[ValidatorBindingMatch]] = {}
    for match in matches:
        by_binding.setdefault(match.binding.binding_id, []).append(match)
    return by_binding


def _validation_binding_has_dispatch_contract(match: ValidatorBindingMatch) -> bool:
    return bool(match.binding.input_fields or match.binding.expected_result_fields)


async def _run_custom_flow_validator_agent(
    request: DomainValidationRequest,
    *,
    binding_match: ValidatorBindingMatch,
    validator_node: Mapping[str, Any],
    agent_context: Mapping[str, Any],
    source_envelope_id: str,
    source_envelope_revision: int,
) -> Any:
    node_data = validator_node.get("data", {}) if isinstance(validator_node, Mapping) else {}
    validator_agent_id = str(node_data.get("agent_id") or "").strip()
    if not validator_agent_id:
        raise ValueError("validation attachment target node is missing agent_id")

    instruction_prefix = (
        "## FLOW VALIDATOR REQUEST\n\n"
        "You are running as a Flow Builder validation attachment. Validate only the "
        "compact DomainValidationRequest JSON supplied in the user message. The "
        "runtime payload may omit selector declarations, target.input_values, and "
        "full evidence records when they duplicate selected_inputs or evidence_summary. Return one JSON "
        "object matching the DomainValidatorResultBase contract. Preserve the supplied "
        "request_id, validator_binding_id, validator_agent, and target fields exactly.\n\n"
        "---\n\n"
    )
    runtime_context = [instruction_prefix]
    agent_kwargs = dict(agent_context)
    agent_kwargs["additional_runtime_context"] = runtime_context
    agent = get_agent_by_id(validator_agent_id, **agent_kwargs)

    tool_name = (
        "validate_"
        f"{_tool_safe_agent_id(validator_agent_id)}_"
        f"{_tool_safe_agent_id(request.validator_binding_id)}"
    )
    streaming_tool: Any = _create_streaming_tool(
        agent=agent,
        tool_name=tool_name,
        tool_description=f"Run validator attachment {validator_agent_id}",
        specialist_name=node_data.get("agent_display_name") or validator_agent_id,
    )
    provider_payload = {
        "source_envelope": {
            "envelope_id": source_envelope_id,
            "revision": source_envelope_revision,
        },
        "validator_binding": binding_match.binding.identity_details(),
        "validation_request": validator_request_payload_for_agent(request),
    }
    try:
        validator_model = get_model_for_agent(validator_agent_id)
        validator_provider = resolve_model_provider(validator_model)
    except Exception:
        validator_model = None
        validator_provider = None
    provider_context_preflight(
        surface="flow_validator",
        operation="custom_flow_validator",
        provider=validator_provider,
        model=validator_model,
        payload=provider_payload,
        metadata={
            "validator_binding_id": request.validator_binding_id,
            "request_id": request.request_id,
            "validator_agent_id": validator_agent_id,
            "source_envelope_id": source_envelope_id,
            "source_envelope_revision": source_envelope_revision,
        },
        emit_runtime_event=True,
    )
    payload = json.dumps(provider_payload, sort_keys=True)
    if hasattr(streaming_tool, "on_invoke_tool"):
        tool_ctx = SimpleNamespace(tool_name=tool_name)
        return await streaming_tool.on_invoke_tool(
            tool_ctx,
            json.dumps({"query": payload}),
        )
    return await streaming_tool(query=payload)


def _ordered_validation_matches(
    matches: list[ValidatorBindingMatch],
) -> list[ValidatorBindingMatch]:
    return sorted(
        matches,
        key=lambda match: (
            match.binding.binding_id,
            json.dumps(match.target_details(), sort_keys=True),
        ),
    )


def _request_for_flow_validator_node(
    request: DomainValidationRequest,
    validator_node: Mapping[str, Any],
) -> DomainValidationRequest:
    node_data = validator_node.get("data", {}) if isinstance(validator_node, Mapping) else {}
    validator_agent_id = str(node_data.get("agent_id") or "").strip()
    if not validator_agent_id:
        return request
    return request.model_copy(
        update={
            "request_id": f"{request.request_id}:flow-validator:{validator_agent_id}",
            "validator_agent": ValidatorAgentRef(
                package_id="flow",
                agent_id=validator_agent_id,
            ),
        }
    )


async def _collect_flow_validator_materialization_inputs(
    *,
    source_envelope: DomainEnvelope,
    source_envelope_revision: int,
    registry: DomainPackValidationRegistry,
    groups: list[dict[str, Any]],
    flow: CurationFlow,
    agent_context: Mapping[str, Any],
) -> tuple[
    list[ValidatorResultMaterializationInput],
    list[ValidationFinding],
    list[dict[str, Any]],
]:
    matches_by_binding = _validation_matches_by_binding(
        registry.match_bindings(
            source_envelope,
            states=[ValidationBindingState.ACTIVE],
        )
    )
    nodes_by_id = _flow_node_by_id(flow)
    materialization_inputs: list[ValidatorResultMaterializationInput] = []
    selector_findings: list[ValidationFinding] = []
    result_metadata: list[dict[str, Any]] = []

    for group in groups:
        state = str(group.get("state") or "")
        binding_id = _binding_id_from_group(group)
        if state == "skipped":
            result_metadata.append(
                {
                    "group_id": group.get("group_id"),
                    "state": state,
                    "validator_binding_id": binding_id,
                    "status": "skipped",
                    "skipped_by_flow_configuration": True,
                }
            )
            continue
        if state not in {"automatic", "replaced", "supplemental"} or not binding_id:
            continue

        binding_matches = _ordered_validation_matches(matches_by_binding.get(binding_id, []))
        if not binding_matches:
            result_metadata.append(
                {
                    "group_id": group.get("group_id"),
                    "state": state,
                    "validator_binding_id": binding_id,
                    "status": "not_matched",
                }
            )
            continue

        validator_node = None
        if state in {"replaced", "supplemental"}:
            validator_node_id = str(group.get("validator_node_id") or "").strip()
            validator_node = nodes_by_id.get(validator_node_id)

        for match in binding_matches:
            if state == "automatic" and not _validation_binding_has_dispatch_contract(
                match
            ):
                result_metadata.append(
                    {
                        "group_id": group.get("group_id"),
                        "state": state,
                        "validator_binding_id": binding_id,
                        "status": "non_dispatch_binding",
                    }
                )
                continue

            if state == "automatic" and _source_envelope_has_validator_finding(
                source_envelope,
                binding_id=binding_id,
                match=match,
            ):
                result_metadata.append(
                    {
                        "group_id": group.get("group_id"),
                        "state": state,
                        "validator_binding_id": binding_id,
                        "status": "already_validated",
                    }
                )
                continue

            selector_result = build_domain_validation_request(match)
            if selector_result.findings:
                selector_findings.extend(selector_result.findings)
                result_metadata.append(
                    {
                        "group_id": group.get("group_id"),
                        "state": state,
                        "validator_binding_id": binding_id,
                        "status": "selector_failed",
                        "finding_count": len(selector_result.findings),
                    }
                )
                continue
            if selector_result.request is None:
                result_metadata.append(
                    {
                        "group_id": group.get("group_id"),
                        "state": state,
                        "validator_binding_id": binding_id,
                        "status": "request_not_available",
                    }
                )
                continue

            request = selector_result.request
            if state in {"replaced", "supplemental"} and validator_node is not None:
                request = _request_for_flow_validator_node(request, validator_node)
            validator_result = (
                None
                if state in {"replaced", "supplemental"}
                else preflight_unresolved_validator_result(request)
            )
            if validator_result is None:
                try:
                    if state in {"replaced", "supplemental"}:
                        if validator_node is None:
                            raise ValueError(
                                "custom validator node is missing from the flow definition"
                            )
                        raw_output = await _run_custom_flow_validator_agent(
                            request,
                            binding_match=match,
                            validator_node=validator_node,
                            agent_context=agent_context,
                            source_envelope_id=source_envelope.envelope_id,
                            source_envelope_revision=source_envelope_revision,
                        )
                    else:
                        raw_output = await asyncio.to_thread(
                            run_package_scoped_validator_agent,
                            request,
                            binding=match.binding,
                        )
                    validator_result = validator_result_from_agent_output(
                        raw_output,
                        request=request,
                    )
                except Exception as exc:
                    logger.warning(
                        "[Flow Executor] Validator group '%s' failed for binding %s",
                        group.get("group_id"),
                        binding_id,
                        exc_info=exc,
                    )
                    validator_result = unresolved_validator_result_for_dispatch_problem(
                        request,
                        reason="validator_agent_error",
                        explanation=f"Validator agent execution failed: {exc}",
                    )

            materialization_inputs.append(
                ValidatorResultMaterializationInput(
                    match=match,
                    request=request,
                    result=validator_result,
                )
            )
            result_metadata.append(
                {
                    "group_id": group.get("group_id"),
                    "state": state,
                    "validator_binding_id": binding_id,
                    "status": validator_result.status,
                    "request_id": request.request_id,
                    "validator_agent": request.validator_agent.model_dump(mode="json"),
                    "target": request.target.model_dump(mode="json"),
                    "selected_inputs": dict(request.selected_inputs),
                    "input_selectors": dict(request.input_selectors),
                    "expected_result_fields": dict(request.expected_result_fields),
                    "lookup_attempts": [
                        attempt.model_dump(mode="json")
                        if hasattr(attempt, "model_dump")
                        else dict(attempt)
                        for attempt in (validator_result.lookup_attempts or [])
                        if hasattr(attempt, "model_dump") or isinstance(attempt, Mapping)
                    ],
                    "curator_message": validator_result.curator_message,
                    "missing_expected_fields": list(
                        validator_result.missing_expected_fields
                    ),
                }
            )

    return materialization_inputs, selector_findings, result_metadata


def _source_envelope_has_validator_finding(
    source_envelope: DomainEnvelope,
    *,
    binding_id: str,
    match: ValidatorBindingMatch,
) -> bool:
    """Return whether an automatic flow validator already ran upstream."""

    for finding in source_envelope.validation_findings:
        details = finding.details if isinstance(finding.details, Mapping) else {}
        validation_metadata = details.get("validation_metadata")
        if not isinstance(validation_metadata, Mapping):
            continue
        if str(validation_metadata.get("validator_binding_id") or "") != binding_id:
            continue
        target = validation_metadata.get("target")
        if _validator_finding_target_matches(target, match):
            return True
    return False


def _validator_finding_target_matches(
    target: Any,
    match: ValidatorBindingMatch,
) -> bool:
    if not isinstance(target, Mapping):
        return False

    match_target = match.target_details()
    for key in ("object_id", "pending_ref_id", "field_path"):
        expected = match_target.get(key)
        if expected is not None and target.get(key) != expected:
            return False
    expected_object_type = match_target.get("object_type")
    if expected_object_type is not None and target.get("object_type") != expected_object_type:
        return False
    return True


async def _execute_validation_groups_for_step(
    *,
    flow: CurationFlow,
    candidate: ExtractionEnvelopeCandidate | None,
    node_data: Mapping[str, Any],
    document_id: Optional[str],
    user_id: Optional[str],
    session_id: Optional[str],
    flow_run_id: Optional[str],
    agent_context: Mapping[str, Any],
    flow_conversation_summary: str,
) -> dict[str, Any]:
    groups = _validation_groups_from_node_data(node_data)
    grouped = _groups_by_state(groups)
    executable_groups = (
        grouped.get("automatic", [])
        + grouped.get("replaced", [])
        + grouped.get("supplemental", [])
    )
    if not groups:
        return {}

    timing_started_at = time.monotonic()
    phase_timings_ms: dict[str, int] = {}
    group_counts_by_state = {
        state: len(state_groups)
        for state, state_groups in sorted(grouped.items())
    }

    def _emit_validation_group_timing(
        *,
        status: str,
        error: str | None = None,
        extra_details: Mapping[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {
            "flowId": str(flow.id),
            "flowName": flow.name,
            "flowRunId": flow_run_id,
            "status": status,
            "totalDurationMs": _elapsed_ms(timing_started_at),
            "phaseTimingsMs": dict(phase_timings_ms),
            "groupCount": len(groups),
            "executableGroupCount": len(executable_groups),
            "groupCountsByState": group_counts_by_state,
            "groups": [
                {
                    "groupId": group.get("group_id"),
                    "state": group.get("state"),
                    "validatorBindingId": group.get("binding_id"),
                    "required": group.get("required"),
                    "blocking": group.get("blocking"),
                }
                for group in groups
            ],
        }
        if error:
            details["error"] = error
        if extra_details:
            details.update(dict(extra_details))
        _emit_flow_runtime_event(
            {
                "type": "FLOW_VALIDATION_GROUP_TIMING",
                "timestamp": _now_iso(),
                "details": details,
            }
        )

    result_metadata: list[dict[str, Any]] = []
    if candidate is None:
        if executable_groups:
            error = "Validation groups require a structured extraction envelope candidate."
            _emit_validation_group_timing(status="error", error=error)
            raise RuntimeError(error)
        _emit_validation_group_timing(status="skipped", extra_details={"reason": "no_candidate"})
        return {"validation_group_results": {"groups": result_metadata}}
    if not executable_groups and not grouped.get("skipped"):
        _emit_validation_group_timing(
            status="skipped",
            extra_details={"reason": "no_executable_groups"},
        )
        return {"validation_group_results": {"groups": result_metadata}}
    if not document_id or not user_id or not session_id:
        error = (
            "Validation groups require document_id, user_id, and session_id so the "
            "source envelope revision can be persisted."
        )
        _emit_validation_group_timing(status="error", error=error)
        raise RuntimeError(error)

    persist_started_at = time.monotonic()
    persisted_records = _persist_flow_extraction_candidates(
        candidates=[candidate],
        document_id=document_id,
        user_id=str(user_id),
        session_id=session_id,
        trace_id=get_current_trace_id(),
        flow_run_id=flow_run_id,
    )
    phase_timings_ms["persist_candidates_ms"] = _elapsed_ms(persist_started_at)
    if not persisted_records:
        error = "Validation groups could not persist the source envelope."
        _emit_validation_group_timing(status="error", error=error)
        raise RuntimeError(error)

    materialization_started_at = time.monotonic()
    source_ref = ensure_domain_envelope_materialization(
        persisted_records[0],
        persist=True,
    )
    phase_timings_ms["ensure_materialization_ms"] = _elapsed_ms(
        materialization_started_at
    )

    session = SessionLocal()
    try:
        source_load_started_at = time.monotonic()
        envelope_row = session.get(DomainEnvelopeModel, source_ref.envelope_id)
        if envelope_row is None:
            error = f"Persisted domain envelope {source_ref.envelope_id} was not found."
            _emit_validation_group_timing(status="error", error=error)
            raise RuntimeError(error)
        source_envelope_revision = int(envelope_row.revision)
        source_envelope = DomainEnvelope.model_validate(envelope_row.envelope_json)
        domain_pack = resolve_curation_domain_pack_by_id(source_envelope.domain_pack_id)
        if domain_pack is None:
            error = (
                "No domain pack is registered for "
                f"domain_pack_id={source_envelope.domain_pack_id!r}."
            )
            _emit_validation_group_timing(status="error", error=error)
            raise RuntimeError(error)
        registry = DomainPackValidationRegistry.from_domain_pack(domain_pack)
        phase_timings_ms["load_source_envelope_ms"] = _elapsed_ms(
            source_load_started_at
        )

        collect_started_at = time.monotonic()
        materialization_inputs, selector_findings, executable_metadata = (
            await _collect_flow_validator_materialization_inputs(
                source_envelope=source_envelope,
                source_envelope_revision=source_envelope_revision,
                registry=registry,
                groups=groups,
                flow=flow,
                agent_context=agent_context,
            )
        )
        phase_timings_ms["collect_materialization_inputs_ms"] = _elapsed_ms(
            collect_started_at
        )
        result_metadata.extend(executable_metadata)

        working_envelope = source_envelope
        appended_findings: list[ValidationFinding] = []
        if selector_findings:
            selector_started_at = time.monotonic()
            working_envelope, selector_appended = append_validation_findings_to_envelope(
                working_envelope,
                selector_findings,
                actor_id="flow_validator_group",
            )
            appended_findings.extend(selector_appended)
            phase_timings_ms["append_selector_findings_ms"] = _elapsed_ms(
                selector_started_at
            )
        if materialization_inputs:
            result_materialization_started_at = time.monotonic()
            materialization_result = materialize_validator_results_into_envelope(
                working_envelope,
                domain_pack.metadata,
                materialization_inputs,
                actor_id="flow_validator_group",
                source_envelope_revision=source_envelope_revision,
            )
            working_envelope = materialization_result.envelope
            appended_findings.extend(materialization_result.appended_findings)
            phase_timings_ms["materialize_validator_results_ms"] = _elapsed_ms(
                result_materialization_started_at
            )

        materialized_revision = source_envelope_revision
        if appended_findings:
            checkpoint_started_at = time.monotonic()
            checkpoint = write_domain_envelope_checkpoint(
                session,
                DomainEnvelopeCheckpointRequest(
                    project_key=envelope_row.project_key,
                    envelope=working_envelope,
                    expected_revision=source_envelope_revision,
                    document_id=envelope_row.document_id,
                    session_id=envelope_row.session_id,
                    flow_run_id=envelope_row.flow_run_id,
                    object_model_ref_json=envelope_row.object_model_ref_json or {},
                    model_field_ref_json=envelope_row.model_field_ref_json or {},
                ),
            )
            materialized_revision = checkpoint.revision
            phase_timings_ms["checkpoint_write_ms"] = _elapsed_ms(
                checkpoint_started_at
            )

        _emit_validation_group_timing(
            status="success",
            extra_details={
                "sourceEnvelopeId": source_envelope.envelope_id,
                "sourceEnvelopeRevision": source_envelope_revision,
                "materializedEnvelopeRevision": materialized_revision,
                "materializationInputCount": len(materialization_inputs),
                "selectorFindingCount": len(selector_findings),
                "appendedFindingCount": len(appended_findings),
                "resultGroupCount": len(result_metadata),
            },
        )
        return {
            "validation_group_results": {
                "source_envelope_id": source_envelope.envelope_id,
                "source_envelope_revision": source_envelope_revision,
                "materialized_envelope_revision": materialized_revision,
                "appended_finding_count": len(appended_findings),
                "groups": sorted(
                    result_metadata,
                    key=lambda item: (
                        str(item.get("validator_binding_id") or ""),
                        str(item.get("group_id") or ""),
                        str(item.get("request_id") or ""),
                    ),
                ),
                "conversation_summary": flow_conversation_summary,
            }
        }
    finally:
        session.close()


def _build_step_evidence_counts(
    completed_steps: list[dict[str, Any]],
) -> dict[str, int]:
    """Derive step evidence counts from completed-step entries."""

    step_counts: dict[str, int] = {}
    for step in completed_steps:
        raw_step_number = step.get("step")
        if raw_step_number is None:
            continue
        try:
            step_number = int(raw_step_number)
        except (TypeError, ValueError):
            continue
        try:
            evidence_count = int(step.get("evidence_count") or 0)
        except (TypeError, ValueError):
            evidence_count = 0
        step_counts[str(step_number)] = max(evidence_count, 0)
    return step_counts


def _build_step_evidence_preview(
    evidence_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the bounded step-evidence preview included in SSE events."""

    return list(evidence_records[:_FLOW_STEP_EVIDENCE_PREVIEW_LIMIT])


def _flow_step_candidate_expected_sources(
    *,
    curation_adapter_key: str | None,
    entry: Optional[dict[str, Any]],
) -> list[str]:
    """Return evidence sources showing a step is expected to produce extraction output."""

    if not curation_adapter_key:
        return []
    if _is_output_formatter_entry(entry):
        return []
    return ["catalog_curation_metadata"]


def _flow_candidate_reject_reason(
    *,
    candidate: ExtractionEnvelopeCandidate | None,
    candidate_expected: bool,
    used_internal_extraction_payload: bool,
    adapter_key_resolved: bool,
    evidence_count: int,
) -> str | None:
    """Explain the coarse handoff outcome without inspecting full payload values."""

    if candidate is not None:
        if candidate_expected and not adapter_key_resolved:
            return "missing_adapter_key"
        if candidate_expected and evidence_count <= 0:
            return "evidence_records_empty"
        return None
    if not candidate_expected:
        return "candidate_not_expected"
    if not used_internal_extraction_payload:
        return "internal_payload_missing"
    if not adapter_key_resolved:
        return "missing_adapter_key"
    return "payload_not_extraction_envelope_or_rejected"


def _build_flow_extraction_handoff_audit_event(
    *,
    flow: CurationFlow,
    flow_run_id: Optional[str],
    completed_step: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    """Build the public flow event for a completed step handoff audit."""

    audit = completed_step.get("extraction_handoff_audit")
    if not isinstance(audit, Mapping):
        return None
    return {
        "type": FLOW_EXTRACTION_HANDOFF_AUDIT_EVENT,
        "timestamp": _now_iso(),
        "data": {
            "flow_id": str(flow.id),
            "flow_name": flow.name,
            "flow_run_id": flow_run_id,
            **dict(audit),
        },
    }


def _build_flow_extraction_handoff_audits(
    completed_steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return compact handoff audits for final flow status payloads."""

    audits: list[dict[str, Any]] = []
    for step in completed_steps:
        audit = step.get("extraction_handoff_audit")
        if isinstance(audit, Mapping):
            audits.append(dict(audit))
    return audits


def _flow_extraction_output_expected(
    completed_steps: list[dict[str, Any]],
) -> bool:
    """Return whether any completed step was expected to produce extraction output."""

    for step in completed_steps:
        audit = step.get("extraction_handoff_audit")
        if isinstance(audit, Mapping) and audit.get("candidateExpected") is True:
            return True
    return False


def _flow_expected_extraction_handoff_failures(
    completed_steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return fail-closed diagnostics for expected extractor steps with no output."""

    failures: list[dict[str, Any]] = []
    for step in completed_steps:
        audit = step.get("extraction_handoff_audit")
        if not isinstance(audit, Mapping) or audit.get("candidateExpected") is not True:
            continue

        try:
            evidence_count = int(audit.get("evidenceCount") or 0)
        except (TypeError, ValueError):
            evidence_count = 0

        reason = None
        if audit.get("candidateBuilt") is not True:
            reason = str(audit.get("candidateRejectReason") or "no_extraction_candidate")
        elif audit.get("adapterKeyResolved") is not True:
            reason = "missing_adapter_key"
        elif evidence_count <= 0:
            reason = "evidence_records_empty"

        if reason is None:
            continue

        failures.append(
            {
                "step": audit.get("step"),
                "toolName": audit.get("toolName"),
                "agentId": audit.get("agentId"),
                "agentName": audit.get("agentName"),
                "reason": reason,
                "candidateBuilt": audit.get("candidateBuilt"),
                "candidateRejectReason": audit.get("candidateRejectReason"),
                "adapterKeyResolved": audit.get("adapterKeyResolved"),
                "evidenceCount": evidence_count,
                "internalPayloadFound": audit.get("internalPayloadFound"),
                "internalEventFoundByFlow": audit.get("internalEventFoundByFlow"),
            }
        )
    return failures


def _flow_expected_extraction_output_error_event(
    *,
    flow_name: str,
    failures: list[dict[str, Any]],
    completed_steps: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Build a FLOW_ERROR when expected extraction output is absent."""

    failure_bits = []
    for failure in failures:
        step = failure.get("step")
        tool_name = failure.get("toolName") or "unknown tool"
        reason = failure.get("reason") or "unknown"
        failure_bits.append(f"step {step} ({tool_name}): {reason}")
    failure_summary = "; ".join(failure_bits) or "unknown extraction handoff failure"
    failure_reason = (
        f"Flow '{flow_name}' did not produce required extraction output for "
        f"expected curation step(s): {failure_summary}."
    )
    return (
        failure_reason,
        {
            "type": "FLOW_ERROR",
            "timestamp": _now_iso(),
            "details": {
                "reason": "missing_expected_extraction_output",
                "message": failure_reason,
                "extraction_handoff_failures": failures,
                "extraction_handoff_audits": _build_flow_extraction_handoff_audits(
                    completed_steps
                ),
            },
        },
    )


def _attach_extraction_handoff_audits_to_flow_error(
    flow_error_event: Dict[str, Any],
    completed_steps: list[dict[str, Any]],
) -> Dict[str, Any]:
    """Attach final handoff audits to a FLOW_ERROR event when available."""

    details = flow_error_event.setdefault("details", {})
    if isinstance(details, dict):
        details.setdefault(
            "extraction_handoff_audits",
            _build_flow_extraction_handoff_audits(completed_steps),
        )
    return flow_error_event


def _apply_persisted_result_counts_to_handoff_audits(
    completed_steps: list[dict[str, Any]],
    records: list[CurationExtractionResultRecord],
    *,
    persistence_status: str = "success",
    persistence_error_reason: str | None = None,
) -> None:
    """Attach final persistence counts to completed-step handoff audits."""

    persisted_by_key: dict[str, int] = {}
    for record in records:
        key = _flow_record_persistence_key(record)
        if key:
            persisted_by_key[key] = persisted_by_key.get(key, 0) + 1

    for step in completed_steps:
        audit = step.get("extraction_handoff_audit")
        if not isinstance(audit, dict):
            continue
        audit["persistenceAttempted"] = True
        audit["persistenceStatus"] = persistence_status
        if persistence_error_reason:
            audit["persistenceErrorReason"] = persistence_error_reason
        candidate = step.get("candidate")
        if not isinstance(candidate, ExtractionEnvelopeCandidate):
            audit["persistedResultCount"] = 0
            continue
        flow_step_key = _flow_candidate_persistence_key(candidate)
        audit["persistedResultCount"] = persisted_by_key.get(flow_step_key, 0)


def _build_flow_validator_lookup_audit_events(
    completed_step: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return synthetic lookup events for automatic flow validator groups."""

    validation_results = completed_step.get("validation_group_results")
    if not isinstance(validation_results, Mapping):
        return []
    groups = validation_results.get("groups")
    if not isinstance(groups, list):
        return []

    events: list[dict[str, Any]] = []
    agent_name = str(
        completed_step.get("agent_name")
        or completed_step.get("agent_id")
        or "Flow validation"
    )
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        binding_id = str(group.get("validator_binding_id") or "").strip()
        lookup_attempts = group.get("lookup_attempts")
        if not binding_id or not isinstance(lookup_attempts, list):
            continue
        status = str(group.get("status") or "").strip() or "unknown"
        for index, attempt in enumerate(lookup_attempts, start=1):
            if not isinstance(attempt, Mapping):
                continue
            provider = str(attempt.get("provider") or "validator").strip()
            method = str(attempt.get("method") or "validator_lookup").strip()
            raw_query = attempt.get("query")
            query = dict(raw_query) if isinstance(raw_query, Mapping) else {}
            outcome = str(attempt.get("outcome") or "unknown").strip()
            try:
                result_count = int(attempt.get("result_count") or 0)
            except (TypeError, ValueError):
                result_count = 0
            friendly_provider = provider or "validator"
            friendly_method = method or "validator_lookup"
            tool_args = {
                "provider": friendly_provider,
                "method": friendly_method,
                **dict(query),
            }
            events.append(
                {
                    "type": "TOOL_START",
                    "timestamp": _now_iso(),
                    "details": {
                        "agent": agent_name,
                        "friendlyName": (
                            f"{agent_name}: Validator Lookup "
                            f"({binding_id}, {friendly_provider}.{friendly_method})"
                        ),
                        "isSpecialistInternal": True,
                        "lookupIndex": index,
                        "toolArgs": tool_args,
                        "toolName": "domain_validator_lookup",
                        "validatorBindingId": binding_id,
                        "validatorResultStatus": status,
                        "source": "flow_validation_group",
                    },
                }
            )
            events.append(
                {
                    "type": "TOOL_COMPLETE",
                    "timestamp": _now_iso(),
                    "details": {
                        "error": attempt.get("message") if outcome == "error" else None,
                        "friendlyName": (
                            f"{agent_name}: Validator Lookup "
                            f"{outcome or 'complete'}"
                        ),
                        "isSpecialistInternal": True,
                        "lookupIndex": index,
                        "outcome": outcome,
                        "resultCount": result_count,
                        "success": outcome != "error",
                        "toolName": "domain_validator_lookup",
                        "validatorBindingId": binding_id,
                        "validatorResultStatus": status,
                        "source": "flow_validation_group",
                    },
                }
            )
    return events


def _build_completed_step_adapter_keys(
    completed_steps: list[dict[str, Any]],
) -> list[str]:
    """Return distinct adapter keys represented by persisted flow candidates."""

    adapter_keys: list[Optional[str]] = []
    for step in completed_steps:
        candidate = step.get("candidate")
        if isinstance(candidate, ExtractionEnvelopeCandidate):
            adapter_keys.append(_resolve_flow_candidate_adapter_key(candidate))
    return _unique_non_empty_scope_values(adapter_keys)


def _resolve_flow_agent_entry(
    agent_id: str,
    *,
    db_user_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve agent_id to execution metadata from unified agent records."""
    try:
        metadata_kwargs: Dict[str, Any] = {}
        if db_user_id is not None:
            metadata_kwargs["db_user_id"] = db_user_id
        metadata = get_agent_metadata(agent_id, **metadata_kwargs)
    except ValueError:
        return None

    return {
        "name": metadata.get("display_name", agent_id),
        "description": metadata.get("description") or "",
        "category": metadata.get("category") or "",
        "subcategory": metadata.get("subcategory") or "",
        "requires_document": metadata.get("requires_document", False),
        "required_params": metadata.get("required_params", []),
        "curation": metadata.get("curation"),
    }


def is_agent_in_flow(flow: CurationFlow, agent_id: str) -> bool:
    """Check if an agent is part of a flow's step sequence.

    Used to restrict which tools are enabled during flow execution.
    Only agents explicitly in the flow can have their tools called.

    Args:
        flow: The CurationFlow object containing flow_definition
        agent_id: The agent ID to check (e.g., "gene", "disease")

    Returns:
        True if the agent is in the flow, False otherwise
    """
    flow_def = flow.flow_definition
    nodes = flow_def.get("nodes", [])
    for node in nodes:
        node_data = node.get("data", {})
        if node_data.get("agent_id") == agent_id:
            return True
    return False


def get_flow_agent_ids(flow: CurationFlow) -> Set[str]:
    """Get the set of agent IDs used in a flow.

    Excludes task_input nodes since they are not executable agents.

    Args:
        flow: The CurationFlow object containing flow_definition

    Returns:
        Set of agent IDs (e.g., {"gene", "disease", "allele"})
    """
    agent_ids = set()
    for node in flow.flow_definition.get("nodes", []):
        agent_id = node.get("data", {}).get("agent_id")
        node_type = node.get("type", "agent")
        # Skip task_input nodes - they're not agents
        if agent_id and node_type != "task_input" and agent_id != "task_input":
            agent_ids.add(agent_id)
    return agent_ids


def get_task_instructions(flow: CurationFlow) -> Optional[str]:
    """Extract task_instructions from the task_input node in a flow.

    The task_input node contains the curator's initial task/request that
    provides context for the entire flow.

    Args:
        flow: The CurationFlow object containing flow_definition

    Returns:
        task_instructions string if found, None otherwise
    """
    for node in flow.flow_definition.get("nodes", []):
        node_type = node.get("type", "agent")
        agent_id = node.get("data", {}).get("agent_id")
        if node_type == "task_input" or agent_id == "task_input":
            return node.get("data", {}).get("task_instructions")
    return None


def _get_ordered_executable_nodes(flow: CurationFlow) -> List[Dict[str, Any]]:
    """Return executable flow nodes in edge-traversal order.

    Uses entry_node_id + edges when available, and appends disconnected
    executable nodes at the end so no configured step is silently ignored.
    """
    flow_def = flow.flow_definition or {}
    nodes: List[Dict[str, Any]] = flow_def.get("nodes", []) or []
    edges: List[Dict[str, Any]] = flow_def.get("edges", []) or []
    entry_node_id = flow_def.get("entry_node_id")

    node_by_id = {n.get("id"): n for n in nodes if n.get("id")}
    if not node_by_id:
        return []

    edges_from: Dict[str, List[str]] = {}
    incoming_targets: Set[str] = set()
    validation_attachment_targets: Set[str] = set()
    for edge in edges:
        edge_role = edge.get("role", DEFAULT_FLOW_EDGE_ROLE)
        source = edge.get("source")
        target = edge.get("target")
        if not source or not target:
            continue
        if edge_role == VALIDATION_ATTACHMENT_EDGE_ROLE:
            validation_attachment_targets.add(target)
            continue
        edges_from.setdefault(source, []).append(target)
        incoming_targets.add(target)

    if entry_node_id and entry_node_id in node_by_id:
        start_node_id = entry_node_id
    else:
        potential_starts = [n.get("id") for n in nodes if n.get("id") not in incoming_targets]
        start_node_id = potential_starts[0] if potential_starts else nodes[0].get("id")

    ordered: List[Dict[str, Any]] = []
    visited: Set[str] = set()
    queue: List[str] = [start_node_id] if start_node_id else []

    def _is_executable(node: Dict[str, Any]) -> bool:
        node_type = node.get("type", "agent")
        agent_id = node.get("data", {}).get("agent_id")
        node_id = node.get("id")
        return (
            node_id not in validation_attachment_targets
            and node_type != "task_input"
            and agent_id not in ("task_input", "supervisor")
        )

    while queue:
        node_id = queue.pop(0)
        if node_id in visited:
            continue
        visited.add(node_id)
        node = node_by_id.get(node_id)
        if node and _is_executable(node):
            ordered.append(node)
        for next_id in edges_from.get(node_id, []):
            if next_id not in visited:
                queue.append(next_id)

    # Append any disconnected executable nodes to preserve configured steps.
    for node in nodes:
        node_id = node.get("id")
        if node_id not in visited and _is_executable(node):
            ordered.append(node)

    return ordered


def _count_agent_ids(flow: CurationFlow) -> Dict[str, int]:
    """Count occurrences of each agent_id in the flow (excluding task_input).

    Used to detect duplicate agent usage so tools can be named uniquely
    per step (e.g., ask_gene_step1_specialist, ask_gene_step3_specialist).

    Args:
        flow: The CurationFlow object containing flow_definition

    Returns:
        Dict mapping agent_id to occurrence count.
    """
    counts: Dict[str, int] = {}
    for node in flow.flow_definition.get("nodes", []):
        node_type = node.get("type", "agent")
        agent_id = node.get("data", {}).get("agent_id")
        if node_type == "task_input" or agent_id == "task_input" or not agent_id:
            continue
        counts[agent_id] = counts.get(agent_id, 0) + 1
    return counts


def flow_requires_document(
    flow: CurationFlow,
    *,
    db_user_id: Optional[int] = None,
) -> bool:
    """Check if any agent in the flow requires a document.

    Used to determine whether to include document guidance in supervisor
    instructions. Only adds document awareness when the flow actually has
    document-requiring agents (like PDF Specialist).

    Args:
        flow: The CurationFlow object containing flow_definition

    Returns:
        True if any agent in the flow requires a document, False otherwise
    """
    for agent_id in get_flow_agent_ids(flow):
        entry = _resolve_flow_agent_entry(agent_id, db_user_id=db_user_id)
        if entry and entry.get("requires_document", False):
            return True
    return False


def get_all_agent_tools(
    flow: CurationFlow,
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    flow_run_id: Optional[str] = None,
    user_query: Optional[str] = None,
    db_user_id: Optional[int] = None,
    document_name: Optional[str] = None,
    active_groups: Optional[List[str]] = None,
    doc_context: Optional[DocumentContext] = None,
    include_unavailable: bool = False,
) -> Any:
    """Get streaming-wrapped tools for agents in the flow.

    Creates one tool per flow node (not per unique agent_id). This means
    if the same agent appears in multiple steps, each step gets its own
    agent instance with its own custom_instructions. Duplicate agent_ids
    get step-numbered tool names (e.g., ask_gene_step1_specialist).

    Uses _create_streaming_tool() to wrap each agent, which captures internal
    tool calls via run_specialist_with_events() and emits events for the audit
    panel and PDF highlighting. This is the same pattern used by normal chat.

    Document context (hierarchy, abstract, sections) can be passed in to avoid
    redundant fetches, or will be fetched automatically using DocumentContext
    which leverages the same cache as normal chat.

    Args:
        flow: The curation flow defining which agents are active
        document_id: For document-aware agents
        user_id: For tenant isolation (Cognito subject ID)
        session_id: Session identifier for persisted flow-step context
        flow_run_id: Optional batch/grouping identifier shared across flow executions
        user_query: Optional user-provided flow context for prep-step assembly
        db_user_id: Database user ID for private/project agent visibility checks
        document_name: Optional filename for prompt context
        active_groups: Active group IDs for database agents
        doc_context: Pre-fetched DocumentContext (optimization to avoid re-fetch)

    Returns:
        By default returns (tools, created_tool_names).
        When include_unavailable=True, returns
        (tools, created_tool_names, unavailable_steps, execution_state) where
        unavailable_steps contains skipped steps with reasons for UI warnings.
    """
    nodes = _get_ordered_executable_nodes(flow)
    agent_id_counts = _count_agent_ids(flow)
    all_tools = []
    created_tool_names: Set[str] = set()
    unavailable_steps: List[Dict[str, Any]] = []

    # Use pre-fetched document context if provided, otherwise fetch
    # This optimization matches how chat pre-fetches and passes through
    if doc_context is None and document_id and user_id:
        doc_context = DocumentContext.fetch(document_id, user_id, document_name)
        logger.info(
            f"[Flow Executor] Fetched document context: {doc_context.section_count()} sections, "
            f"abstract={'yes' if doc_context.abstract else 'no'}"
        )
    elif doc_context:
        logger.debug(
            '[Flow Executor] Using pre-fetched document context: %s sections', doc_context.section_count())

    # Build context for agent creation
    # Start with document context if available, then add flow-specific params
    context = {}
    if doc_context:
        context.update(doc_context.to_agent_kwargs())
    else:
        # Fallback for non-document flows
        context["document_id"] = document_id
        context["user_id"] = user_id
    context["active_groups"] = active_groups or []
    if db_user_id is not None:
        context["db_user_id"] = db_user_id

    # Create one tool per node (not per unique agent_id)
    # This ensures each step gets its own agent instance with its own custom_instructions
    step_num = 0
    ordered_tool_names: List[str] = []
    execution_state = {
        "next_tool_index": 0,
        "ordered_tool_names": ordered_tool_names,
        "completed_steps": [],
        "evidence_registry": _EvidenceRegistry(),
        "persisted_extraction_results": [],
    }
    flow_conversation_summary = _build_flow_conversation_summary(flow, user_query)

    def _wrap_with_step_order(
        tool_callable,
        *,
        tool_name: str,
        specialist_label: str,
        agent_id: str,
        agent_name: str,
        step_number: int,
        curation_adapter_key: str | None,
        candidate_expected_from: list[str],
        node_data: dict[str, Any],
    ):
        """Enforce strict flow step ordering at runtime."""

        # Always embed a user-facing specialist label in the wrapper description.
        # Runner-side audit formatting reads tool descriptions to recover custom agent
        # names (ask_ca_<uuid>_specialist) for TOOL_START/TOOL_COMPLETE labels.
        description_override = f"Ask the {specialist_label}"

        @function_tool(name_override=tool_name, description_override=description_override)
        async def _ordered_tool(query: str) -> str:
            step_started_at = time.monotonic()
            phase_timings_ms: dict[str, int] = {}
            next_idx = execution_state["next_tool_index"]
            if next_idx >= len(ordered_tool_names):
                return (
                    "All remaining flow steps are already complete. "
                    "Summarize final output and stop."
                )
            expected_tool = ordered_tool_names[next_idx]
            if tool_name != expected_tool:
                logger.info(
                    "[Flow Executor] Step order blocked tool '%s'; expected '%s' next",
                    tool_name,
                    expected_tool,
                )
                return (
                    f"Flow step order is strict. The next required step tool is "
                    f"'{expected_tool}'. Do not call '{tool_name}' yet."
                )

            template_timestamp = _format_flow_template_timestamp()
            template_variables = _build_flow_builtin_template_variables(
                document_name=document_name,
                flow_run_id=flow_run_id,
                timestamp=template_timestamp,
            )
            resolved_query = _build_flow_step_query(
                flow=flow,
                node_data=node_data,
                step_number=step_number,
                agent_name=agent_name,
                user_query=user_query,
                document_id=document_id,
                document_name=document_name,
            )
            output_filename_descriptor = _resolve_output_filename_descriptor(
                output_filename_template=node_data.get("output_filename_template"),
                template_variables=template_variables,
            )

            # _create_streaming_tool() returns a FunctionTool (not a plain callable).
            # Invoke via on_invoke_tool() so we execute the underlying specialist wrapper.
            output_filename_token = set_current_output_filename_stem(output_filename_descriptor)
            internal_event_cursor = _capture_internal_extraction_event_cursor()
            specialist_started_at = time.monotonic()
            projected_chat_output: str | None = None
            try:
                direct_formatter_result = await _try_project_terminal_flow_output(
                    agent_id=agent_id,
                    completed_steps=execution_state["completed_steps"],
                    flow_name=flow.name,
                    agent_name=agent_name,
                    flow_run_id=flow_run_id,
                    document_id=document_id,
                    projection_plan=(
                        node_data.get("projection_plan")
                        if isinstance(node_data.get("projection_plan"), Mapping)
                        else None
                    ),
                    node_data=node_data,
                    resolved_query=resolved_query,
                    output_filename_descriptor=output_filename_descriptor,
                )
                if direct_formatter_result is not None:
                    result = direct_formatter_result
                    if agent_id in _FLOW_CHAT_FORMATTER_AGENT_IDS:
                        projected_chat_output = direct_formatter_result
                elif hasattr(tool_callable, "on_invoke_tool"):
                    # Newer openai-agents tool invokers may dereference ctx.tool_name.
                    tool_ctx = SimpleNamespace(tool_name=tool_name)
                    result = await tool_callable.on_invoke_tool(
                        tool_ctx,
                        json.dumps({"query": resolved_query}),
                    )
                else:
                    result = await tool_callable(query=resolved_query)
            finally:
                reset_current_output_filename_stem(output_filename_token)
                phase_timings_ms["specialist_tool_invoke_ms"] = _elapsed_ms(
                    specialist_started_at
                )

            internal_payload_started_at = time.monotonic()
            step_result, internal_lookup_audit = (
                _internal_extraction_tool_output_with_audit_since(
                    internal_event_cursor,
                    tool_name=tool_name,
                )
            )
            used_internal_extraction_payload = step_result is not None
            phase_timings_ms["internal_payload_lookup_ms"] = _elapsed_ms(
                internal_payload_started_at
            )
            if not used_internal_extraction_payload:
                step_result = result
            result_text = _stringify_tool_output(step_result)
            validation_schedule = validation_schedule_from_node_data(node_data)
            validation_schedule_metadata = (
                {"validation_schedule": validation_schedule}
                if any(validation_schedule.values())
                else {}
            )
            candidate_started_at = time.monotonic()
            candidate, step_evidence_metadata = (
                build_extraction_envelope_candidate_with_evidence(
                    step_result,
                    agent_key=agent_id,
                    conversation_summary=flow_conversation_summary,
                    adapter_key=curation_adapter_key,
                    metadata={
                        "tool_name": tool_name,
                        "flow_id": str(flow.id),
                        "flow_name": flow.name,
                        "step": step_number,
                        "agent_name": agent_name,
                        **({"document_name": document_name} if document_name else {}),
                        **validation_schedule_metadata,
                    },
                )
            )
            phase_timings_ms["candidate_evidence_build_ms"] = _elapsed_ms(
                candidate_started_at
            )
            evidence_accumulation_started_at = time.monotonic()
            step_evidence = _accumulate_step_evidence(
                execution_state["evidence_registry"],
                step_evidence_metadata.get("evidence_records", []),
            )
            evidence_count = int(step_evidence.get("evidence_count") or 0)
            candidate_expected = bool(candidate_expected_from)
            adapter_key_resolved = (
                bool(_resolve_flow_candidate_adapter_key(candidate))
                if candidate is not None
                else bool(curation_adapter_key)
            )
            candidate_reject_reason = _flow_candidate_reject_reason(
                candidate=candidate,
                candidate_expected=candidate_expected,
                used_internal_extraction_payload=used_internal_extraction_payload,
                adapter_key_resolved=adapter_key_resolved,
                evidence_count=evidence_count,
            )
            extraction_handoff_audit: dict[str, Any] | None = None
            if candidate_expected:
                extraction_handoff_audit = {
                    "step": step_number,
                    "toolName": tool_name,
                    "agentId": agent_id,
                    "agentName": agent_name,
                    "candidateExpected": True,
                    "candidateExpectedFrom": list(candidate_expected_from),
                    "curationAdapterKey": curation_adapter_key,
                    **internal_lookup_audit,
                    "candidateBuilt": candidate is not None,
                    "candidateRejectReason": candidate_reject_reason,
                    "adapterKeyResolved": adapter_key_resolved,
                    "evidenceCount": evidence_count,
                    "persistenceAttempted": False,
                    "persistedResultCount": None,
                }
            phase_timings_ms["evidence_accumulation_ms"] = _elapsed_ms(
                evidence_accumulation_started_at
            )
            validation_started_at = time.monotonic()
            validation_group_metadata = await _execute_validation_groups_for_step(
                flow=flow,
                candidate=candidate,
                node_data=node_data,
                document_id=document_id,
                user_id=user_id,
                session_id=session_id,
                flow_run_id=flow_run_id,
                agent_context=context,
                flow_conversation_summary=flow_conversation_summary,
            )
            phase_timings_ms["validation_groups_ms"] = _elapsed_ms(
                validation_started_at
            )
            state_update_started_at = time.monotonic()
            total_step_duration_ms = _elapsed_ms(step_started_at)
            step_timing = {
                "totalDurationMs": total_step_duration_ms,
                "phaseTimingsMs": dict(phase_timings_ms),
                "usedInternalExtractionPayload": used_internal_extraction_payload,
                "candidateExpected": candidate_expected,
                "candidateExpectedFrom": list(candidate_expected_from),
                "internalEventEmitted": bool(
                    internal_lookup_audit.get("internalEventEmitted")
                ),
                "internalEventFoundByFlow": bool(
                    internal_lookup_audit.get("internalEventFoundByFlow")
                ),
                "internalPayloadFound": bool(
                    internal_lookup_audit.get("internalPayloadFound")
                ),
                "internalPayloadSource": internal_lookup_audit.get(
                    "internalPayloadSource"
                ),
                "builderFinalizationSeen": bool(
                    internal_lookup_audit.get("builderFinalizationSeen")
                ),
                "candidateBuilt": candidate is not None,
                "candidateRejectReason": candidate_reject_reason,
                "adapterKeyResolved": adapter_key_resolved,
                "evidenceCount": evidence_count,
            }
            completed_step = {
                "step": step_number,
                "agent_id": agent_id,
                "agent_name": agent_name,
                "tool_name": tool_name,
                "output": result_text,
                "output_preview": _truncate_tool_output(result_text),
                "candidate": candidate,
                "timing": step_timing,
                **(
                    {"projected_chat_output": projected_chat_output}
                    if projected_chat_output is not None
                    else {}
                ),
                **validation_schedule_metadata,
                **validation_group_metadata,
                **step_evidence,
            }
            if extraction_handoff_audit is not None:
                completed_step["extraction_handoff_audit"] = extraction_handoff_audit
            execution_state["completed_steps"].append(completed_step)
            execution_state["next_tool_index"] = next_idx + 1
            phase_timings_ms["state_update_ms"] = _elapsed_ms(
                state_update_started_at
            )
            total_step_duration_ms = _elapsed_ms(step_started_at)
            step_timing["totalDurationMs"] = total_step_duration_ms
            step_timing["phaseTimingsMs"] = dict(phase_timings_ms)
            _emit_flow_runtime_event(
                {
                    "type": "FLOW_STEP_TIMING",
                    "timestamp": _now_iso(),
                    "details": {
                        "flowId": str(flow.id),
                        "flowName": flow.name,
                        "flowRunId": flow_run_id,
                        "step": step_number,
                        "toolName": tool_name,
                        "agentId": agent_id,
                        "agentName": agent_name,
                        "totalDurationMs": total_step_duration_ms,
                        "phaseTimingsMs": dict(phase_timings_ms),
                        "usedInternalExtractionPayload": (
                            used_internal_extraction_payload
                        ),
                        "candidateExpected": candidate_expected,
                        "candidateExpectedFrom": list(candidate_expected_from),
                        "internalEventEmitted": bool(
                            internal_lookup_audit.get("internalEventEmitted")
                        ),
                        "internalEventFoundByFlow": bool(
                            internal_lookup_audit.get("internalEventFoundByFlow")
                        ),
                        "internalPayloadFound": bool(
                            internal_lookup_audit.get("internalPayloadFound")
                        ),
                        "internalPayloadSource": internal_lookup_audit.get(
                            "internalPayloadSource"
                        ),
                        "builderFinalizationSeen": bool(
                            internal_lookup_audit.get("builderFinalizationSeen")
                        ),
                        "candidateBuilt": candidate is not None,
                        "candidateRejectReason": candidate_reject_reason,
                        "adapterKeyResolved": adapter_key_resolved,
                        "evidenceCount": evidence_count,
                    },
                }
            )
            flow_handoff_audit_event = _build_flow_extraction_handoff_audit_event(
                flow=flow,
                flow_run_id=flow_run_id,
                completed_step=completed_step,
            )
            if flow_handoff_audit_event is not None:
                _emit_flow_runtime_event(flow_handoff_audit_event)
            return result

        return _ordered_tool

    for node in nodes:
        data = node.get("data", {})
        agent_id = data.get("agent_id")

        step_num += 1

        if not agent_id:
            logger.warning("[Flow Executor] Node is missing agent_id, skipping")
            unavailable_steps.append({
                "step": step_num,
                "agent_id": None,
                "agent_name": "Unknown",
                "reason": "missing agent_id in flow node",
            })
            continue

        entry = _resolve_flow_agent_entry(agent_id, db_user_id=db_user_id)
        if not entry:
            logger.warning("[Flow Executor] Agent '%s' in flow but not resolvable, skipping", agent_id)
            unavailable_steps.append({
                "step": step_num,
                "agent_id": agent_id,
                "agent_name": data.get("agent_display_name") or agent_id,
                "reason": "agent could not be resolved from unified registry",
            })
            continue

        # Check if this agent requires document and we don't have one
        if entry.get("requires_document", False) and not document_id:
            logger.warning(
                "[Flow Executor] Agent '%s' requires document but none provided, skipping", agent_id)
            unavailable_steps.append({
                "step": step_num,
                "agent_id": agent_id,
                "agent_name": entry.get("name", agent_id),
                "reason": "agent requires a document, but no document is loaded",
            })
            continue

        # Generate tool name — unique per step when agent_id appears multiple times
        is_duplicate = agent_id_counts.get(agent_id, 0) > 1
        tool_agent_segment = _tool_safe_agent_id(agent_id)
        if is_duplicate:
            tool_name = f"ask_{tool_agent_segment}_step{step_num}_specialist"
            specialist_name = f"{entry.get('name', agent_id)} (Step {step_num})"
            base_tool_description = entry.get("description") or f"Ask the {entry.get('name', agent_id)}"
            tool_description = f"{base_tool_description} (Step {step_num})"
        else:
            tool_name = f"ask_{tool_agent_segment}_specialist"
            specialist_name = entry.get("name", agent_id)
            tool_description = entry.get("description") or f"Ask the {entry.get('name', agent_id)}"

        if agent_id == CURATION_PREP_AGENT_ID:
            def _make_curation_prep_tool(
                *,
                current_step_goal: Optional[str],
                current_custom_instructions: Optional[str],
            ):
                @function_tool(name_override=tool_name, description_override=tool_description)
                async def _curation_prep_tool(query: str) -> str:
                    _ = (current_step_goal, current_custom_instructions, query)
                    if not document_id or not user_id or not session_id:
                        raise RuntimeError(
                            "Curation prep flow steps require document_id, user_id, and session_id."
                        )

                    extraction_results = _build_flow_prep_extraction_results(
                        completed_steps=execution_state["completed_steps"],
                        document_id=document_id,
                        user_id=user_id,
                        session_id=session_id,
                        flow_run_id=flow_run_id,
                        conversation_summary=flow_conversation_summary,
                    )
                    if not extraction_results:
                        raise RuntimeError(
                            "Curation prep flow steps require at least one upstream extraction envelope."
                        )

                    prep_output = await run_curation_prep(
                        extraction_results,
                        scope_confirmation=_build_flow_scope_confirmation(
                            extraction_results,
                            flow_name=flow.name,
                        ),
                        persistence_context=CurationPrepPersistenceContext(
                            document_id=document_id,
                            source_kind=CurationExtractionSourceKind.FLOW,
                            origin_session_id=session_id,
                            trace_id=get_current_trace_id(),
                            flow_run_id=flow_run_id,
                            user_id=user_id,
                            conversation_summary=flow_conversation_summary,
                        ),
                    )
                    return prep_output.model_dump_json()

                return _curation_prep_tool

            raw_streaming_tool = _make_curation_prep_tool(
                current_step_goal=data.get("step_goal"),
                current_custom_instructions=data.get("custom_instructions"),
            )
        elif agent_id == CURATION_HANDOFF_AGENT_ID:
            def _make_curation_handoff_tool(
                *,
                current_step_goal: Optional[str],
                current_custom_instructions: Optional[str],
            ):
                @function_tool(name_override=tool_name, description_override=tool_description)
                async def _curation_handoff_tool(query: str) -> str:
                    _ = (current_step_goal, current_custom_instructions, query)
                    if not document_id or not user_id or not session_id:
                        raise RuntimeError(
                            "Curation handoff flow steps require document_id, user_id, and session_id."
                        )

                    extraction_results = _build_flow_prep_extraction_results(
                        completed_steps=execution_state["completed_steps"],
                        document_id=document_id,
                        user_id=user_id,
                        session_id=session_id,
                        flow_run_id=flow_run_id,
                        conversation_summary=flow_conversation_summary,
                    )
                    if not extraction_results:
                        raise RuntimeError(
                            "Curation handoff flow steps require at least one upstream extraction envelope."
                        )

                    handoff_db = SessionLocal()
                    try:
                        handoff_output = await run_flow_curation_handoff(
                            extraction_results=extraction_results,
                            document_id=document_id,
                            runner_user_id=user_id,
                            flow_run_id=flow_run_id,
                            origin_session_id=session_id,
                            conversation_summary=flow_conversation_summary,
                            db=handoff_db,
                        )
                    finally:
                        handoff_db.close()

                    handoff_state = {
                        "review_session_ids": handoff_output.review_session_ids,
                        "adapter_keys": handoff_output.adapter_keys,
                    }
                    execution_state["curation_handoff"] = handoff_state
                    return json.dumps(handoff_state)

                return _curation_handoff_tool

            raw_streaming_tool = _make_curation_handoff_tool(
                current_step_goal=data.get("step_goal"),
                current_custom_instructions=data.get("custom_instructions"),
            )
        else:
            custom_instr = data.get("custom_instructions")
            include_evidence = _resolve_flow_step_include_evidence(
                entry=entry,
                raw_include_evidence=data.get("include_evidence"),
            )
            step_instruction_prefix = _build_flow_step_instruction_prefix(
                custom_instructions=custom_instr,
                include_evidence=include_evidence,
            )
            agent_kwargs = dict(context)
            if step_instruction_prefix:
                agent_kwargs["additional_runtime_context"] = [step_instruction_prefix]
            try:
                agent = get_agent_by_id(agent_id, **agent_kwargs)
            except Exception as e:
                logger.warning("[Flow Executor] Failed to create agent '%s': %s", agent_id, e)
                unavailable_steps.append({
                    "step": step_num,
                    "agent_id": agent_id,
                    "agent_name": entry.get("name", agent_id),
                    "reason": str(e),
                })
                continue

            if step_instruction_prefix:
                applied_overrides: List[str] = []
                if custom_instr and custom_instr.strip():
                    applied_overrides.append("custom_instructions")
                if include_evidence is not None:
                    applied_overrides.append("include_evidence")
                logger.info(
                    "[Flow Executor] Prepended step-local instructions to agent '%s' step %s (%s)",
                    agent_id,
                    step_num,
                    ", ".join(applied_overrides),
                )

            raw_streaming_tool = _create_streaming_tool(
                agent=agent,
                tool_name=tool_name,
                tool_description=tool_description,
                specialist_name=specialist_name,
            )

        curation = entry.get("curation")
        curation_adapter_key = (
            str(curation.get("adapter_key") or "").strip() or None
            if isinstance(curation, dict)
            else None
        )
        candidate_expected_from = _flow_step_candidate_expected_sources(
            curation_adapter_key=curation_adapter_key,
            entry=entry,
        )

        ordered_tool_names.append(tool_name)
        streaming_tool = _wrap_with_step_order(
            raw_streaming_tool,
            tool_name=tool_name,
            specialist_label=specialist_name,
            agent_id=agent_id,
            agent_name=entry.get("name", agent_id),
            step_number=step_num,
            node_data=data,
            curation_adapter_key=curation_adapter_key,
            candidate_expected_from=candidate_expected_from,
        )

        logger.info('[Flow Executor] Created streaming tool: %s (%s)', tool_name, specialist_name)
        all_tools.append(streaming_tool)
        created_tool_names.add(tool_name)

    logger.info('[Flow Executor] Created %s streaming tools for flow', len(all_tools))
    if include_unavailable:
        return all_tools, created_tool_names, unavailable_steps, execution_state
    return all_tools, created_tool_names
def build_supervisor_instructions(
    flow: CurationFlow,
    has_document: bool = False,
    document_name: Optional[str] = None,
    available_tools: Optional[Set[str]] = None,
) -> str:
    """Build supervisor system instructions that list all flow steps.

    The supervisor sees all steps upfront so it knows the intended sequence.
    Skips task_input nodes since they provide context, not execution steps.

    When a document is loaded for the flow, includes guidance so the supervisor
    knows to use PDF tools without asking the user for a document. This fixes
    flows that lack task_input nodes (where the prompt doesn't mention documents).

    When available_tools is provided, steps whose tools were not created
    (e.g., requires_document but no document, missing unified-agent metadata, or
    agent build error) are marked as [unavailable] and their tool references are
    suppressed. This prevents the supervisor from trying to call non-existent tools.

    Args:
        flow: The CurationFlow containing the flow definition
        has_document: Whether a document is loaded for this flow execution
        document_name: Optional filename for context in the guidance
        available_tools: Set of tool names actually created by get_all_agent_tools().
            When provided, only these tools are referenced. Steps with missing
            tools are marked unavailable. When None (backward compat), all steps
            are assumed available.

    Returns:
        System instructions string for the flow supervisor
    """
    agent_id_counts = _count_agent_ids(flow)
    # entry_node_id = flow.flow_definition.get("entry_node_id")  # Reserved for future edge traversal

    # Build ordered step list from edge traversal order.
    step_descriptions = []
    step_num = 0
    for node in _get_ordered_executable_nodes(flow):
        data = node.get("data", {})
        agent_id = data.get("agent_id")

        step_num += 1
        resolved_entry = _resolve_flow_agent_entry(agent_id) if agent_id else None
        agent_name = data.get("agent_display_name")
        if not agent_name and agent_id:
            agent_name = resolved_entry.get("name") if resolved_entry else None
        agent_name = agent_name or agent_id or "Unknown"
        step_goal = data.get("step_goal", "")

        # Determine tool name for this step (matches get_all_agent_tools naming)
        is_duplicate = agent_id_counts.get(agent_id, 0) > 1
        tool_agent_segment = _tool_safe_agent_id(agent_id or "")
        if is_duplicate:
            tool_ref = f"ask_{tool_agent_segment}_step{step_num}_specialist"
        else:
            tool_ref = f"ask_{tool_agent_segment}_specialist"

        # Check if this step's tool was actually created
        # When available_tools is None (backward compat), assume all steps are available
        step_available = available_tools is None or tool_ref in available_tools

        if not step_available:
            step_desc = f"Step {step_num}: {agent_name} [unavailable - tool not loaded, skip this step]"
            step_descriptions.append(step_desc)
            continue

        # Name the exact tool for every step so the supervisor cannot treat one
        # specialist's broad answer as a substitute for later configured steps.
        step_desc = f"Step {step_num}: {agent_name}"
        if step_goal:
            step_desc += f" - {step_goal}"
        step_desc += f" (use tool: {tool_ref})"
        custom_instr = data.get("custom_instructions")
        if custom_instr and custom_instr.strip():
            step_desc += " [has custom instructions]"
        include_evidence = _resolve_flow_step_include_evidence(
            entry=resolved_entry,
            raw_include_evidence=data.get("include_evidence"),
        )
        if include_evidence is True:
            step_desc += " [includes evidence in output]"
        elif include_evidence is False:
            step_desc += " [excludes evidence from output]"
        validation_schedule = validation_schedule_from_node_data(data)
        scheduled_count = len(validation_schedule["scheduled_validators"])
        opt_out_count = len(validation_schedule["opt_outs"])
        replacement_count = len(validation_schedule["replacement_validators"])
        supplemental_count = len(validation_schedule["supplemental_validators"])
        under_development_count = sum(
            1
            for item in validation_schedule["inactive_metadata"]
            if item.get("state") == "under_development"
        )
        if scheduled_count:
            step_desc += f" [schedule {scheduled_count} validator(s)]"
        if opt_out_count:
            step_desc += f" [validation opt-outs recorded: {opt_out_count}]"
        if replacement_count:
            step_desc += f" [replacement validators: {replacement_count}]"
        if supplemental_count:
            step_desc += f" [supplemental validators: {supplemental_count}]"
        if under_development_count:
            step_desc += (
                f" [under-development validators visible: {under_development_count}]"
            )
        step_descriptions.append(step_desc)

    # Build document guidance if a document is loaded
    # This ensures the supervisor knows a document is available even if the
    # flow lacks a task_input node that mentions the document
    doc_guidance = ""
    if has_document:
        name_hint = f" ('{document_name}')" if document_name else ""
        doc_guidance = f"""
Document Available{name_hint}: A document is loaded for this flow execution.
Use the PDF Specialist tools to read and search the document's content.
Do NOT ask the user to provide a document - one is already available.
"""

    instructions = f"""You are executing the "{flow.name}" curation flow.
{doc_guidance}
Execute these steps in order:
{chr(10).join(step_descriptions)}

Guidelines:
- Step execution order is STRICTLY enforced by runtime tool gating
- Call each available step exactly once, in order
- A step is complete only when its named step tool has been called
- If an earlier specialist discusses later-step topics, still call the later
  step tools; narrative coverage is not a substitute for configured flow steps
- If a step is unavailable, skip it and continue to the next available step
- Do not pass previous step output into later step tool calls; the runtime
  preserves completed artifacts separately for review, export, and handoff
- Treat validation schedules attached to extraction steps as runtime metadata;
  do not ask extractor prompts to call validators directly
- The final step typically produces output (file or response)

COMPLETION: Once the final step produces output (e.g., CSV file saved, response generated),
your task is COMPLETE. Respond with a brief summary of what was produced and stop.
Do NOT start a new cycle through the steps after output is produced.
"""
    return instructions


def build_flow_prompt(
    flow: CurationFlow,
    document_id: Optional[str] = None,
    user_query: Optional[str] = None,
) -> str:
    """Build the initial prompt for flow execution.

    Combines flow context with user query information.
    Includes task_instructions from task_input node if present.

    NOTE: We don't include document_id in the prompt because the PDF agent's
    tools are already created with the document context. Adding it here would
    be redundant and could confuse the agent. This matches how normal chat works.

    Args:
        flow: The CurationFlow to execute
        document_id: Optional document ID (not used in prompt - tools already have it)
        user_query: Optional user-provided context or query

    Returns:
        Initial prompt string for the flow supervisor
    """
    prompt_parts = []

    # Extract task_instructions from task_input node (if present)
    task_instructions = get_task_instructions(flow)
    if task_instructions:
        prompt_parts.append(f"Task Instructions:\n{task_instructions}")

    # NOTE: Don't add document_id to prompt - PDF agent's tools already have document context
    # Adding it here would be redundant and differs from normal chat behavior

    # Add user query if provided (this may override or complement task_instructions)
    if user_query:
        prompt_parts.append(f"User Query: {user_query}")
    elif not task_instructions:
        # Only add default if no task_instructions AND no user_query
        prompt_parts.append(f"Execute the '{flow.name}' curation workflow.")

    # Add step-specific goals as context (skip task_input nodes)
    nodes = _get_ordered_executable_nodes(flow)
    step_goals = []
    step_num = 0
    for node in nodes:
        data = node.get("data", {})

        step_num += 1
        goal = data.get("step_goal")
        if goal:
            step_goals.append(f"- Step {step_num}: {goal}")

    if step_goals:
        prompt_parts.append("\nStep Goals:")
        prompt_parts.extend(step_goals)

    return "\n".join(prompt_parts)


def create_flow_supervisor(
    flow: CurationFlow,
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    flow_run_id: Optional[str] = None,
    user_query: Optional[str] = None,
    db_user_id: Optional[int] = None,
    document_name: Optional[str] = None,
    active_groups: Optional[List[str]] = None,
    doc_context: Optional[DocumentContext] = None,
) -> Agent:
    """Create a supervisor agent configured for flow execution.

    The supervisor has access to all agent tools, but only those
    in the flow have is_enabled=True.

    Args:
        flow: The CurationFlow defining the workflow
        document_id: Optional document for PDF-aware agents
        user_id: Cognito subject ID for Weaviate tenant isolation
        session_id: Session identifier for persisted flow-step context
        flow_run_id: Optional batch/grouping identifier shared across flow executions
        user_query: Optional user-provided flow context for prep-step assembly
        db_user_id: Database user ID for private/project agent visibility checks
        document_name: Optional filename for prompt context
        active_groups: Active group IDs for database queries
        doc_context: Pre-fetched DocumentContext (optimization to avoid re-fetch)

    Returns:
        Configured Agent instance for flow supervision
    """
    # Get supervisor config (model, temperature, reasoning)
    config = get_agent_config("supervisor")
    model_provider = resolve_model_provider(config.model)

    # Build model configuration
    model = get_model_for_agent(config.model, provider_override=model_provider)
    model_settings = build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=config.reasoning,
        provider_override=model_provider,
    )

    # Get all tools with flow-based is_enabled
    # Pass through pre-fetched doc_context to avoid redundant Weaviate queries
    # Returns (tools, created_tool_names) so supervisor instructions only
    # reference tools that were actually created
    tools, created_tool_names, unavailable_steps, execution_state = get_all_agent_tools(
        flow=flow,
        document_id=document_id,
        user_id=user_id,
        session_id=session_id,
        flow_run_id=flow_run_id,
        user_query=user_query,
        db_user_id=db_user_id,
        document_name=document_name,
        active_groups=active_groups,
        doc_context=doc_context,
        include_unavailable=True,
    )

    # Fail fast if no tools could be created — the supervisor would have nothing to call
    if not tools:
        step_count = sum(
            1 for n in flow.flow_definition.get("nodes", [])
            if n.get("type") != "task_input" and n.get("data", {}).get("agent_id") != "task_input"
        )
        raise ValueError(
            f"Flow '{flow.name}' has {step_count} step(s) but no agent tools could be created. "
            f"Check that all agent IDs resolve in the unified agents table and required documents are provided."
        )

    # Determine if document guidance should be included in system instructions
    # Only include when: 1) a document is provided AND 2) the flow has document-requiring agents
    # This prevents confusing the supervisor by mentioning documents when no PDF tools exist
    has_document = bool(document_id) and flow_requires_document(
        flow,
        db_user_id=db_user_id,
    )

    # Build supervisor instructions with document awareness if applicable
    # Pass created_tool_names so instructions only reference tools that exist
    instructions = build_supervisor_instructions(
        flow,
        has_document=has_document,
        document_name=document_name,
        available_tools=created_tool_names,
    )

    # Create flow supervisor agent
    supervisor = Agent(
        name=f"Flow Supervisor: {flow.name}",
        instructions=instructions,
        tools=tools,
        model=model,
        model_settings=model_settings,
    )
    setattr(supervisor, "_flow_unavailable_steps", unavailable_steps)
    setattr(supervisor, "_flow_execution_state", execution_state)

    logger.info(
        f"[Flow Executor] Created flow supervisor for '{flow.name}': "
        f"model={config.model}, streaming_tools={len(tools)}"
    )

    return supervisor


def _persist_flow_extraction_candidates(
    *,
    candidates: List[ExtractionEnvelopeCandidate],
    document_id: Optional[str],
    user_id: str,
    session_id: str,
    trace_id: Optional[str],
    flow_run_id: Optional[str],
) -> list[CurationExtractionResultRecord]:
    """Persist flow-produced extraction envelopes and return stored records."""

    if not candidates or not document_id:
        return []

    normalized_flow_run_id = str(flow_run_id or "").strip() or None
    existing_by_key: dict[str, CurationExtractionResultRecord] = {}
    if normalized_flow_run_id is not None:
        existing_results = list_extraction_results(
            document_id=document_id,
            flow_run_id=normalized_flow_run_id,
            origin_session_id=session_id,
            user_id=user_id,
            source_kind=CurationExtractionSourceKind.FLOW,
        )
        existing_by_key = {
            key: result
            for result in existing_results
            if (key := _flow_record_persistence_key(result))
        }

    ordered_records: list[CurationExtractionResultRecord] = []
    requests: list[CurationExtractionPersistenceRequest] = []
    request_keys: list[str] = []
    for candidate in candidates:
        flow_step_key = _flow_candidate_persistence_key(candidate)
        if flow_step_key in existing_by_key:
            ordered_records.append(existing_by_key[flow_step_key])
            continue

        metadata = dict(candidate.metadata)
        if flow_step_key:
            metadata["flow_step_key"] = flow_step_key
        requests.append(
            CurationExtractionPersistenceRequest(
                document_id=document_id,
                adapter_key=(
                    _resolve_flow_candidate_adapter_key(candidate)
                    or candidate.agent_key
                ),
                agent_key=candidate.agent_key,
                source_kind=CurationExtractionSourceKind.FLOW,
                origin_session_id=session_id,
                trace_id=trace_id,
                flow_run_id=flow_run_id,
                user_id=user_id,
                candidate_count=candidate.candidate_count,
                conversation_summary=candidate.conversation_summary,
                payload_json=candidate.payload_json,
                metadata=metadata,
            )
        )
        request_keys.append(flow_step_key)

    if requests:
        responses = persist_extraction_results(requests)
        persisted_by_key = {
            key: response.extraction_result
            for key, response in zip(request_keys, responses)
        }
        for candidate in candidates:
            flow_step_key = _flow_candidate_persistence_key(candidate)
            if flow_step_key in existing_by_key:
                continue
            persisted = persisted_by_key.get(flow_step_key)
            if persisted is not None:
                ordered_records.append(persisted)

    _materialize_flow_domain_envelope_records(ordered_records)
    return ordered_records


def _materialize_flow_domain_envelope_records(
    records: list[CurationExtractionResultRecord],
) -> None:
    """Ensure flow-persisted domain-envelope extraction records get review rows."""

    for record in records:
        if not _is_flow_domain_envelope_payload(record.payload_json):
            continue
        ensure_domain_envelope_materialization(record, persist=True)


def _is_flow_domain_envelope_payload(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if {"envelope_id", "domain_pack_id", "objects"}.issubset(payload):
        return isinstance(payload.get("objects"), list)
    return isinstance(payload.get("curatable_objects"), list)


def _flow_extraction_result_ref(
    record: CurationExtractionResultRecord,
) -> dict[str, Any]:
    return {
        "extraction_result_id": record.extraction_result_id,
        "adapter_key": record.adapter_key,
        "agent_key": record.agent_key,
        "candidate_count": record.candidate_count,
        "trace_id": record.trace_id,
    }


def _merge_persisted_flow_extraction_results(
    execution_state: dict[str, Any],
    records: list[CurationExtractionResultRecord],
) -> None:
    if not records:
        return

    refs = execution_state.setdefault("persisted_extraction_results", [])
    if not isinstance(refs, list):
        refs = []
        execution_state["persisted_extraction_results"] = refs

    seen = {
        str(ref.get("extraction_result_id") or "").strip()
        for ref in refs
        if isinstance(ref, Mapping)
    }
    for record in records:
        record_id = str(record.extraction_result_id or "").strip()
        if not record_id or record_id in seen:
            continue
        refs.append(_flow_extraction_result_ref(record))
        seen.add(record_id)


def _persist_flow_extraction_candidates_or_build_error(
    *,
    flow_name: str,
    candidates: List[ExtractionEnvelopeCandidate],
    document_id: Optional[str],
    user_id: str,
    session_id: str,
    trace_id: Optional[str],
    flow_run_id: Optional[str],
    extraction_output_required: bool = False,
) -> tuple[bool, Optional[str], Optional[Dict[str, Any]], list[CurationExtractionResultRecord]]:
    """Persist flow extraction candidates and return a FLOW_ERROR payload on failure."""

    if extraction_output_required:
        if not document_id:
            failure_reason = (
                f"Flow '{flow_name}' could not persist required extraction output "
                "because no document_id was provided."
            )
            return (
                False,
                failure_reason,
                {
                    "type": "FLOW_ERROR",
                    "timestamp": _now_iso(),
                    "details": {
                        "reason": "missing_document_id_for_extraction_persistence",
                        "message": failure_reason,
                    },
                },
                [],
            )
        if not candidates:
            failure_reason = (
                f"Flow '{flow_name}' expected curation extraction output, but no "
                "persistable extraction candidates were produced."
            )
            return (
                False,
                failure_reason,
                {
                    "type": "FLOW_ERROR",
                    "timestamp": _now_iso(),
                    "details": {
                        "reason": "no_extraction_candidates",
                        "message": failure_reason,
                    },
                },
                [],
            )

    missing_adapter_candidates = [
        candidate
        for candidate in candidates
        if not _resolve_flow_candidate_adapter_key(candidate)
    ]
    if missing_adapter_candidates:
        missing_agents = _unique_non_empty_scope_values(
            [candidate.agent_key for candidate in missing_adapter_candidates]
        )
        failure_reason = (
            f"Flow '{flow_name}' produced extraction candidates without adapter keys: "
            f"{', '.join(missing_agents) or 'unknown agent'}."
        )
        return (
            False,
            failure_reason,
            {
                "type": "FLOW_ERROR",
                "timestamp": _now_iso(),
                "details": {
                    "reason": "missing_adapter_key",
                    "message": failure_reason,
                    "agent_keys": missing_agents,
                },
            },
            [],
        )

    try:
        persisted_records = _persist_flow_extraction_candidates(
            candidates=candidates,
            document_id=document_id,
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            flow_run_id=flow_run_id,
        )
    except Exception as exc:
        failure_reason = f"Failed to persist extraction results for flow '{flow_name}'. {exc}"
        logger.exception(
            "[Flow Executor] Extraction persistence failed for flow '%s'",
            flow_name,
            extra={
                "document_id": document_id,
                "session_id": session_id,
                "trace_id": trace_id,
            },
        )
        return (
            False,
            failure_reason,
            {
                "type": "FLOW_ERROR",
                "timestamp": _now_iso(),
                "details": {
                    "reason": "extraction_persistence_failed",
                    "message": failure_reason,
                },
            },
            [],
        )

    if extraction_output_required and not persisted_records:
        failure_reason = (
            f"Flow '{flow_name}' expected persisted extraction results, but "
            "persistence returned no records."
        )
        return (
            False,
            failure_reason,
            {
                "type": "FLOW_ERROR",
                "timestamp": _now_iso(),
                "details": {
                    "reason": "extraction_persistence_empty_result",
                    "message": failure_reason,
                },
            },
            [],
        )
    if extraction_output_required and len(persisted_records) < len(candidates):
        failure_reason = (
            f"Flow '{flow_name}' persisted only {len(persisted_records)} of "
            f"{len(candidates)} required extraction candidate(s)."
        )
        return (
            False,
            failure_reason,
            {
                "type": "FLOW_ERROR",
                "timestamp": _now_iso(),
                "details": {
                    "reason": "extraction_persistence_partial_result",
                    "message": failure_reason,
                    "persisted_count": len(persisted_records),
                    "candidate_count": len(candidates),
                },
            },
            persisted_records,
        )

    return True, None, None, persisted_records


def _missing_required_flow_steps(
    execution_state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return required step tools that the supervisor did not call."""

    if not isinstance(execution_state, Mapping):
        return []

    ordered_tool_names = execution_state.get("ordered_tool_names")
    if not isinstance(ordered_tool_names, list):
        return []

    completed_tool_names = {
        str(step.get("tool_name") or "").strip()
        for step in execution_state.get("completed_steps") or []
        if isinstance(step, Mapping)
    }

    missing: list[dict[str, Any]] = []
    for index, tool_name in enumerate(ordered_tool_names, start=1):
        normalized_tool_name = str(tool_name or "").strip()
        if normalized_tool_name and normalized_tool_name not in completed_tool_names:
            missing.append({"step": index, "tool_name": normalized_tool_name})
    return missing


def _flow_incomplete_error_event(
    *,
    flow_name: str,
    missing_steps: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Build the failure reason and SSE event for an incomplete flow."""

    missing_text = ", ".join(
        f"step {step['step']} ({step['tool_name']})"
        for step in missing_steps
    )
    failure_reason = (
        f"Flow '{flow_name}' ended before all required steps ran. "
        f"Missing: {missing_text}."
    )
    return (
        failure_reason,
        {
            "type": "FLOW_ERROR",
            "timestamp": _now_iso(),
            "details": {
                "reason": "incomplete_flow_steps",
                "message": failure_reason,
                "missing_steps": missing_steps,
            },
        },
    )


async def execute_flow(
    flow: CurationFlow,
    user_id: str,
    session_id: str,
    db_user_id: Optional[int] = None,
    document_id: Optional[str] = None,
    document_name: Optional[str] = None,
    user_query: Optional[str] = None,
    active_groups: Optional[List[str]] = None,
    flow_run_id: Optional[str] = None,
    trace_context: Optional[Dict[str, str]] = None,
) -> AsyncGenerator[dict, None]:
    """Execute a curation flow using the shared streaming infrastructure.

    Delegates to run_agent_streamed() with a custom flow supervisor to get
    the same rich audit events as regular chat (SUPERVISOR_START, AGENT_GENERATING,
    CREW_START, SUPERVISOR_COMPLETE, etc.) plus Langfuse tracing, prompt logging,
    and document metadata caching.

    Args:
        flow: The CurationFlow to execute
        user_id: Cognito subject ID for Weaviate tenant isolation
        session_id: Session ID for tracing (Langfuse)
        db_user_id: Database user ID for private/project agent visibility checks
        document_id: Optional document for PDF-aware agents
        document_name: Optional name of the document for Langfuse metadata
        user_query: Optional user-provided query/context
        active_groups: Active group IDs for database queries
        flow_run_id: Optional batch/grouping identifier shared across flow executions
        trace_context: Optional existing Langfuse trace identifiers to reuse on retry

    Yields:
        dict: Streaming events - FLOW_STARTED, then all regular chat events
              (RUN_STARTED, SUPERVISOR_START, TOOL_START, etc.), then FLOW_FINISHED
    """
    logger.info(
        f"[Flow Executor] Starting flow: '{flow.name}', "
        f"user_id={user_id}, session_id={session_id}"
    )
    flow_run_id = flow_run_id or str(uuid4())

    # Pre-fetch document context BEFORE creating supervisor (optimization)
    # This matches how chat pre-fetches and passes through to avoid redundant Weaviate queries
    # The DocumentContext cache ensures we only hit Weaviate once even if called multiple times
    doc_context = None
    if document_id and user_id:
        doc_context = DocumentContext.fetch(document_id, user_id, document_name)
        logger.info(
            f"[Flow Executor] Pre-fetched document context: {doc_context.section_count()} sections, "
            f"abstract={'yes' if doc_context.abstract else 'no'}"
        )

    # Create flow supervisor with restricted tools
    # Pass pre-fetched doc_context to avoid redundant fetches in get_all_agent_tools
    supervisor = create_flow_supervisor(
        flow=flow,
        document_id=document_id,
        user_id=user_id,
        session_id=session_id,
        flow_run_id=flow_run_id,
        user_query=user_query,
        db_user_id=db_user_id,
        document_name=document_name,
        active_groups=active_groups,
        doc_context=doc_context,
    )

    # Build flow prompt
    prompt = build_flow_prompt(flow, document_id, user_query)

    # Calculate step count for metadata (exclude task_input nodes)
    all_nodes = flow.flow_definition.get("nodes", [])
    total_steps = sum(
        1 for n in all_nodes
        if n.get("type") != "task_input" and n.get("data", {}).get("agent_id") != "task_input"
    )

    # Emit flow-specific FLOW_STARTED (before delegating)
    # This adds flow metadata that run_agent_streamed doesn't know about
    yield {
        "type": "FLOW_STARTED",
        "timestamp": _now_iso(),
        "data": {
            "execution_mode": "flow",
            "flow_id": str(flow.id),
            "flow_name": flow.name,
            "total_steps": total_steps,
            "flow_run_id": flow_run_id,
        }
    }

    # Surface any unavailable flow steps to UI/audit so skipped work is explicit.
    unavailable_steps = getattr(supervisor, "_flow_unavailable_steps", []) or []
    for step in unavailable_steps:
        step_num = step.get("step")
        agent_name = step.get("agent_name", "Unknown Agent")
        reason = step.get("reason", "unknown reason")
        yield {
            "type": "DOMAIN_WARNING",
            "timestamp": _now_iso(),
            "details": {
                "reason": "flow_step_unavailable",
                "message": (
                    f"Flow step {step_num} ({agent_name}) is unavailable and will be skipped: {reason}"
                ),
                "step": step_num,
                "agent_id": step.get("agent_id"),
                "agent_name": agent_name,
                "unavailable_reason": reason,
            }
        }

    # Delegate to run_agent_streamed with flow supervisor
    # This gives us: Langfuse tracing, prompt logging, document metadata,
    # rich events (SUPERVISOR_START, AGENT_GENERATING, CREW_START, etc.)
    # Pass pre-fetched doc_context to avoid redundant Weaviate queries
    from src.lib.openai_agents.runner import run_agent_streamed

    flow_status = "completed"
    failure_reason: Optional[str] = None
    trace_id: Optional[str] = None
    extraction_persisted = False
    curation_handoff_emitted = False
    pending_terminal_output_event: Optional[dict[str, Any]] = None
    flow_execution_state = supervisor._flow_execution_state
    completed_steps = flow_execution_state["completed_steps"]
    evidence_registry = flow_execution_state["evidence_registry"]

    async for event in run_agent_streamed(
        context_messages=[{"role": "user", "content": prompt}],
        user_id=str(user_id),
        session_id=session_id,
        document_id=document_id,
        document_name=document_name,
        active_groups=active_groups,
        agent=supervisor,  # Pass the flow supervisor
        doc_context=doc_context,  # Pass pre-fetched context (optimization)
        trace_context=trace_context,
    ):
        event_type = event.get("type")
        event_data = event.get("data", {}) or {}

        if event_type == INTERNAL_EXTRACTION_RESULT_EVENT_TYPE:
            continue

        if event_type == "RUN_STARTED" and "trace_id" in event_data:
            trace_id = event_data.get("trace_id")

        flow_step_evidence_event: Optional[dict[str, Any]] = None
        flow_validator_audit_events: list[dict[str, Any]] = []
        projected_chat_ready_event: Optional[dict[str, Any]] = None
        if event_type == "TOOL_COMPLETE":
            details = event.get("details", {}) or {}
            tool_name = str(details.get("toolName") or "").strip()
            completed_step = _find_completed_step_by_tool_name(completed_steps, tool_name)
            if completed_step is not None:
                flow_validator_audit_events = (
                    _build_flow_validator_lookup_audit_events(completed_step)
                )
                step_evidence_records = list(completed_step.get("evidence_records") or [])
                step_evidence_preview = _build_step_evidence_preview(step_evidence_records)
                flow_step_evidence_event = {
                    "type": "FLOW_STEP_EVIDENCE",
                    "timestamp": _now_iso(),
                    "data": {
                        "flow_id": str(flow.id),
                        "flow_name": flow.name,
                        "flow_run_id": flow_run_id,
                        "step": completed_step.get("step"),
                        "tool_name": completed_step.get("tool_name"),
                        "agent_id": completed_step.get("agent_id"),
                        "agent_name": completed_step.get("agent_name"),
                        "evidence_records": step_evidence_preview,
                        "evidence_preview": step_evidence_preview,
                        "evidence_count": int(completed_step.get("evidence_count") or 0),
                        "total_evidence_records": len(evidence_registry.records()),
                    },
                }
                projected_chat_output = completed_step.get("projected_chat_output")
                if isinstance(projected_chat_output, str):
                    projected_chat_ready_event = {
                        "type": "CHAT_OUTPUT_READY",
                        "timestamp": _now_iso(),
                        "details": {
                            "output": projected_chat_output,
                            "output_preview": _truncate_tool_output(
                                projected_chat_output,
                                max_chars=200,
                            ),
                            "output_length": len(projected_chat_output),
                        },
                    }
            if (
                pending_terminal_output_event is not None
                and not _missing_required_flow_steps(flow_execution_state)
            ):
                yield event
                for flow_validator_audit_event in flow_validator_audit_events:
                    yield flow_validator_audit_event
                if flow_step_evidence_event is not None:
                    yield flow_step_evidence_event
                event = pending_terminal_output_event
                event_type = str(event.get("type") or "")
                event_data = event.get("data", {}) or {}
                pending_terminal_output_event = None
                flow_validator_audit_events = []
                flow_step_evidence_event = None

        if projected_chat_ready_event is not None:
            yield event
            for flow_validator_audit_event in flow_validator_audit_events:
                yield flow_validator_audit_event
            if flow_step_evidence_event is not None:
                yield flow_step_evidence_event
            event = projected_chat_ready_event
            event_type = "CHAT_OUTPUT_READY"
            event_data = event.get("data", {}) or {}
            flow_validator_audit_events = []
            flow_step_evidence_event = None

        # Terminate flow after output is produced
        # FILE_READY indicates a file output agent (CSV, TSV, JSON) completed
        # CHAT_OUTPUT_READY indicates chat output agent completed
        # This prevents the supervisor from looping back to call agents again
        if event_type == "SPECIALIST_ERROR":
            yield event
            details = event.get("details", {}) or {}
            failure_reason = (
                details.get("error")
                or details.get("message")
                or "A specialist step failed."
            )
            flow_status = "failed"
            logger.error(
                "[Flow Executor] Specialist error in flow '%s': %s",
                flow.name,
                failure_reason,
            )
            yield {
                "type": "FLOW_ERROR",
                "timestamp": _now_iso(),
                "details": {
                    "reason": "specialist_step_failed",
                    "message": (
                        f"Flow '{flow.name}' stopped because a specialist step failed. "
                        f"{failure_reason}"
                    ),
                },
            }
            break
        if event_type == "RUN_ERROR":
            yield event
            failure_reason = (
                event_data.get("message")
                or event_data.get("error")
                or "Flow execution failed."
            )
            flow_status = "failed"
            logger.error(
                "[Flow Executor] Run error in flow '%s': %s",
                flow.name,
                failure_reason,
            )
            yield {
                "type": "FLOW_ERROR",
                "timestamp": _now_iso(),
                "details": {
                    "reason": "run_error",
                    "message": (
                        f"Flow '{flow.name}' failed during execution. {failure_reason}"
                    ),
                },
            }
            break
        if event_type in {"FILE_READY", "CHAT_OUTPUT_READY"}:
            missing_steps = _missing_required_flow_steps(flow_execution_state)
            if missing_steps:
                if event_type == "FILE_READY":
                    pending_terminal_output_event = event
                    logger.info(
                        "[Flow Executor] Deferring terminal FILE_READY for flow '%s' "
                        "until required step state catches up; missing=%s",
                        flow.name,
                        missing_steps,
                    )
                    continue
                failure_reason, flow_error_event = _flow_incomplete_error_event(
                    flow_name=flow.name,
                    missing_steps=missing_steps,
                )
                flow_status = "failed"
                yield flow_error_event
                break

            handoff_failures = _flow_expected_extraction_handoff_failures(completed_steps)
            if handoff_failures:
                failure_reason, flow_error_event = (
                    _flow_expected_extraction_output_error_event(
                        flow_name=flow.name,
                        failures=handoff_failures,
                        completed_steps=completed_steps,
                    )
                )
                flow_status = "failed"
                yield flow_error_event
                break

            extraction_candidates = _collect_completed_step_candidates(completed_steps)
            extraction_output_required = (
                _flow_extraction_output_expected(completed_steps)
                or bool(extraction_candidates)
            )
            persisted, failure_reason, flow_error_event, persisted_records = (
                _persist_flow_extraction_candidates_or_build_error(
                    flow_name=flow.name,
                    candidates=extraction_candidates,
                    document_id=document_id,
                    user_id=str(user_id),
                    session_id=session_id,
                    trace_id=trace_id,
                    flow_run_id=flow_run_id,
                    extraction_output_required=extraction_output_required,
                )
            )
            if not persisted:
                _apply_persisted_result_counts_to_handoff_audits(
                    completed_steps,
                    persisted_records,
                    persistence_status="failed",
                    persistence_error_reason=failure_reason,
                )
                flow_status = "failed"
                if flow_error_event is not None:
                    flow_error_event = _attach_extraction_handoff_audits_to_flow_error(
                        flow_error_event,
                        completed_steps,
                    )
                    yield flow_error_event
                break

            extraction_persisted = True
            _apply_persisted_result_counts_to_handoff_audits(
                completed_steps,
                persisted_records,
                persistence_status="success",
            )
            _merge_persisted_flow_extraction_results(
                flow_execution_state,
                persisted_records,
            )
            yield event
            logger.info(
                "[Flow Executor] %s produced - terminating flow '%s'",
                "Output file" if event_type == "FILE_READY" else "Chat output",
                flow.name,
            )
            break
        yield event
        for flow_validator_audit_event in flow_validator_audit_events:
            yield flow_validator_audit_event
        if flow_step_evidence_event is not None:
            yield flow_step_evidence_event

        curation_handoff_state = flow_execution_state.get("curation_handoff")
        if (
            not curation_handoff_emitted
            and isinstance(curation_handoff_state, Mapping)
        ):
            missing_steps = _missing_required_flow_steps(flow_execution_state)
            if missing_steps:
                failure_reason, flow_error_event = _flow_incomplete_error_event(
                    flow_name=flow.name,
                    missing_steps=missing_steps,
                )
                flow_status = "failed"
                yield flow_error_event
                break

            curation_handoff_emitted = True
            yield {
                "type": CURATION_HANDOFF_READY_EVENT,
                "timestamp": _now_iso(),
                "details": {
                    "review_session_ids": list(
                        curation_handoff_state.get("review_session_ids") or []
                    ),
                    "adapter_keys": list(curation_handoff_state.get("adapter_keys") or []),
                    "document_id": document_id,
                },
            }
            logger.info(
                "[Flow Executor] Curation handoff produced for flow '%s'",
                flow.name,
            )

    if flow_status != "failed":
        missing_steps = _missing_required_flow_steps(flow_execution_state)
        if missing_steps:
            failure_reason, flow_error_event = _flow_incomplete_error_event(
                flow_name=flow.name,
                missing_steps=missing_steps,
            )
            flow_status = "failed"
            yield flow_error_event

    if flow_status != "failed" and not extraction_persisted:
        handoff_failures = _flow_expected_extraction_handoff_failures(completed_steps)
        if handoff_failures:
            failure_reason, flow_error_event = (
                _flow_expected_extraction_output_error_event(
                    flow_name=flow.name,
                    failures=handoff_failures,
                    completed_steps=completed_steps,
                )
            )
            flow_status = "failed"
            yield flow_error_event
        else:
            extraction_candidates = _collect_completed_step_candidates(completed_steps)
            extraction_output_required = (
                _flow_extraction_output_expected(completed_steps)
                or bool(extraction_candidates)
            )
            persisted, failure_reason, flow_error_event, persisted_records = (
                _persist_flow_extraction_candidates_or_build_error(
                    flow_name=flow.name,
                    candidates=extraction_candidates,
                    document_id=document_id,
                    user_id=str(user_id),
                    session_id=session_id,
                    trace_id=trace_id,
                    flow_run_id=flow_run_id,
                    extraction_output_required=extraction_output_required,
                )
            )
            if not persisted:
                _apply_persisted_result_counts_to_handoff_audits(
                    completed_steps,
                    persisted_records,
                    persistence_status="failed",
                    persistence_error_reason=failure_reason,
                )
                flow_status = "failed"
                if flow_error_event is not None:
                    flow_error_event = _attach_extraction_handoff_audits_to_flow_error(
                        flow_error_event,
                        completed_steps,
                    )
                    yield flow_error_event
            else:
                extraction_persisted = True
                _apply_persisted_result_counts_to_handoff_audits(
                    completed_steps,
                    persisted_records,
                    persistence_status="success",
                )
                _merge_persisted_flow_extraction_results(
                    flow_execution_state,
                    persisted_records,
                )

    # Emit flow-specific completion event
    yield {
        "type": "FLOW_FINISHED",
        "timestamp": _now_iso(),
        "data": {
            "flow_id": str(flow.id),
            "flow_name": flow.name,
            "flow_run_id": flow_run_id,
            "document_id": document_id,
            "origin_session_id": session_id,
            "status": flow_status,
            "failure_reason": failure_reason,
            "total_evidence_records": len(evidence_registry.records()),
            "step_evidence_counts": _build_step_evidence_counts(completed_steps),
            "adapter_keys": _build_completed_step_adapter_keys(completed_steps),
            "extraction_handoff_audits": _build_flow_extraction_handoff_audits(
                completed_steps
            ),
            "extraction_result_refs": list(
                flow_execution_state.get("persisted_extraction_results") or []
            ),
            "extraction_result_ids": [
                ref.get("extraction_result_id")
                for ref in flow_execution_state.get("persisted_extraction_results") or []
                if isinstance(ref, Mapping) and ref.get("extraction_result_id")
            ],
            "review_session_ids": list(
                (flow_execution_state.get("curation_handoff") or {}).get(
                    "review_session_ids"
                )
                or []
            ),
        }
    }

    if flow_status == "failed":
        logger.warning(
            "[Flow Executor] Flow failed: '%s' (reason=%s)",
            flow.name,
            failure_reason,
        )
    else:
        logger.info("[Flow Executor] Flow completed: '%s'", flow.name)
