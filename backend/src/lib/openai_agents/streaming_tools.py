"""
Streaming tool wrappers for specialist agents.

This module provides custom tool wrappers that expose internal agent activity.
Unlike `as_tool()` which runs agents as black boxes, these wrappers use
`Runner.run_streamed()` to capture internal tool calls and report them.

REAL-TIME EVENT STREAMING:
Events can be pushed to a live queue for immediate emission to the audit panel,
or collected in a context variable for batch emission after completion.

When a live queue is set via `set_live_event_queue()`, events are pushed
immediately, allowing real-time visibility into specialist agent activity.
"""

import copy
import asyncio
import importlib
import json
import logging
import re
import time
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import List, Dict, Any, Mapping, Optional

from agents import Agent, AgentOutputSchema, Runner, RunConfig

from .audit_labels import build_specialist_internal_friendly_name
from .config import get_max_turns, reasoning_summary_request_settings, resolve_model_provider
from .evidence_summary import (
    build_record_evidence_summary_record,
    canonicalize_structured_result_payload,
    coerce_tool_event_dict,
    extract_evidence_records_from_structured_result,
    structured_result_evidence_reference_report,
    structured_result_missing_evidence_record_refs,
    structured_result_requires_evidence,
)
from .event_types import (
    INTERNAL_EXTRACTION_RESULT_EVENT_TYPE as _INTERNAL_EXTRACTION_RESULT_EVENT_TYPE,
)
from .tools.evidence_workspace import (
    reset_active_evidence_records,
    set_active_evidence_records,
)
from .extraction_builder_workspace import (
    ExtractionBuilderWorkspace,
    build_internal_extraction_result_event,
    finalize_extraction_payload,
    get_active_extraction_builder_workspace,
    reset_active_extraction_builder_workspace,
    set_active_extraction_builder_workspace,
    stage_extraction_payload,
)
from .extraction_trace_events import (
    get_current_extraction_trace_run,
    write_extraction_trace_event,
    write_stream_event,
)
from .tool_call_policy import (
    DOCUMENT_REQUIRED_TOOL_NAMES,
    required_package_tool_names_from_metadata,
    required_tool_names_for_available_tools,
)

# Prompt context tracking for execution logging
from src.lib.prompts.context import (
    append_pending_prompt_runtime_context,
    commit_pending_prompts,
)
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult

logger = logging.getLogger(__name__)

INTERNAL_EXTRACTION_RESULT_EVENT_TYPE = _INTERNAL_EXTRACTION_RESULT_EVENT_TYPE
_DOCUMENT_REQUIRED_TOOL_NAMES = set(DOCUMENT_REQUIRED_TOOL_NAMES)
_GROQ_SCHEMA_CONSTRAINTS_ADAPTER_KEY = "groq_schema_constraints"
_DOMAIN_ENVELOPE_SUPERVISOR_FIELD_PRIORITY = (
    "mention",
    "label",
    "name",
    "primary_external_id",
    "curie",
    "gene_symbol",
    "symbol",
    "taxon",
    "taxon_id",
    "species",
    "data_provider",
    "term_id",
    "term_label",
    "ontology_id",
    "ontology_term_id",
    "chebi_id",
    "disease_id",
)
_DOMAIN_ENVELOPE_SUPERVISOR_FIELD_SKIP = {
    "chunk_id",
    "confidence",
    "data_provider_hint",
    "evidence_record_id",
    "evidence_record_ids",
    "figure_reference",
    "identity_resolution_notes",
    "page",
    "proposed_primary_external_id",
    "proposed_gene_symbol",
    "proposed_symbol",
    "proposed_taxon",
    "section",
    "subsection",
    "taxon_hint",
    "verified_quote",
}


def _elapsed_ms(started_at: float) -> int:
    """Return elapsed monotonic time in milliseconds."""

    return int((time.monotonic() - started_at) * 1000)


def _tool_output_summary(tool_name: str, output: Any) -> Optional[Dict[str, Any]]:
    """Return a compact, artifact-safe summary of a tool output payload."""

    payload = coerce_tool_event_dict(output)
    if not isinstance(payload, dict):
        return None

    if tool_name != "record_evidence":
        return None

    summary_fields = (
        "status",
        "entity",
        "span_ids",
        "source_span_ids",
        "source_fragments",
        "document_id",
        "chunk_id",
        "chunk_ids",
        "verified_quote",
        "evidence_record_id",
        "page",
        "section",
        "subsection",
        "figure_reference",
        "failed_span_id",
        "failed_span_index",
        "failed_span_error",
        "message",
        "retry_instructions",
    )
    summary: Dict[str, Any] = {}
    for field_name in summary_fields:
        if field_name not in payload:
            continue
        value = payload.get(field_name)
        if isinstance(value, str) and len(value) > 1000:
            value = f"{value[:1000]}..."
        summary[field_name] = value
    return summary


def _is_domain_envelope_extraction_output_type(output_type: Any) -> bool:
    """Return whether an output type uses the shared domain-envelope contract."""

    if output_type is None:
        return False
    try:
        return issubclass(output_type, DomainEnvelopeExtractionResult)
    except TypeError:
        return False


def _apply_relaxed_output_schema_if_needed(agent: Agent, output_type: Any) -> Agent:
    """Relax SDK strict-schema conversion for domain-envelope outputs."""

    if not _is_domain_envelope_extraction_output_type(output_type):
        return agent

    runtime_agent = copy.copy(agent)
    runtime_agent.output_type = AgentOutputSchema(
        output_type,
        strict_json_schema=False,
    )
    return runtime_agent


def _extract_stream_tool_call_tracking_id(item: Any) -> Optional[str]:
    """Best-effort stable tool call identifier across SDK item shapes."""

    raw_item = getattr(item, "raw_item", None)
    candidates = (
        getattr(item, "id", None),
        getattr(item, "tool_id", None),
        getattr(item, "tool_call_id", None),
        getattr(item, "call_id", None),
        getattr(raw_item, "id", None),
        getattr(raw_item, "tool_id", None),
        getattr(raw_item, "tool_call_id", None),
        getattr(raw_item, "call_id", None),
    )

    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text

    return None


def _normalize_record_evidence_span_ids(value: Any) -> List[str]:
    """Return clean span IDs from a record_evidence payload."""

    if not isinstance(value, list):
        return []

    return [
        span_id
        for span_id in (str(raw_span_id or "").strip() for raw_span_id in value)
        if span_id
    ]


def _pop_matching_pending_tool_call(
    pending_tool_calls: "deque[Dict[str, Any]]",
    *,
    output_item: Any,
) -> Optional[Dict[str, Any]]:
    """Match a tool output to its originating tool call, preferring stable call IDs."""

    if not pending_tool_calls:
        return None

    output_tool_id = _extract_stream_tool_call_tracking_id(output_item)
    if output_tool_id:
        for candidate_tool in list(pending_tool_calls):
            if str(candidate_tool.get("tool_id") or "").strip() == output_tool_id:
                pending_tool_calls.remove(candidate_tool)
                return candidate_tool

    output_payload = coerce_tool_event_dict(getattr(output_item, "output", None))
    if isinstance(output_payload, dict):
        output_entity = str(output_payload.get("entity") or "").strip()
        output_span_ids = _normalize_record_evidence_span_ids(output_payload.get("span_ids"))
        if output_entity and output_span_ids:
            for candidate_tool in list(pending_tool_calls):
                if str(candidate_tool.get("tool_name") or "").strip() != "record_evidence":
                    continue
                candidate_args = candidate_tool.get("tool_args")
                if not isinstance(candidate_args, dict):
                    continue
                candidate_span_ids = _normalize_record_evidence_span_ids(candidate_args.get("span_ids"))
                if (
                    str(candidate_args.get("entity") or "").strip() == output_entity
                    and candidate_span_ids == output_span_ids
                ):
                    pending_tool_calls.remove(candidate_tool)
                    return candidate_tool

    if len(pending_tool_calls) > 1:
        logger.warning(
            "Ambiguous tool output without matching call_id; falling back to oldest pending tool call",
            extra={"output_tool_id": output_tool_id, "pending_count": len(pending_tool_calls)},
        )

    return pending_tool_calls.popleft()


# =============================================================================
# EXCEPTION CLASSES
# =============================================================================

class SpecialistOutputError(Exception):
    """
    Raised when a specialist agent fails to produce required structured output after retry.

    This error indicates that the specialist completed its tool calls but did not generate
    the expected Pydantic model output, even after being given a second chance with a
    nudge prompt.
    """
    def __init__(
        self,
        specialist_name: str,
        output_type_name: str,
        message: str | None = None,
        *,
        details: list[dict[str, Any]] | None = None,
    ):
        self.specialist_name = specialist_name
        self.output_type_name = output_type_name
        self.details = details or []
        super().__init__(
            message
            or f"{specialist_name} failed to produce {output_type_name} after retry"
        )


def _extract_model_identifier(model: Any) -> str:
    """Best-effort model ID extraction from agent model config."""
    if isinstance(model, str):
        return model
    return str(getattr(model, "model", "") or "").strip()


def _reasoning_request_metadata(agent: Agent) -> Dict[str, Any]:
    model = _extract_model_identifier(getattr(agent, "model", None))
    reasoning_settings = getattr(getattr(agent, "model_settings", None), "reasoning", None)
    reasoning_effort = getattr(reasoning_settings, "effort", None)
    if not model:
        return {
            "availability": "unavailable",
            "reason": "missing_model_identifier",
        }
    try:
        provider = resolve_model_provider(model)
        return reasoning_summary_request_settings(
            model=model,
            reasoning_effort=reasoning_effort,
            provider_override=provider,
        )
    except Exception as exc:
        return {
            "availability": "unavailable",
            "model": model,
            "reason": type(exc).__name__,
        }


def _reasoning_summary_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("summary_text", "text"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_reasoning_summary_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()

    text = getattr(value, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    return ""


def _should_use_groq_tool_json_compat(agent: Agent) -> bool:
    """Groq currently rejects response_format JSON mode combined with tool calling.

    Docs: https://console.groq.com/docs/structured-outputs
    """
    output_type = getattr(agent, "output_type", None)
    if output_type is None:
        return False

    tools = getattr(agent, "tools", []) or []
    if len(tools) == 0:
        return False

    model_id = _extract_model_identifier(getattr(agent, "model", None)).lower()
    if model_id.startswith("groq/"):
        return True

    # Safety net for alternate naming conventions if encountered in runtime model IDs.
    if "groq" in model_id and "/" in model_id:
        return True

    return False


def _build_json_only_instruction(output_type: Any) -> str:
    """Instruction used when structured outputs must be disabled for provider compatibility."""
    schema_blob = ""
    try:
        if output_type is not None and hasattr(output_type, "model_json_schema"):
            schema_blob = json.dumps(output_type.model_json_schema(), ensure_ascii=True)
    except Exception:
        schema_blob = ""

    if schema_blob:
        return (
            "IMPORTANT OUTPUT FORMAT REQUIREMENT:\n"
            "After completing tool calls, respond with ONLY valid JSON (no markdown, no backticks).\n"
            "Do NOT return markdown tables, prose explanations, or fenced code blocks.\n"
            "Your JSON MUST match this schema exactly:\n"
            f"{schema_blob}\n"
        )

    return (
        "IMPORTANT OUTPUT FORMAT REQUIREMENT:\n"
        "After completing tool calls, respond with ONLY valid JSON (no markdown, no backticks).\n"
        "Do NOT return markdown tables, prose explanations, or fenced code blocks.\n"
    )


def _append_agent_runtime_instruction(
    runtime_agent: Agent,
    source_agent: Agent,
    *,
    instruction: str,
    layer_id_suffix: str,
    title: str,
    source_ref: str,
) -> Agent:
    """Append runtime-only prompt content and keep pending assembly metadata aligned."""

    if runtime_agent is source_agent:
        runtime_agent = copy.copy(source_agent)
    runtime_agent.instructions = (
        f"{getattr(runtime_agent, 'instructions', '') or ''}\n\n"
        f"{instruction}"
    ).strip()
    append_pending_prompt_runtime_context(
        source_agent,
        layer_id_suffix=layer_id_suffix,
        title=title,
        content=instruction,
        source_ref=source_ref,
        target_agent=runtime_agent,
    )
    return runtime_agent


def _try_parse_markdown_field_table(raw_text: str) -> Optional[Dict[str, Any]]:
    """Parse simple markdown field tables into a JSON object candidate.

    Some Groq responses return a 3-column markdown table like:
    | Field | Type | Content |
    | **answer** | string | ... |
    | **citations** | array | [...] |
    | **sources** | array | [...] |
    """
    if not raw_text:
        return None

    candidate: Dict[str, Any] = {}
    saw_table_row = False

    for raw_line in str(raw_text).splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or line.count("|") < 3:
            continue
        # Skip markdown separator rows like |-----|-----|-----|
        if re.match(r"^\|\s*[-:]+\s*(\|\s*[-:]+\s*)+\|?$", line):
            continue

        saw_table_row = True
        body = line[1:-1] if line.endswith("|") else line[1:]
        parts = [p.strip() for p in body.split("|")]
        if len(parts) < 3:
            continue

        field = parts[0].strip().strip("*").strip().lower()
        if field not in {"answer", "citations", "sources"}:
            continue

        content = "|".join(parts[2:]).strip()
        if not content:
            continue

        if field == "answer":
            # Drop surrounding quotes for plain answer strings.
            if (content.startswith('"') and content.endswith('"')) or (
                content.startswith("'") and content.endswith("'")
            ):
                content = content[1:-1]
            candidate["answer"] = content
            continue

        # citations/sources should be JSON arrays when possible.
        try:
            parsed = json.loads(content)
            candidate[field] = parsed
        except Exception:
            # Conservative fallback: keep minimally valid shape.
            if field == "citations":
                candidate[field] = []
            else:
                candidate[field] = [content]

    if not saw_table_row or not candidate:
        return None
    return candidate


def _try_validate_json_output(raw_text: str, output_type: Any) -> Optional[str]:
    """Extract JSON from text and validate against a Pydantic output_type."""
    if not raw_text or output_type is None:
        return None

    text = str(raw_text).strip()
    if not text:
        return None

    json_candidate = text
    if not (text.startswith("{") and text.endswith("}")):
        json_start = text.find("{")
        json_end = text.rfind("}")
        if json_start < 0 or json_end <= json_start:
            return None
        json_candidate = text[json_start:json_end + 1]

    try:
        parsed = json.loads(json_candidate)
        validated = output_type.model_validate(parsed)
        return json.dumps(validated.model_dump())
    except Exception:
        pass

    # Groq sometimes emits markdown field tables instead of raw JSON.
    markdown_candidate = _try_parse_markdown_field_table(text)
    if markdown_candidate is None:
        return None

    try:
        validated = output_type.model_validate(markdown_candidate)
        return json.dumps(validated.model_dump())
    except Exception:
        return None


def _extract_tool_name(tool: Any) -> str:
    """Best-effort extraction of tool name from SDK tool objects."""
    return str(
        getattr(tool, "name", None)
        or getattr(tool, "tool_name", None)
        or ""
    ).strip()


def _import_callable(import_path: str) -> Any:
    module_name, attr_name = import_path.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


@lru_cache(maxsize=16)
def _tool_metadata_by_name() -> Dict[str, Dict[str, Any]]:
    """Return package-declared tool metadata keyed by tool ID."""
    from src.lib.packages.tool_registry import load_tool_registry

    registry = load_tool_registry()
    return {
        binding.tool_id: dict(binding.metadata)
        for binding in registry.bindings
        if isinstance(binding.metadata, dict)
    }


@lru_cache(maxsize=16)
def _tool_provider_adapter_factories(adapter_key: str) -> Dict[str, Any]:
    """Return package-declared provider adapter factories keyed by tool ID."""
    from src.lib.packages.import_paths import extend_sys_path_for_package
    from src.lib.packages.tool_registry import load_tool_registry

    registry = load_tool_registry()
    factories: Dict[str, Any] = {}
    for binding in registry.bindings:
        import_path = binding.provider_adapters.get(adapter_key)
        if not import_path:
            continue
        package = registry.package_registry.get_package(binding.source.package_id)
        if package is not None:
            extend_sys_path_for_package(package)
        factories[binding.tool_id] = _import_callable(import_path)
    return factories


def _required_package_tool_names(available_tool_names: set[str]) -> set[str]:
    return required_package_tool_names_from_metadata(
        available_tool_names,
        _tool_metadata_by_name(),
    )


def _required_tool_names_for_agent(agent: Agent) -> Optional[set[str]]:
    """Return required tool set for runtime enforcement, if applicable."""
    tools = getattr(agent, "tools", []) or []
    available_tool_names = {
        name for name in (_extract_tool_name(tool) for tool in tools) if name
    }
    if not available_tool_names:
        return None

    required_tools = required_tool_names_for_available_tools(
        available_tool_names,
        required_package_tool_names_resolver=_required_package_tool_names,
    )
    return set(required_tools) if required_tools else None


def _agent_tool_names(agent: Agent) -> set[str]:
    """Return normalized tool names for an agent."""
    tools = getattr(agent, "tools", []) or []
    return {
        name for name in (_extract_tool_name(tool) for tool in tools) if name
    }


def _estimate_bulk_entity_count(input_text: str) -> int:
    """Estimate how many entities are being requested in a single specialist query."""
    if not input_text:
        return 0

    text = str(input_text)
    lowered = text.lower()

    # Prefer list-heavy tail segment to avoid counting instruction prose.
    tail_start = 0
    for marker in (
        "extracted list:",
        "raw list:",
        "gene list:",
        "list:",
        "genes extracted:",
        "items:",
    ):
        idx = lowered.find(marker)
        if idx >= 0:
            tail_start = max(tail_start, idx + len(marker))
    candidate = text[tail_start:] if tail_start > 0 else text

    # Split on common list delimiters and keep plausible symbols.
    parts = re.split(r"[,;\n]+", candidate)
    seen: set[str] = set()
    for raw in parts:
        token = raw.strip().strip(".")
        if not token:
            continue
        if len(token) > 80:
            continue
        if token.lower().startswith(("query:", "return:", "provide:", "notes:")):
            continue
        if re.search(r"[A-Za-z0-9]", token) is None:
            continue
        seen.add(token.lower())

    return len(seen)


def _compute_adaptive_specialist_max_turns(
    *,
    agent: Agent,
    input_text: str,
    base_max_turns: int,
) -> int:
    """Increase turn budget for package-declared bulk lookup workloads."""
    tool_names = _agent_tool_names(agent)
    bulk_specs = [
        _tool_metadata_by_name().get(tool_name, {}).get("bulk_list_optimization")
        for tool_name in tool_names
    ]
    bulk_specs = [spec for spec in bulk_specs if isinstance(spec, dict) and spec.get("enabled")]
    if not bulk_specs:
        return base_max_turns

    entity_count = _estimate_bulk_entity_count(input_text)
    minimum_entities = min(
        _bulk_list_optimization_int(spec, "minimum_entities") for spec in bulk_specs
    )
    if entity_count < minimum_entities:
        return base_max_turns

    max_turn_cap = max(
        _bulk_list_optimization_int(spec, "max_turns", minimum=1) for spec in bulk_specs
    )
    min_turn_floor = max(
        _bulk_list_optimization_int(spec, "min_turns", minimum=1) for spec in bulk_specs
    )
    adaptive = max(base_max_turns, 10 + (entity_count * 2))
    adaptive = max(adaptive, min_turn_floor)
    adaptive = min(adaptive, max_turn_cap)
    return adaptive


def _bulk_list_optimization_int(
    spec: dict[str, Any],
    field_name: str,
    *,
    minimum: int = 0,
) -> int:
    value = spec.get(field_name)
    if value is None:
        raise ValueError(
            "Package tool bulk_list_optimization is enabled but "
            f"{field_name} is not declared."
        )
    if isinstance(value, bool):
        raise ValueError(
            "Package tool bulk_list_optimization field "
            f"{field_name} must be an integer."
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Package tool bulk_list_optimization field "
            f"{field_name} must be an integer."
        ) from exc
    if parsed < minimum:
        raise ValueError(
            "Package tool bulk_list_optimization field "
            f"{field_name} must be at least {minimum}."
        )
    return parsed


def _build_tool_efficiency_instruction(agent: Agent, input_text: str) -> str:
    """Return guidance that nudges large list processing toward fewer tool turns."""
    tool_names = _agent_tool_names(agent)
    bulk_specs = [
        _tool_metadata_by_name().get(tool_name, {}).get("bulk_list_optimization")
        for tool_name in tool_names
    ]
    bulk_specs = [spec for spec in bulk_specs if isinstance(spec, dict) and spec.get("enabled")]
    if not bulk_specs:
        return ""

    entity_count = _estimate_bulk_entity_count(input_text)
    minimum_entities = min(
        _bulk_list_optimization_int(spec, "minimum_entities") for spec in bulk_specs
    )
    if entity_count < minimum_entities:
        return ""

    instructions = [
        str(spec.get("instruction") or "").strip()
        for spec in bulk_specs
        if str(spec.get("instruction") or "").strip()
    ]
    if instructions:
        return "\n\n".join(instructions) + "\n"
    tools_text = ", ".join(sorted(tool_names))
    raise ValueError(
        "Package tool bulk_list_optimization is enabled but no instruction is declared "
        f"for active tool(s): {tools_text}"
    )


def _adapt_tools_with_provider_adapter(tools: List[Any], adapter_key: str) -> List[Any]:
    """Replace package-declared tools with provider-specific adapter factories."""
    adapter_factories = _tool_provider_adapter_factories(adapter_key)
    if not adapter_factories:
        return list(tools)

    adapted: List[Any] = []
    for tool in tools:
        tool_name = _extract_tool_name(tool)
        adapter_factory = adapter_factories.get(tool_name)
        if adapter_factory is None:
            adapted.append(tool)
            continue

        adapted.append(adapter_factory())
    return adapted


def _adapt_tools_for_groq_schema_constraints(tools: List[Any]) -> List[Any]:
    """Replace package-declared tools that violate Groq's strict schema constraints."""
    return _adapt_tools_with_provider_adapter(
        tools,
        _GROQ_SCHEMA_CONSTRAINTS_ADAPTER_KEY,
    )


def _required_tool_failure_message(
    *,
    agent: Agent,
    specialist_name: str,
    tool_calls: List["SpecialistToolCall"],
) -> Optional[str]:
    """Return enforcement error if specialist skipped required internal tools."""
    required_tools = _required_tool_names_for_agent(agent)
    if not required_tools:
        return None

    called_tools = {
        str(getattr(call, "tool_name", "") or "").strip()
        for call in tool_calls
        if str(getattr(call, "tool_name", "") or "").strip()
    }
    if called_tools & required_tools:
        return None

    required_text = ", ".join(sorted(required_tools))
    called_text = ", ".join(sorted(called_tools)) if called_tools else "none"

    if required_tools == _DOCUMENT_REQUIRED_TOOL_NAMES:
        return (
            f"{specialist_name} did not call required document tools before answering. "
            f"Required: {required_text}. Called: {called_text}."
        )

    metadata_by_name = _tool_metadata_by_name()
    message = None
    for tool_name in sorted(required_tools):
        required_call = metadata_by_name.get(tool_name, {}).get("required_tool_call")
        if not isinstance(required_call, dict):
            raise ValueError(
                "Package required_tool_call metadata must be declared "
                f"for tool '{tool_name}'."
            )
        candidate = str(required_call.get("failure_message") or "").strip()
        if not candidate:
            raise ValueError(
                "Package required_tool_call metadata must declare failure_message "
                f"for tool '{tool_name}'."
            )
        if message is None:
            message = candidate

    return f"{specialist_name} {message}. Required: {required_text}. Called: {called_text}."


def _emit_specialist_evidence_summary_or_raise(
    *,
    specialist_name: str,
    tool_name: Optional[str],
    expected_output_type: Any,
    final_output: Any,
    live_evidence_records: List[Dict[str, Any]],
):
    """Emit specialist evidence summary from live tool-verified evidence or fail fast."""
    evidence_records = extract_evidence_records_from_structured_result(final_output)
    requires_evidence = structured_result_requires_evidence(
        final_output,
        expected_output_type=expected_output_type,
    )
    missing_record_refs = (
        structured_result_missing_evidence_record_refs(
            final_output,
            expected_output_type=expected_output_type,
        )
        if requires_evidence
        else False
    )

    if evidence_records and not missing_record_refs:
        add_specialist_event({
            "type": "evidence_summary",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_name": tool_name,
            "evidence_records": evidence_records,
        })
        return

    if not requires_evidence:
        return

    if live_evidence_records and not missing_record_refs:
        add_specialist_event({
            "type": "evidence_summary",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool_name": tool_name,
            "evidence_records": live_evidence_records,
        })
        return

    output_type_name = getattr(expected_output_type, "__name__", "response")
    error_message = (
        f"{specialist_name} completed extraction output without the required verified evidence records."
    )
    evidence_reference_report = structured_result_evidence_reference_report(
        final_output,
        expected_output_type=expected_output_type,
    )
    logger.error(
        "%s requires_evidence=%s missing_record_refs=%s structured_evidence_count=%s "
        "live_evidence_count=%s evidence_reference_report=%s",
        error_message,
        requires_evidence,
        missing_record_refs,
        len(evidence_records),
        len(live_evidence_records),
        evidence_reference_report,
    )
    add_specialist_event({
        "type": "SPECIALIST_ERROR",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": {
            "specialist": specialist_name,
            "output_type": output_type_name,
            "error": error_message,
            "reason": "missing_evidence_records",
            "requires_evidence": requires_evidence,
            "missing_record_refs": missing_record_refs,
            "structured_evidence_count": len(evidence_records),
            "live_evidence_count": len(live_evidence_records),
            "evidence_reference_report": evidence_reference_report,
            "severity": "error",
        }
    })
    raise SpecialistOutputError(
        specialist_name=specialist_name,
        output_type_name=output_type_name,
        message=error_message,
    )


def _canonicalize_structured_output_text(
    final_output: str,
    *,
    expected_output_type: Any,
) -> str:
    """Collapse duplicate normalized items before the supervisor reads structured output."""

    if expected_output_type is None:
        return final_output

    try:
        payload = json.loads(final_output)
    except Exception:
        return final_output

    if not isinstance(payload, dict):
        return final_output

    canonical_payload = canonicalize_structured_result_payload(payload)
    if not isinstance(canonical_payload, dict):
        return final_output

    try:
        validated_output = expected_output_type.model_validate(canonical_payload)
        return json.dumps(validated_output.model_dump())
    except Exception:
        return json.dumps(canonical_payload)


def _reduce_specialist_output_for_supervisor(
    final_output: str,
    *,
    expected_output_type: Any,
) -> str:
    """Return concise answer text when structured output carries a dedicated answer field."""

    if expected_output_type is None:
        return final_output

    try:
        payload = json.loads(final_output)
    except Exception:
        return final_output

    if not isinstance(payload, dict):
        return final_output

    answer_text = str(payload.get("answer") or "").strip()
    if answer_text:
        return answer_text

    if _is_domain_envelope_extraction_output_type(expected_output_type):
        summary_text = _domain_envelope_supervisor_summary(payload)
        if summary_text:
            return summary_text

    return final_output


def _domain_envelope_supervisor_summary(payload: Dict[str, Any]) -> str:
    """Build a compact supervisor-facing summary from a materialized envelope."""

    objects = payload.get("objects")
    if not isinstance(objects, list) or not objects:
        return ""

    domain_pack_id = str(payload.get("domain_pack_id") or "domain envelope")
    lines = [
        (
            f"Validated domain envelope result for {domain_pack_id}. "
            "Use these validated/materialized values in the final answer."
        )
    ]

    for index, item in enumerate(objects[:5], start=1):
        if not isinstance(item, dict):
            continue
        object_type = str(item.get("object_type") or "object").strip()
        status = str(item.get("status") or "unknown").strip()
        pending_ref = str(
            item.get("pending_ref_id") or item.get("object_id") or ""
        ).strip()
        payload_fields = _domain_envelope_supervisor_payload_fields(
            item.get("payload")
        )
        if not payload_fields:
            continue
        label_parts = [f"{index}.", object_type]
        if pending_ref:
            label_parts.append(pending_ref)
        label_parts.append(f"({status})")
        lines.append(f"{' '.join(label_parts)}: {payload_fields}")

    if len(lines) == 1:
        return ""

    findings = payload.get("validation_findings")
    if isinstance(findings, list):
        status_counts: Dict[str, int] = {}
        resolved_messages: List[str] = []
        unresolved_messages: List[str] = []
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            status = str(finding.get("status") or "unknown").strip() or "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
            if status == "resolved" and len(resolved_messages) < 5:
                resolved_summary = _domain_envelope_resolved_finding_summary(finding)
                if resolved_summary:
                    resolved_messages.append(resolved_summary)
            if status != "resolved" and len(unresolved_messages) < 3:
                code = str(finding.get("code") or "validation finding").strip()
                message = str(finding.get("message") or "").strip()
                unresolved_messages.append(
                    f"{code}: {_truncate_for_supervisor_summary(message, limit=180)}"
                )
        if status_counts:
            counts = ", ".join(
                f"{status}={count}" for status, count in sorted(status_counts.items())
            )
            lines.append(f"Validation findings: {counts}.")
        for message in resolved_messages:
            lines.append(f"Resolved validator finding: {message}")
        for message in unresolved_messages:
            lines.append(f"Unresolved validator finding: {message}")

    return "\n".join(lines)


def _domain_envelope_resolved_finding_summary(finding: Dict[str, Any]) -> str:
    details = finding.get("details")
    if not isinstance(details, dict):
        return ""

    validation_result = details.get("validation_result")
    if not isinstance(validation_result, dict):
        validation_result = {}
    resolved_values = validation_result.get("resolved_values")
    if not isinstance(resolved_values, dict):
        resolved_values = {}

    lookup_attempts = details.get("lookup_attempts")
    if not isinstance(lookup_attempts, list):
        lookup_attempts = []

    validation_request = details.get("validation_request")
    if not isinstance(validation_request, dict):
        validation_request = {}

    target_label = _domain_envelope_request_label(validation_request)
    resolved_id = _first_scalar_value(
        resolved_values,
        ("curie", "primary_external_id", "external_id", "id", "identifier"),
    ) or _first_lookup_attempt_scalar(lookup_attempts, "resolved_id")
    resolved_symbol = _first_scalar_value(
        resolved_values,
        ("symbol", "allele_symbol", "gene_symbol", "label", "name"),
    ) or _first_lookup_attempt_scalar(lookup_attempts, "resolved_label")
    resolved_taxon = _first_scalar_value(resolved_values, ("taxon", "taxon_curie"))

    parts: list[str] = []
    if target_label:
        parts.append(_truncate_for_supervisor_summary(target_label, limit=80))
    if resolved_id:
        parts.append(f"curie={_truncate_for_supervisor_summary(resolved_id)}")
    if resolved_symbol:
        parts.append(f"symbol={_truncate_for_supervisor_summary(resolved_symbol)}")
    if resolved_taxon:
        parts.append(f"taxon={_truncate_for_supervisor_summary(resolved_taxon)}")
    if not parts:
        message = str(finding.get("message") or "").strip()
        return _truncate_for_supervisor_summary(message, limit=180) if message else ""
    return "; ".join(parts)


def _domain_envelope_request_label(validation_request: Dict[str, Any]) -> str:
    for source_key in ("selected_inputs",):
        source = validation_request.get(source_key)
        if not isinstance(source, dict):
            continue
        label = _first_scalar_value(
            source,
            ("mention", "label", "name", "symbol", "curie", "id"),
        )
        if label:
            return label

    target = validation_request.get("target")
    if not isinstance(target, dict):
        return ""
    input_values = target.get("input_values")
    if isinstance(input_values, dict):
        label = _first_scalar_value(
            input_values,
            ("mention", "label", "name", "symbol", "curie", "id"),
        )
        if label:
            return label
    return _first_scalar_value(target, ("object_id", "object_type", "field_path")) or ""


def _first_scalar_value(source: Dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = source.get(key)
        if _is_supervisor_summary_scalar(value):
            text = str(value).strip()
            if text:
                return text
    return ""


def _first_lookup_attempt_scalar(
    lookup_attempts: list[Any],
    key: str,
) -> str:
    for attempt in lookup_attempts:
        if not isinstance(attempt, dict):
            continue
        value = attempt.get(key)
        if _is_supervisor_summary_scalar(value):
            text = str(value).strip()
            if text:
                return text
    return ""


def _domain_envelope_supervisor_payload_fields(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    selected: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add_field(key: str) -> None:
        if key in seen or key in _DOMAIN_ENVELOPE_SUPERVISOR_FIELD_SKIP:
            return
        value = payload.get(key)
        if not _is_supervisor_summary_scalar(value):
            return
        text = _truncate_for_supervisor_summary(str(value).strip())
        if not text:
            return
        seen.add(key)
        selected.append((key, text))

    for key in _DOMAIN_ENVELOPE_SUPERVISOR_FIELD_PRIORITY:
        add_field(key)

    for key in sorted(payload):
        if len(selected) >= 10:
            break
        if not _looks_like_materialized_identity_field(key):
            continue
        add_field(key)

    return "; ".join(f"{key}={value}" for key, value in selected)


def _is_supervisor_summary_scalar(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (int, float, bool))


def _looks_like_materialized_identity_field(key: str) -> bool:
    normalized = key.lower()
    if normalized in _DOMAIN_ENVELOPE_SUPERVISOR_FIELD_SKIP:
        return False
    return (
        normalized.endswith("_id")
        or normalized.endswith("_curie")
        or normalized.endswith("_symbol")
        or normalized.endswith("_label")
        or normalized in {"id", "curie", "symbol", "label", "name", "taxon"}
    )


def _truncate_for_supervisor_summary(value: str, *, limit: int = 120) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}..."


def _agent_key_from_specialist_tool_name(tool_name: Optional[str]) -> Optional[str]:
    """Resolve the canonical agent key segment from an ask_*_specialist tool name."""

    match = re.match(
        r"^ask_(?P<agent_key>.+?)(?:_step\d+)?_specialist$",
        str(tool_name or "").strip(),
    )
    if match is None:
        return None
    return match.group("agent_key")


def _active_builder_workspace_or_none() -> ExtractionBuilderWorkspace | None:
    try:
        return get_active_extraction_builder_workspace()
    except RuntimeError:
        return None


def _record_builder_specialist_output_failure(
    *,
    builder_workspace: ExtractionBuilderWorkspace,
    specialist_name: str,
    tool_name: Optional[str],
    output_type_name: str,
    reason: str,
    message: str,
    candidate_id: str,
    extra: Mapping[str, Any] | None = None,
) -> None:
    if builder_workspace.finalization is not None:
        return
    error = {
        "message": message,
        "reason": reason,
        "specialist_name": specialist_name,
        "tool_name": tool_name,
        "output_type": output_type_name,
    }
    if extra:
        error.update(dict(extra))
    builder_workspace.record_validation_failure(
        errors=[error],
        candidate_ids=[candidate_id],
    )


def _is_domain_envelope_output_json(
    final_output: str,
    *,
    expected_output_type: Any,
) -> bool:
    if not final_output or not _is_domain_envelope_extraction_output_type(expected_output_type):
        return False
    try:
        payload = json.loads(final_output)
    except Exception:
        return False
    return isinstance(payload, dict)


def _validator_dispatch_has_error_result(dispatch_result: Any) -> bool:
    """Return whether any dispatched validator result reports an execution error."""

    for result in getattr(dispatch_result, "validator_results", ()) or ():
        for attempt in getattr(result, "lookup_attempts", ()) or ():
            if getattr(attempt, "outcome", None) == "error":
                return True
    return False


def _validator_dispatch_error_details(dispatch_result: Any) -> list[dict[str, Any]]:
    """Return compact validator-dispatch execution errors for builder validation."""

    errors: list[dict[str, Any]] = []
    for result in getattr(dispatch_result, "validator_results", ()) or ():
        result_request_id = getattr(result, "request_id", None)
        result_binding_id = getattr(result, "validator_binding_id", None)
        for attempt in getattr(result, "lookup_attempts", ()) or ():
            if getattr(attempt, "outcome", None) != "error":
                continue
            errors.append(
                {
                    "reason": "domain_validator_dispatch_failed",
                    "message": (
                        getattr(attempt, "message", None)
                        or getattr(result, "curator_message", None)
                        or getattr(result, "explanation", None)
                        or (
                            "Domain-envelope validator dispatch reported an "
                            "execution error."
                        )
                    ),
                    "request_id": result_request_id,
                    "validator_binding_id": result_binding_id,
                    "provider": getattr(attempt, "provider", None),
                    "method": getattr(attempt, "method", None),
                }
            )
    return errors


def _validator_dispatch_status_counts(dispatch_result: Any) -> dict[str, int]:
    """Count validator result statuses for compact audit labels."""

    counts: dict[str, int] = {}
    for result in getattr(dispatch_result, "validator_results", ()) or ():
        status = str(getattr(result, "status", "unknown") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _validator_dispatch_completion_label(
    specialist_name: str,
    dispatch_result: Any,
) -> str:
    counts = _validator_dispatch_status_counts(dispatch_result)
    if not counts:
        return f"{specialist_name}: Active Validator Dispatch complete"

    status_summary = ", ".join(
        f"{status} {count}" for status, count in sorted(counts.items())
    )
    return (
        f"{specialist_name}: Active Validator Dispatch complete "
        f"({status_summary})"
    )


def _validator_lookup_tool_args(attempt: Any) -> dict[str, Any]:
    """Build audit-panel-friendly tool arguments for one validator lookup."""

    raw_query = getattr(attempt, "query", None)
    query = dict(raw_query) if isinstance(raw_query, dict) else {}
    method = str(getattr(attempt, "method", "") or "")

    if method and "method" not in query:
        query["method"] = method

    # The audit panel knows how to pretty-print AGR curation query methods. For
    # generic dispatch errors, show a JSON string instead of "[object Object]".
    if method not in {
        "get_gene_by_exact_symbol",
        "search_genes",
        "get_gene_by_id",
        "get_allele_by_exact_symbol",
        "search_alleles",
        "get_allele_by_id",
        "get_species",
        "get_data_providers",
        "search_anatomy_terms",
        "search_life_stage_terms",
        "search_go_terms",
    }:
        return {
            "query": json.dumps(raw_query or {}, sort_keys=True),
            "method": method or "validator_lookup",
        }

    return query


def _validator_lookup_audit_key(binding_id: Any, attempt: Any) -> str:
    raw_query = getattr(attempt, "query", None)
    return json.dumps(
        {
            "binding_id": str(binding_id or ""),
            "provider": str(getattr(attempt, "provider", "") or "validator"),
            "method": str(getattr(attempt, "method", "") or "validator_lookup"),
            "query": raw_query if isinstance(raw_query, dict) else raw_query,
            "outcome": str(getattr(attempt, "outcome", "") or "unknown"),
            "message": getattr(attempt, "message", None),
            "result_count": getattr(attempt, "result_count", None),
        },
        sort_keys=True,
        default=str,
    )


def _validator_lookup_status_summary(status_counts: dict[str, int]) -> str:
    if not status_counts:
        return "unknown"
    if len(status_counts) == 1:
        return next(iter(status_counts))
    return "mixed"


def _validator_lookup_complete_label(
    specialist_name: str,
    outcome: str,
    *,
    target_count: int,
    status_summary: str,
) -> str:
    if target_count <= 1:
        return f"{specialist_name}: Validator Lookup {outcome}"
    status_text = (
        "mixed validation"
        if status_summary == "mixed"
        else f"{status_summary} validation"
    )
    return (
        f"{specialist_name}: Validator Lookup {outcome} "
        f"({target_count} targets, {status_text})"
    )


def _emit_validator_lookup_audit_events(
    *,
    specialist_name: str,
    dispatch_result: Any,
) -> None:
    """Surface package-scoped validator lookup attempts in the live audit stream."""

    lookup_records: list[dict[str, Any]] = []
    lookup_record_by_key: dict[str, dict[str, Any]] = {}
    for result in getattr(dispatch_result, "validator_results", ()) or ():
        binding_id = getattr(result, "validator_binding_id", None)
        status = getattr(result, "status", None)
        request_id = getattr(result, "request_id", None)
        for attempt in getattr(result, "lookup_attempts", ()) or ():
            key = _validator_lookup_audit_key(binding_id, attempt)
            record = lookup_record_by_key.get(key)
            if record is None:
                record = {
                    "attempt": attempt,
                    "binding_id": binding_id,
                    "statuses": {},
                    "request_ids": [],
                }
                lookup_record_by_key[key] = record
                lookup_records.append(record)
            status_key = str(status or "unknown")
            record["statuses"][status_key] = (
                int(record["statuses"].get(status_key, 0)) + 1
            )
            if request_id is not None:
                record["request_ids"].append(str(request_id))

    for index, record in enumerate(lookup_records, start=1):
        attempt = record["attempt"]
        binding_id = record["binding_id"]
        status_counts = dict(record["statuses"])
        status_summary = _validator_lookup_status_summary(status_counts)
        request_ids = list(record["request_ids"])
        duplicate_count = len(request_ids) or sum(status_counts.values()) or 1
        method = str(getattr(attempt, "method", "") or "validator_lookup")
        provider = str(getattr(attempt, "provider", "") or "validator")
        outcome = str(getattr(attempt, "outcome", "") or "unknown")
        message = getattr(attempt, "message", None)
        tool_args = _validator_lookup_tool_args(attempt)
        friendly_name = (
            f"{specialist_name}: Validator Lookup"
            f" ({binding_id}, {provider}.{method})"
        )

        add_specialist_event({
            "type": "TOOL_START",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {
                "toolName": "agr_curation_query"
                if provider == "agr_curation_query"
                else "domain_validator_lookup",
                "friendlyName": friendly_name,
                "agent": specialist_name,
                "toolArgs": tool_args,
                "isSpecialistInternal": True,
                "validatorBindingId": binding_id,
                "validatorResultStatus": status_summary,
                "validatorResultStatuses": status_counts,
                "validatorLookupDuplicateCount": duplicate_count,
                "validatorLookupRequestIds": request_ids,
                "lookupIndex": index,
            },
        })
        add_specialist_event({
            "type": "TOOL_COMPLETE",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {
                "toolName": "agr_curation_query"
                if provider == "agr_curation_query"
                else "domain_validator_lookup",
                "friendlyName": _validator_lookup_complete_label(
                    specialist_name,
                    outcome,
                    target_count=duplicate_count,
                    status_summary=status_summary,
                ),
                "success": outcome not in {"error", "conflict"},
                "error": message if outcome == "error" else None,
                "isSpecialistInternal": True,
                "validatorBindingId": binding_id,
                "validatorResultStatus": status_summary,
                "validatorResultStatuses": status_counts,
                "validatorLookupDuplicateCount": duplicate_count,
                "validatorLookupRequestIds": request_ids,
                "lookupIndex": index,
                "resultCount": getattr(attempt, "result_count", None),
                "outcome": outcome,
            },
        })


async def _dispatch_domain_envelope_validators_for_chat(
    final_output: str,
    *,
    expected_output_type: Any,
    specialist_name: str,
    tool_name: Optional[str],
) -> str:
    """Run active domain-pack validators before extractor output reaches supervisor."""

    if not _is_domain_envelope_output_json(
        final_output,
        expected_output_type=expected_output_type,
    ):
        return final_output

    agent_key = _agent_key_from_specialist_tool_name(tool_name)
    if agent_key is None:
        raise SpecialistOutputError(
            specialist_name=specialist_name,
            output_type_name=getattr(expected_output_type, "__name__", "response"),
            message=(
                "Domain-envelope validator dispatch could not resolve the "
                f"source agent from tool name {tool_name!r}."
            ),
        )

    dispatch_wall_started_at = time.monotonic()
    dispatch_phase_timings_ms: Dict[str, int] = {}

    try:
        from src.lib.curation_workspace.curation_prep_service import (
            _domain_envelope_from_extraction_result,
        )
        from src.lib.curation_workspace.extraction_results import (
            build_extraction_envelope_candidate,
        )
        from src.lib.curation_workspace.adapter_registry import (
            resolve_curation_domain_pack_by_id,
        )
        from src.lib.domain_packs.validator_dispatch import (
            dispatch_active_validator_bindings,
        )
        from src.schemas.curation_workspace import (
            CurationExtractionResultRecord,
            CurationExtractionSourceKind,
        )
    except Exception as exc:
        logger.warning(
            "Domain-envelope chat validation unavailable for %s: %s",
            specialist_name,
            exc,
        )
        raise SpecialistOutputError(
            specialist_name=specialist_name,
            output_type_name=getattr(expected_output_type, "__name__", "response"),
            message=f"Domain-envelope validator dispatch is unavailable: {exc}",
        ) from exc

    candidate_started_at = time.monotonic()
    candidate = build_extraction_envelope_candidate(
        final_output,
        agent_key=agent_key,
        conversation_summary=f"{specialist_name} chat extraction",
    )
    dispatch_phase_timings_ms["candidate_build_ms"] = _elapsed_ms(
        candidate_started_at
    )
    if candidate is None:
        raise SpecialistOutputError(
            specialist_name=specialist_name,
            output_type_name=getattr(expected_output_type, "__name__", "response"),
            message=(
                "Domain-envelope validator dispatch could not resolve curation "
                f"adapter ownership for agent {agent_key!r}."
            ),
        )

    try:
        envelope_started_at = time.monotonic()
        extraction_record = CurationExtractionResultRecord(
            extraction_result_id=f"chat-runtime:{uuid.uuid4()}",
            document_id="chat-runtime",
            adapter_key=candidate.adapter_key,
            agent_key=candidate.agent_key,
            source_kind=CurationExtractionSourceKind.CHAT,
            candidate_count=candidate.candidate_count,
            conversation_summary=candidate.conversation_summary,
            payload_json=candidate.payload_json,
            created_at=datetime.now(timezone.utc),
            metadata=dict(candidate.metadata),
        )
        envelope = _domain_envelope_from_extraction_result(extraction_record)
        domain_pack = resolve_curation_domain_pack_by_id(envelope.domain_pack_id)
        dispatch_phase_timings_ms["envelope_materialization_ms"] = _elapsed_ms(
            envelope_started_at
        )
        if domain_pack is None:
            error_message = (
                "Domain-envelope validator dispatch could not resolve domain pack "
                f"{envelope.domain_pack_id!r}."
            )
            logger.warning("%s %s", specialist_name, error_message)
            raise SpecialistOutputError(
                specialist_name=specialist_name,
                output_type_name=getattr(expected_output_type, "__name__", "response"),
                message=error_message,
            )

        add_specialist_event({
            "type": "TOOL_START",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {
                "toolName": "dispatch_active_validator_bindings",
                "friendlyName": f"{specialist_name}: Active Validator Dispatch",
                "agent": specialist_name,
                "toolArgs": {
                    "domain_pack_id": envelope.domain_pack_id,
                    "object_count": len(envelope.objects),
                },
                "phaseTimingsMs": dict(dispatch_phase_timings_ms),
                "isSpecialistInternal": True,
            },
        })

        def _emit_validator_dispatch_event(event: dict[str, Any]) -> None:
            event_name = str(event.get("event") or "")
            is_start = event_name == "validator_batch_start"
            is_complete = event_name == "validator_batch_complete"
            if not is_start and not is_complete:
                return
            request_count = int(event.get("request_count") or 0)
            binding_id = event.get("validator_binding_id")
            friendly_action = "start" if is_start else str(event.get("status") or "complete")
            add_specialist_event({
                "type": "TOOL_START" if is_start else "TOOL_COMPLETE",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": {
                    "toolName": "dispatch_active_validator_batch",
                    "friendlyName": (
                        f"{specialist_name}: Validator Batch {friendly_action} "
                        f"({binding_id}, {request_count} request"
                        f"{'' if request_count == 1 else 's'})"
                    ),
                    "agent": specialist_name,
                    "toolArgs": {
                        "validator_binding_id": binding_id,
                        "batch_family": event.get("batch_family"),
                        "request_count": request_count,
                    },
                    "success": event.get("status") != "error",
                    "error": event.get("error"),
                    "isSpecialistInternal": True,
                    "validatorBindingId": binding_id,
                    "validatorAgent": event.get("validator_agent"),
                    "validatorBatchFamily": event.get("batch_family"),
                    "validatorBatchRequestCount": request_count,
                    "validatorBatchRequestIds": event.get("request_ids") or [],
                    "validatorBatchDurationSeconds": event.get("duration_seconds"),
                    "validatorBatchRunnerDurationSeconds": event.get(
                        "runner_duration_seconds"
                    ),
                    "validatorBatchOutputValidationDurationSeconds": event.get(
                        "output_validation_duration_seconds"
                    ),
                    "validatorBatchResolvedCount": event.get("resolved_count"),
                    "validatorBatchUnresolvedCount": event.get("unresolved_count"),
                },
            })

        core_dispatch_started_at = time.monotonic()
        dispatch_result = await asyncio.to_thread(
            dispatch_active_validator_bindings,
            envelope,
            domain_pack,
            event_emitter=_emit_validator_dispatch_event,
            source_envelope_revision=1,
        )
        dispatch_phase_timings_ms["core_dispatch_ms"] = _elapsed_ms(
            core_dispatch_started_at
        )

        has_validator_error = _validator_dispatch_has_error_result(dispatch_result)
        add_specialist_event({
            "type": "TOOL_COMPLETE",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {
                "toolName": "dispatch_active_validator_bindings",
                "friendlyName": _validator_dispatch_completion_label(
                    specialist_name,
                    dispatch_result,
                ),
                "success": not has_validator_error,
                "isSpecialistInternal": True,
                "matchedBindingCount": len(dispatch_result.matched_bindings),
                "validatorResultCount": len(dispatch_result.validator_results),
                "validatorAgentRunCount": getattr(
                    dispatch_result,
                    "validator_agent_run_count",
                    len(dispatch_result.validator_results),
                ),
                "batchValidatorRunCount": getattr(
                    dispatch_result,
                    "batch_validator_run_count",
                    0,
                ),
                "validatorBatchGroups": list(
                    getattr(dispatch_result, "validator_batch_groups", ())
                ),
                "appendedFindingCount": len(dispatch_result.appended_findings),
                "durationMs": _elapsed_ms(dispatch_wall_started_at),
                "durationSeconds": round(
                    _elapsed_ms(dispatch_wall_started_at) / 1000,
                    3,
                ),
                "phaseTimingsMs": dict(dispatch_phase_timings_ms),
            },
        })
        _emit_validator_lookup_audit_events(
            specialist_name=specialist_name,
            dispatch_result=dispatch_result,
        )
        if has_validator_error:
            error_details = _validator_dispatch_error_details(dispatch_result)
            error_message = (
                "Domain-envelope validator dispatch reported execution errors."
            )
            if error_details:
                error_message = str(error_details[0].get("message") or error_message)
            raise SpecialistOutputError(
                specialist_name=specialist_name,
                output_type_name=getattr(expected_output_type, "__name__", "response"),
                message=error_message,
                details=error_details,
            )

        logger.info(
            "%s chat domain-envelope validation dispatched %s binding(s), "
            "%s validator result(s), %s finding(s)",
            specialist_name,
            len(dispatch_result.matched_bindings),
            len(dispatch_result.validator_results),
            len(dispatch_result.appended_findings),
            extra={
                "specialist_name": specialist_name,
                "tool_name": tool_name,
                "domain_pack_id": envelope.domain_pack_id,
                "operation": "chat_domain_envelope_validation",
            },
        )
        serialization_started_at = time.monotonic()
        serialized_envelope = json.dumps(dispatch_result.envelope.model_dump(mode="json"))
        dispatch_phase_timings_ms["result_serialization_ms"] = _elapsed_ms(
            serialization_started_at
        )
        return serialized_envelope
    except SpecialistOutputError as exc:
        logger.warning(
            "Domain-envelope chat validation failed for %s: %s",
            specialist_name,
            exc,
            exc_info=exc,
        )
        add_specialist_event({
            "type": "SPECIALIST_ERROR",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {
                "specialist": specialist_name,
                "error": f"Domain-envelope validator dispatch failed: {exc}",
                "reason": "domain_validator_dispatch_failed",
                "severity": "error",
                "validatorDispatchErrors": getattr(exc, "details", []),
            },
        })
        raise
    except Exception as exc:
        logger.warning(
            "Domain-envelope chat validation failed for %s: %s",
            specialist_name,
            exc,
            exc_info=exc,
        )
        add_specialist_event({
            "type": "SPECIALIST_ERROR",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {
                "specialist": specialist_name,
                "error": f"Domain-envelope validator dispatch failed: {exc}",
                "reason": "domain_validator_dispatch_failed",
                "severity": "error",
            },
        })
        raise SpecialistOutputError(
            specialist_name=specialist_name,
            output_type_name=getattr(expected_output_type, "__name__", "response"),
            message=f"Domain-envelope validator dispatch failed: {exc}",
        ) from exc


# =============================================================================
# BATCHING NUDGE CONFIGURATION
# =============================================================================
# When the supervisor calls the same specialist multiple times in a row,
# we gently remind it that batching is available. This helps prevent
# inefficient patterns like calling ask_gene_specialist 20 times for
# individual genes instead of once with all genes.

BATCHING_NUDGE_CONFIG = {
    "ask_gene_specialist": {
        "example": 'ask_gene_specialist("Look up these genes: daf-16, lin-3, unc-54, ...")',
        "entity": "genes",
    },
    "ask_allele_specialist": {
        "example": 'ask_allele_specialist("Look up these alleles: e1370, n765, tm1234, ...")',
        "entity": "alleles",
    },
    "ask_disease_specialist": {
        "example": 'ask_disease_specialist("Look up these diseases: Alzheimer disease, diabetes mellitus, ...")',
        "entity": "diseases",
    },
    "ask_chemical_specialist": {
        "example": 'ask_chemical_specialist("Look up these chemicals: glucose, ATP, ethanol, ...")',
        "entity": "chemicals",
    },
    "ask_ontology_term_validation_specialist": {
        "example": 'ask_ontology_term_validation_specialist("Resolve these typed ontology terms: anatomy pharynx for WB, life stage L3 larval stage for WB, GO cellular component nucleus, ...")',
        "entity": "terms",
    },
    "ask_gene_ontology_specialist": {
        "example": 'ask_gene_ontology_specialist("Look up these GO terms: apoptotic process, kinase activity, ...")',
        "entity": "GO terms",
    },
    "ask_go_annotations_specialist": {
        "example": 'ask_go_annotations_specialist("Get GO annotations for these genes: WB:WBGene00000912, WB:WBGene00001234, ...")',
        "entity": "genes",
    },
}

# Threshold for triggering the nudge (3 consecutive calls to same specialist)
BATCHING_NUDGE_THRESHOLD = 3


def get_batching_config() -> Dict[str, Any]:
    """
    Generate batching config from AGENT_REGISTRY.

    Returns dict keyed by supervisor tool name (e.g., "ask_gene_specialist")
    with entity and example for batching nudge prompts.

    Falls back to hardcoded BATCHING_NUDGE_CONFIG if registry is not available.
    """
    try:
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
    except ImportError:
        # Fallback to hardcoded if registry not available
        return BATCHING_NUDGE_CONFIG

    config: Dict[str, Any] = {}
    for agent_id, entry in AGENT_REGISTRY.items():
        batching = entry.get("batching")
        if not batching:
            continue

        # Get tool name from supervisor config (single source of truth)
        supervisor = entry.get("supervisor", {})
        tool_name = supervisor.get("tool_name")
        if not tool_name:
            continue

        config[tool_name] = {
            "entity": batching["entity"],
            "example": batching["example"],
        }

    return config


# Track consecutive specialist calls for batching nudge (per-request isolation via ContextVar)
# Format: {"last_tool": "tool_name", "count": N}
# Using ContextVar ensures thread-safety for concurrent requests
_consecutive_call_tracker: ContextVar[Dict[str, Any]] = ContextVar(
    'consecutive_call_tracker',
    default={"last_tool": None, "count": 0}
)


def reset_consecutive_call_tracker():
    """Reset the consecutive call tracker. Call this at the start of a new conversation."""
    _consecutive_call_tracker.set({"last_tool": None, "count": 0})
    logger.debug("Tracker reset for new request")


def _track_specialist_call(tool_name: str) -> int:
    """
    Track a specialist call and return the consecutive count.

    Thread-safe via ContextVar - each request has isolated state.

    Args:
        tool_name: The tool being called (e.g., "ask_gene_specialist")

    Returns:
        The number of consecutive calls to this tool (1 = first call)
    """
    tracker = _consecutive_call_tracker.get()

    if tracker["last_tool"] == tool_name:
        new_count = tracker["count"] + 1
    else:
        new_count = 1

    # Update the tracker with new state
    _consecutive_call_tracker.set({"last_tool": tool_name, "count": new_count})

    logger.debug("%s called, consecutive count: %s", tool_name, new_count)
    return new_count


def _generate_batching_nudge(tool_name: str, consecutive_count: int) -> Optional[str]:
    """
    Generate a batching nudge message if appropriate.

    Only generates a nudge if:
    - The tool supports batching (is in BATCHING_NUDGE_CONFIG)
    - This is exactly the Nth consecutive call (threshold hit)

    Args:
        tool_name: The tool being called
        consecutive_count: How many times in a row this tool has been called

    Returns:
        A nudge message string, or None if no nudge needed
    """
    # Only nudge on exactly the threshold (not every call after)
    if consecutive_count != BATCHING_NUDGE_THRESHOLD:
        return None

    # Check if this tool supports batching (use registry-derived config)
    batching_config = get_batching_config()
    config = batching_config.get(tool_name)
    if not config:
        return None

    entity = config["entity"]
    example = config["example"]

    # Keep the message neutral and helpful
    nudge = f"""

---
Note: You've called this specialist {consecutive_count} times for individual {entity}. If you have more to look up, you can batch them in one call:

{example}

If separate calls are intentional for this task, no problem.
---"""

    logger.info("Generated nudge for %s after %s consecutive calls", tool_name, consecutive_count)
    return nudge


# Context variable to collect specialist internal events (legacy batch mode)
# This allows the supervisor's runner to access events after tool completion
_specialist_events: ContextVar[List[Dict[str, Any]]] = ContextVar(
    'specialist_events', default=[]
)

# ContextVar for live event list (real-time mode) - isolated per async context
# This replaces the previous module-level global that caused race conditions
# when multiple batch jobs ran concurrently (events leaked between batches).
#
# RACE CONDITION FIX (2026-01-23, KANBAN-935):
# The previous global variable allowed Batch A's FILE_READY events to be
# captured by Batch B when they ran concurrently. Using ContextVar ensures
# each batch execution has its own isolated list that cannot be overwritten
# by other concurrent executions.
#
# Note: The previous comment about "ContextVar creates task-local storage that
# doesn't work across SDK contexts" was incorrect - the issue was with the
# global being overwritten by concurrent batches, not ContextVar behavior.
_live_event_list_var: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar(
    'live_event_list', default=None
)


@dataclass
class SpecialistToolCall:
    """Represents an internal tool call made by a specialist."""
    tool_name: str
    tool_args: Optional[Dict[str, Any]] = None
    output_preview: Optional[str] = None
    output_summary: Optional[Dict[str, Any]] = None
    duration_ms: Optional[int] = None


@dataclass
class SpecialistActivity:
    """Summary of a specialist agent's internal activity."""
    specialist_name: str
    tool_calls: List[SpecialistToolCall] = field(default_factory=list)
    total_duration_ms: Optional[int] = None


def get_collected_events() -> List[Dict[str, Any]]:
    """Get all events collected from specialist runs (batch mode)."""
    return _specialist_events.get()


def clear_collected_events():
    """Clear the collected specialist events."""
    _specialist_events.set([])


def set_live_event_list(event_list: Optional[List[Dict[str, Any]]]):
    """
    Set a live event list for real-time event streaming.

    When set, specialist events are appended immediately to this list
    instead of being collected in the ContextVar batch.

    Uses ContextVar for proper isolation between concurrent batch executions.
    Each batch's list is isolated to its own execution context.

    Args:
        event_list: A list to append events to, or None to disable
    """
    _live_event_list_var.set(event_list)
    logger.debug("Live event list set: %s", event_list is not None)


def get_live_event_list() -> Optional[List[Dict[str, Any]]]:
    """Get the current live event list, if any.

    Returns the list from the current execution context (ContextVar).
    """
    return _live_event_list_var.get()


def add_specialist_event(event: Dict[str, Any]):
    """
    Add an event - either push to live list or collect for batch emission.

    If a live list is set (via ContextVar), the event is appended immediately
    for real-time streaming. Otherwise, it's collected for batch emission
    after the specialist completes.

    Uses ContextVar for proper isolation - each concurrent batch execution
    has its own list that cannot be contaminated by other batches.
    """
    write_stream_event(event)

    event_list = _live_event_list_var.get()
    if event_list is not None:
        # Real-time mode: append to list immediately
        # Python's list.append() is thread-safe (GIL protected)
        event_list.append(event)
        logger.debug("Appended event to live list: %s, list_len=%s", event.get("type"), len(event_list))
    else:
        # Batch mode: collect for later emission
        events = _specialist_events.get()
        events.append(event)
        _specialist_events.set(events)


def _emit_chunk_provenance_from_output(tool_name: str, output: str):
    """
    Parse PDF tool output and emit CHUNK_PROVENANCE events for PDF highlighting.

    This enables the frontend PDF viewer to highlight relevant sections based
    on what the agent read/searched.

    Args:
        tool_name: Name of the tool (search_document or read_section)
        output: JSON string output from the tool
    """
    try:
        # Parse the tool output JSON
        if isinstance(output, str):
            data = json.loads(output)
        elif hasattr(output, "model_dump"):
            data = output.model_dump()
        else:
            data = output if isinstance(output, dict) else {}

        if tool_name == "search_document":
            # ChunkSearchResult: {"summary": "...", "hits": [...]}
            hits = data.get("hits", [])
            for hit in hits:
                chunk_id = hit.get("chunk_id")
                if not chunk_id:
                    continue

                # Get doc_items with bounding boxes from the chunk (from PDFX)
                # These contain page, bbox coordinates for PDF highlighting
                doc_items = hit.get("doc_items") or []

                if not doc_items:
                    # Fallback: create minimal doc_items if none available
                    page_number = hit.get("page_number")
                    if page_number:
                        doc_items = [{"page": page_number}]
                    else:
                        logger.debug("Chunk %s has no doc_items or page_number, skipping", chunk_id)
                        continue

                # Emit CHUNK_PROVENANCE event
                event_payload = {
                    "type": "CHUNK_PROVENANCE",
                    "message_id": str(uuid.uuid4()),
                    "chunk_id": chunk_id,
                    "doc_items": doc_items,
                    "source_tool": tool_name,
                }
                add_specialist_event(event_payload)

        elif tool_name == "read_section":
            # SectionReadResult: {"summary": "...", "section": {...}}
            section = data.get("section")
            if section:
                section_title = section.get("section_title")

                # Get doc_items with bounding boxes from all chunks in the section
                doc_items = section.get("doc_items") or []

                if not doc_items:
                    logger.debug("Section '%s' has no doc_items, skipping provenance", section_title)
                    return

                # Emit CHUNK_PROVENANCE event with the section's doc_items
                add_specialist_event({
                    "type": "CHUNK_PROVENANCE",
                    "message_id": str(uuid.uuid4()),
                    "chunk_id": f"section:{section_title}",
                    "doc_items": doc_items,
                    "source_tool": tool_name,
                })
                logger.debug(
                    "Emitted CHUNK_PROVENANCE for section '%s' with %s doc_items",
                    section_title,
                    len(doc_items),
                )

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse %s output for chunk provenance: %s", tool_name, e)
    except Exception as e:
        logger.warning("Error extracting chunk provenance from %s: %s", tool_name, e)


async def run_specialist_with_events(
    agent: Agent,
    input_text: str,
    specialist_name: str,
    run_config: Optional[RunConfig] = None,
    max_turns: Optional[int] = None,
    tool_name: Optional[str] = None,
) -> str:
    """
    Run a specialist agent and collect its internal tool call events.

    This function uses Runner.run_streamed() to capture internal activity
    and stores events that can be emitted by the supervisor's runner.

    Args:
        agent: The specialist agent to run
        input_text: The input/query for the specialist
        specialist_name: Human-readable name for logging
        run_config: Optional run configuration
        max_turns: Maximum turns for the specialist
        tool_name: The tool name (e.g., "ask_gene_specialist") for batching nudge tracking

    Returns:
        The specialist's final output as a string
    """
    start_time = datetime.now(timezone.utc)
    wall_started_at = time.monotonic()
    phase_timings_ms: Dict[str, int] = {}
    tool_calls: List[SpecialistToolCall] = []
    live_evidence_records: List[Dict[str, Any]] = []
    pending_tool_calls: "deque[Dict[str, Any]]" = deque()

    # Track consecutive calls for batching nudge
    consecutive_count = 0
    if tool_name:
        consecutive_count = _track_specialist_call(tool_name)
    else:
        logger.warning("tool_name is None for %s, skipping consecutive call tracking", specialist_name)

    # Use config default if not specified
    if max_turns is None:
        max_turns = get_max_turns()
    max_turns = _compute_adaptive_specialist_max_turns(
        agent=agent,
        input_text=input_text,
        base_max_turns=max_turns,
    )

    logger.info(
        "Starting specialist=%s (max_turns=%s)",
        specialist_name,
        max_turns,
        extra={"specialist_name": specialist_name, "tool_name": tool_name},
    )

    expected_output_type = getattr(agent, "output_type", None)
    groq_tool_json_compat_mode = False
    runtime_agent = agent

    # Groq currently rejects response_format + tools in the same request.
    # For this provider/path, run with tools and plain-text JSON, then validate.
    if _should_use_groq_tool_json_compat(agent):
        groq_tool_json_compat_mode = True
        json_only_instruction = _build_json_only_instruction(expected_output_type)
        runtime_agent = copy.copy(agent)
        runtime_agent.output_type = None
        runtime_agent.tools = _adapt_tools_for_groq_schema_constraints(
            list(getattr(agent, "tools", []) or [])
        )
        runtime_agent = _append_agent_runtime_instruction(
            runtime_agent,
            agent,
            instruction=json_only_instruction,
            layer_id_suffix="groq_json_only",
            title="Groq JSON-only runtime instruction",
            source_ref="src.lib.openai_agents.streaming_tools:groq_json_only",
        )
        logger.info(
            "%s enabling Groq JSON/tool compatibility mode (output_type disabled for initial run)",
            specialist_name,
            extra={"specialist_name": specialist_name, "tool_name": tool_name},
        )
    else:
        relaxed_agent = _apply_relaxed_output_schema_if_needed(
            runtime_agent,
            expected_output_type,
        )
        if relaxed_agent is not runtime_agent:
            runtime_agent = relaxed_agent
            logger.info(
                "%s using relaxed output schema for domain-envelope structured output",
                specialist_name,
                extra={"specialist_name": specialist_name, "tool_name": tool_name},
            )

    efficiency_instruction = _build_tool_efficiency_instruction(agent, input_text)
    if efficiency_instruction:
        runtime_agent = _append_agent_runtime_instruction(
            runtime_agent,
            agent,
            instruction=efficiency_instruction,
            layer_id_suffix="tool_efficiency",
            title="Tool efficiency runtime instruction",
            source_ref="src.lib.openai_agents.streaming_tools:tool_efficiency",
        )
        logger.info(
            "%s applying tool-efficiency instruction for large list workload",
            specialist_name,
            extra={"specialist_name": specialist_name, "tool_name": tool_name},
        )

    # Commit pending prompts for this specialist - moves from pending to used
    # This is where the agent ACTUALLY executes, so we log the prompts now
    commit_pending_prompts(runtime_agent)

    # Create a run config that disables tracing to avoid OpenTelemetry context conflicts
    # The parent supervisor run already has tracing enabled via Langfuse
    effective_config = run_config or RunConfig()
    effective_config = RunConfig(
        model_provider=effective_config.model_provider if hasattr(effective_config, 'model_provider') else None,
        tracing_disabled=True,  # Disable to avoid nested context issues
    )

    # Run with streaming to capture internal events
    runner_create_started_at = time.monotonic()
    result = Runner.run_streamed(
        runtime_agent,
        input=input_text,
        max_turns=max_turns,
        run_config=effective_config
    )
    phase_timings_ms["runner_create_ms"] = _elapsed_ms(runner_create_started_at)
    write_extraction_trace_event(
        event_type="model.reasoning_summary.request",
        input_summary=_reasoning_request_metadata(runtime_agent),
        metadata={"agent": specialist_name, "tool_name": tool_name},
    )

    # Event tracking for debugging
    total_event_count = 0
    event_type_counts: dict = {}
    is_generating = False  # Track if we've emitted AGENT_GENERATING
    reasoning_summary_chunks: List[str] = []

    stream_consume_started_at = time.monotonic()
    evidence_workspace_token = set_active_evidence_records(live_evidence_records)
    trace_run = get_current_extraction_trace_run()
    parent_builder_workspace = _active_builder_workspace_or_none()
    builder_workspace = ExtractionBuilderWorkspace(
        run_id=trace_run.trace_id if trace_run is not None else str(uuid.uuid4()),
        document_id=(
            parent_builder_workspace.document_id
            if parent_builder_workspace is not None
            else None
        ),
        domain_pack_id=(
            parent_builder_workspace.domain_pack_id
            if parent_builder_workspace is not None
            else None
        ),
        agent_id=specialist_name,
    )
    builder_workspace_token = set_active_extraction_builder_workspace(builder_workspace)
    try:
        async for event in result.stream_events():
            total_event_count += 1
            event_type = getattr(event, "type", None)

            # Count all event types for debugging summary
            event_type_key = event_type or "unknown"
            event_type_counts[event_type_key] = event_type_counts.get(event_type_key, 0) + 1

            # Log ALL events at debug level for comprehensive visibility
            if total_event_count <= 5 or total_event_count % 10 == 0:
                # Log first 5 events and then every 10th to avoid spam
                logger.debug(
                    "%s event #%s: type=%s, event_class=%s",
                    specialist_name,
                    total_event_count,
                    event_type,
                    type(event).__name__,
                )

            # Handle raw_response_event - shows model responses
            if event_type == "raw_response_event":
                data = getattr(event, "data", None)
                if data:
                    # Log response metadata
                    response_type = type(data).__name__

                    # Capture text from ResponseTextDeltaEvent - this shows what the model
                    # is writing when it generates text instead of structured output
                    if response_type == "ResponseTextDeltaEvent":
                        delta_text = getattr(data, "delta", "")
                        if delta_text:
                            # Emit AGENT_GENERATING once when text streaming starts
                            # This provides visual feedback in the audit panel
                            if not is_generating:
                                is_generating = True
                                logger.debug("%s generating response (emitting AGENT_GENERATING)", specialist_name)
                                add_specialist_event({
                                    "type": "AGENT_GENERATING",
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "details": {
                                        "agentRole": specialist_name,
                                        "agentDisplayName": specialist_name,
                                        "message": "Agent reasoning"
                                    }
                                })

                            # Accumulate text for logging (track in a variable)
                            if not hasattr(result, "_accumulated_text"):
                                result._accumulated_text = ""
                            result._accumulated_text += delta_text

                            # Log periodically (every 500 chars) to avoid spam
                            text_len = len(result._accumulated_text)
                            if text_len <= 200 or text_len % 500 < len(delta_text):
                                preview = result._accumulated_text[-200:] if text_len > 200 else result._accumulated_text
                                logger.debug("%s TEXT OUTPUT (%s chars): ...%s", specialist_name, text_len, preview)

                    # Capture reasoning summary delta events (GPT-5 reasoning mode)
                    elif response_type == "ResponseReasoningSummaryPartDoneEvent":
                        # This event contains a part of the reasoning summary
                        part = getattr(data, "part", None)
                        if part:
                            text = getattr(part, "text", None)
                            if text:
                                write_extraction_trace_event(
                                    event_type="model.reasoning_summary.output",
                                    output_summary={"summary_text": text},
                                    metadata={
                                        "agent": specialist_name,
                                        "tool_name": tool_name,
                                        "availability": "present",
                                    },
                                )
                                logger.debug(
                                    "%s REASONING SUMMARY PART: %s",
                                    specialist_name,
                                    text[:300] + ("..." if len(text) > 300 else ""),
                                )

                    elif response_type == "ResponseReasoningSummaryTextDeltaEvent":
                        # This event streams reasoning summary text deltas
                        delta = getattr(data, "delta", "")
                        if delta:
                            reasoning_summary_chunks.append(delta)
                            write_extraction_trace_event(
                                event_type="model.reasoning_summary.delta",
                                output_summary={"summary_text": delta},
                                metadata={
                                    "agent": specialist_name,
                                    "tool_name": tool_name,
                                    "availability": "present",
                                },
                            )
                            # Accumulate reasoning for logging
                            if not hasattr(result, "_accumulated_reasoning"):
                                result._accumulated_reasoning = ""
                            result._accumulated_reasoning += delta

                            # Log periodically
                            reasoning_len = len(result._accumulated_reasoning)
                            if reasoning_len <= 200 or reasoning_len % 500 < len(delta):
                                preview = result._accumulated_reasoning[-200:] if reasoning_len > 200 else result._accumulated_reasoning
                                logger.debug(
                                    "%s REASONING DELTA (%s chars): ...%s",
                                    specialist_name,
                                    reasoning_len,
                                    preview,
                                )

                    elif response_type == "ResponseReasoningSummaryTextDoneEvent":
                        # Final reasoning summary text
                        text = getattr(data, "text", "")
                        if text:
                            write_extraction_trace_event(
                                event_type="model.reasoning_summary.output",
                                output_summary={"summary_text": text},
                                metadata={
                                    "agent": specialist_name,
                                    "tool_name": tool_name,
                                    "availability": "present",
                                },
                            )
                            logger.debug(
                                "%s REASONING COMPLETE (%s chars): %s",
                                specialist_name,
                                len(text),
                                text[:500] + ("..." if len(text) > 500 else ""),
                            )

                    elif response_type == "ResponseTextDoneEvent":
                        # Log final text when text generation completes
                        full_text = getattr(data, "text", "")
                        if full_text:
                            result._final_text_output = full_text
                            logger.warning(
                                "%s GENERATED TEXT INSTEAD OF STRUCTURED OUTPUT! Length: %s chars. First 500: %s...",
                                specialist_name,
                                len(full_text),
                                full_text[:500],
                                extra={"specialist_name": specialist_name},
                            )
                    elif response_type not in ("ResponseFunctionCallArgumentsDeltaEvent",):
                        # Log other response types (but not the spammy argument deltas)
                        logger.debug("%s raw_response: type=%s", specialist_name, response_type)

                        # Extra logging for any Reasoning-related events we might have missed
                        if "Reasoning" in response_type:
                            logger.debug("%s REASONING EVENT: %s", specialist_name, response_type)
                            # Try to extract any useful content from the event data
                            for attr in ["delta", "text", "summary", "part", "content"]:
                                if hasattr(data, attr):
                                    value = getattr(data, attr, None)
                                    if value:
                                        logger.debug(
                                            "%s REASONING.%s: %s",
                                            specialist_name,
                                            attr,
                                            str(value)[:300] + ("..." if len(str(value)) > 300 else ""),
                                        )

                    # Check for output content in the response
                    if hasattr(data, "output"):
                        output_items = getattr(data, "output", [])
                        if output_items:
                            logger.debug("%s response has %s output items", specialist_name, len(output_items))
                            for i, item in enumerate(output_items[:3]):  # Log first 3 items
                                item_type = getattr(item, "type", type(item).__name__)
                                logger.debug("%s output[%s]: type=%s", specialist_name, i, item_type)

            if event_type == "run_item_stream_event":
                item = getattr(event, "item", None)
                if item is not None:
                    item_type = getattr(item, "type", None)

                    # Log ALL item types for debugging (not just tool calls)
                    if item_type not in ("tool_call_item", "tool_call_output_item"):
                        # Log non-tool item types at INFO level
                        logger.debug(
                            "%s item: type=%s, item_class=%s",
                            specialist_name,
                            item_type,
                            type(item).__name__,
                        )

                        # Special handling for reasoning_item - log the reasoning content
                        if item_type == "reasoning_item":
                            reasoning_content = ""

                            # Check for summary attribute (per OpenAI docs, this is the key attribute)
                            if hasattr(item, "summary"):
                                reasoning_content = _reasoning_summary_text(getattr(item, "summary", None))

                            # Check raw_item for nested content
                            if not reasoning_content and hasattr(item, "raw_item"):
                                raw = getattr(item, "raw_item", None)
                                if raw:
                                    # Try to get summary from raw_item
                                    if hasattr(raw, "summary"):
                                        reasoning_content = _reasoning_summary_text(getattr(raw, "summary", None))

                            if reasoning_content:
                                write_extraction_trace_event(
                                    event_type="model.reasoning_summary.output",
                                    output_summary={"summary_text": reasoning_content},
                                    metadata={
                                        "agent": specialist_name,
                                        "tool_name": tool_name,
                                        "availability": "present",
                                    },
                                )
                                # Log reasoning content (truncate to 500 chars for readability)
                                content_preview = str(reasoning_content)[:500]
                                logger.debug(
                                    "%s REASONING ITEM (%s chars): %s",
                                    specialist_name,
                                    len(str(reasoning_content)),
                                    content_preview + ("..." if len(str(reasoning_content)) > 500 else ""),
                                )
                            else:
                                # Log all attributes of the item to understand its structure
                                attrs = [a for a in dir(item) if not a.startswith('_')]
                                logger.debug("%s reasoning_item attributes: %s", specialist_name, attrs)
                                # Also dump the item to see what's in it
                                try:
                                    if hasattr(item, "model_dump"):
                                        item_dict = item.model_dump()
                                        logger.debug("%s reasoning_item dump: %s", specialist_name, str(item_dict)[:500])
                                except Exception as e:
                                    logger.debug("Could not dump reasoning_item: %s", e)

                        # Try to extract any content from the item
                        if hasattr(item, "content"):
                            content = getattr(item, "content", None)
                            if content:
                                content_preview = str(content)[:100]
                                logger.debug("%s item content: %s...", specialist_name, content_preview)
                        if hasattr(item, "text"):
                            text = getattr(item, "text", None)
                            if text:
                                text_preview = str(text)[:100]
                                logger.debug("%s item text: %s...", specialist_name, text_preview)
                        if hasattr(item, "raw_item"):
                            raw = getattr(item, "raw_item", None)
                            if raw:
                                logger.debug("%s raw_item type: %s", specialist_name, type(raw).__name__)

                    if item_type == "tool_call_item":
                        # Reset is_generating flag - new tool call means a new generation phase after
                        is_generating = False

                        tool_started_at = datetime.now(timezone.utc)
                        current_tool_name = (
                            getattr(item, "name", None) or
                            getattr(item, "tool_name", None) or
                            getattr(getattr(item, "raw_item", None), "name", None) or
                            "unknown_tool"
                        )

                        # Try to get tool arguments
                        tool_args = None
                        raw_item = getattr(item, "raw_item", None)
                        if raw_item:
                            tool_args_str = getattr(raw_item, "arguments", None)
                            if tool_args_str:
                                try:
                                    tool_args = json.loads(tool_args_str)
                                except Exception:
                                    pass

                        logger.info(
                            "%s calling: %s",
                            specialist_name,
                            current_tool_name,
                            extra={"specialist_name": specialist_name, "tool_name": current_tool_name},
                        )
                        tool_call_id = _extract_stream_tool_call_tracking_id(item)

                        # Emit event for real-time visibility
                        # Use standard TOOL_START type so frontend can display it
                        add_specialist_event({
                            "type": "TOOL_START",
                            "timestamp": tool_started_at.isoformat(),
                            "details": {
                                "toolName": current_tool_name,
                                "friendlyName": build_specialist_internal_friendly_name(
                                    specialist_name,
                                    current_tool_name,
                                ),
                                "agent": specialist_name,
                                "toolArgs": tool_args,
                                "toolCallId": tool_call_id,
                                "isSpecialistInternal": True  # Mark as internal specialist tool
                            }
                        })

                        # Start building the tool call record
                        tool_index = len(tool_calls)
                        tool_calls.append(SpecialistToolCall(
                            tool_name=current_tool_name,
                            tool_args=tool_args
                        ))
                        pending_tool_calls.append({
                            "tool_name": current_tool_name,
                            "tool_args": tool_args,
                            "tool_id": tool_call_id,
                            "tool_index": tool_index,
                            "tool_started_at": tool_started_at,
                        })

                    elif item_type == "tool_call_output_item":
                        completed_tool = _pop_matching_pending_tool_call(
                            pending_tool_calls,
                            output_item=item,
                        )
                        if completed_tool is None:
                            logger.debug(
                                "%s received tool output without prior tool call, skipping",
                                specialist_name,
                            )
                            continue
                        current_tool_name = str(completed_tool.get("tool_name") or "unknown_tool")

                        output = getattr(item, "output", "")
                        output_preview = str(output)[:200]
                        if len(str(output)) > 200:
                            output_preview += "..."

                        duration_ms = None
                        tool_started_at = completed_tool.get("tool_started_at")
                        if isinstance(tool_started_at, datetime):
                            duration = datetime.now(timezone.utc) - tool_started_at
                            duration_ms = int(duration.total_seconds() * 1000)

                        logger.info(
                            "%s %s complete (%sms)",
                            specialist_name,
                            current_tool_name,
                            duration_ms,
                            extra={
                                "specialist_name": specialist_name,
                                "tool_name": current_tool_name,
                                "duration_ms": duration_ms,
                                "operation": "specialist_tool_execution",
                            },
                        )

                        # Update the last tool call with output info
                        tool_index = completed_tool.get("tool_index")
                        output_summary = _tool_output_summary(current_tool_name, output)
                        if isinstance(tool_index, int) and 0 <= tool_index < len(tool_calls):
                            tool_calls[tool_index].output_preview = output_preview
                            tool_calls[tool_index].output_summary = output_summary
                            tool_calls[tool_index].duration_ms = duration_ms

                        evidence_record = build_record_evidence_summary_record(
                            tool_name=current_tool_name,
                            tool_input=completed_tool.get("tool_args"),
                            tool_output=output,
                        )
                        if evidence_record is not None:
                            live_evidence_records.append(evidence_record)

                        # Extract chunk provenance from PDF tool outputs for highlighting
                        if current_tool_name in ("search_document", "read_section"):
                            _emit_chunk_provenance_from_output(current_tool_name, output)

                        # Emit event for real-time visibility
                        # Use standard TOOL_COMPLETE type so frontend can display it
                        completed_tool_id = completed_tool.get("tool_id")
                        add_specialist_event({
                            "type": "TOOL_COMPLETE",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "details": {
                                "toolName": current_tool_name,
                                "friendlyName": build_specialist_internal_friendly_name(
                                    specialist_name,
                                    current_tool_name,
                                    complete=True,
                                ),
                                "success": True,
                                "durationMs": duration_ms,
                                "toolCallId": completed_tool_id,
                                "isSpecialistInternal": True  # Mark as internal specialist tool
                            },
                            "internal": {
                                "tool_output": output,
                                "output_length": len(str(output)),
                                "output_preview": output_preview,
                                "output_summary": output_summary,
                            },
                        })

                        # Check if tool output contains FileInfo (file download)
                        # File formatter tools (save_csv_file, etc.) return FileInfo as JSON
                        if output:
                            try:
                                output_data = json.loads(str(output)) if isinstance(output, str) else output
                                # Check for FileInfo signature: must have file_id, download_url, filename
                                if (
                                    isinstance(output_data, dict) and
                                    output_data.get("file_id") and
                                    output_data.get("download_url") and
                                    output_data.get("filename")
                                ):
                                    logger.info(
                                        "File output detected from %s: %s (%s)",
                                        specialist_name,
                                        output_data.get("filename"),
                                        output_data.get("format"),
                                    )
                                    # Emit FILE_READY event for frontend to render FileDownloadCard
                                    add_specialist_event({
                                        "type": "FILE_READY",
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                        "details": {
                                            "file_id": output_data.get("file_id"),
                                            "filename": output_data.get("filename"),
                                            "format": output_data.get("format"),
                                            "size_bytes": output_data.get("size_bytes"),
                                            "mime_type": output_data.get("mime_type"),
                                            "download_url": output_data.get("download_url"),
                                            "created_at": output_data.get("created_at"),
                                        }
                                    })
                            except (json.JSONDecodeError, TypeError, AttributeError) as e:
                                # Not JSON or not FileInfo - this is normal for most tools
                                logger.debug("FileInfo detection skipped: %s", type(e).__name__)

        # Log comprehensive event summary for debugging
        logger.info(
            "%s stream completed normally. Total events: %s, Event types: %s",
            specialist_name,
            total_event_count,
            event_type_counts,
        )
        phase_timings_ms["stream_consume_ms"] = _elapsed_ms(
            stream_consume_started_at
        )

    except asyncio.CancelledError:
        phase_timings_ms["stream_consume_ms"] = _elapsed_ms(
            stream_consume_started_at
        )
        builder_workspace.mark_cancelled(reason="specialist stream cancelled")
        raise
    except Exception as e:
        phase_timings_ms["stream_consume_ms"] = _elapsed_ms(
            stream_consume_started_at
        )
        if type(e).__name__ == "MaxTurnsExceeded":
            error_message = (
                f"{specialist_name} reached max turns ({max_turns}) after "
                f"{len(tool_calls)} internal tool calls."
            )
            add_specialist_event({
                "type": "SPECIALIST_ERROR",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": {
                    "specialist": specialist_name,
                    "error": error_message,
                    "reason": "max_turns_exceeded",
                    "severity": "error",
                }
            })
        logger.error(
            "%s stream error: %s: %s. Events before error: %s, Event types: %s",
            specialist_name,
            type(e).__name__,
            e,
            total_event_count,
            event_type_counts,
        )
        builder_workspace.mark_aborted(reason=f"{type(e).__name__}: {e}")
        raise
    finally:
        reset_active_evidence_records(evidence_workspace_token)
        reset_active_extraction_builder_workspace(builder_workspace_token)

    stream_duration = datetime.now(timezone.utc) - start_time
    stream_duration_ms = int(stream_duration.total_seconds() * 1000)

    post_stream_started_at = time.monotonic()
    builder_candidate_id = f"{tool_name or specialist_name}:structured_result"
    required_tool_error = _required_tool_failure_message(
        agent=runtime_agent,
        specialist_name=specialist_name,
        tool_calls=tool_calls,
    )
    if required_tool_error:
        output_type_name = getattr(expected_output_type, "__name__", "response")
        logger.error(
            "%s required-tool enforcement failure: %s",
            specialist_name,
            required_tool_error,
        )
        add_specialist_event({
            "type": "SPECIALIST_ERROR",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {
                "specialist": specialist_name,
                "output_type": output_type_name,
                "error": required_tool_error,
                "reason": "required_tool_not_called",
                "severity": "error",
            }
        })
        _record_builder_specialist_output_failure(
            builder_workspace=builder_workspace,
            specialist_name=specialist_name,
            tool_name=tool_name,
            output_type_name=output_type_name,
            reason="required_tool_not_called",
            message=required_tool_error,
            candidate_id=builder_candidate_id,
        )
        raise SpecialistOutputError(
            specialist_name=specialist_name,
            output_type_name=output_type_name,
                message=required_tool_error,
        )

    # Get final output - handle both structured and string outputs
    final_output = ""
    logger.info(
        "%s checking final_output: hasattr=%s, value=%s, type=%s",
        specialist_name,
        hasattr(result, "final_output"),
        getattr(result, "final_output", "N/A"),
        type(getattr(result, "final_output", None)),
    )

    if hasattr(result, "final_output") and result.final_output is not None:
        if hasattr(result.final_output, "model_dump"):
            # Structured output (Pydantic model)
            final_output = json.dumps(result.final_output.model_dump())
            logger.info("%s final_output is Pydantic model: %s...", specialist_name, final_output[:200])
        else:
            # String output
            final_output = str(result.final_output)
            logger.info("%s final_output is string: %s...", specialist_name, final_output[:200])

            # PDF specialist may emit a markdown field table (answer/citations/sources).
            # Normalize to plain answer text so supervisor receives concise tool output.
            if expected_output_type is None:
                markdown_candidate = _try_parse_markdown_field_table(final_output)
                answer_text = (
                    str(markdown_candidate.get("answer", "")).strip()
                    if isinstance(markdown_candidate, dict)
                    else ""
                )
                if answer_text:
                    final_output = answer_text
                    logger.info(
                        "%s normalized markdown table output to plain answer text (%s chars)",
                        specialist_name,
                        len(final_output),
                    )

            # Groq compatibility path: we intentionally disabled SDK-level structured
            # outputs when tools are present; recover structure by validating text JSON.
            if groq_tool_json_compat_mode and expected_output_type is not None:
                validated_output = _try_validate_json_output(final_output, expected_output_type)
                if validated_output:
                    final_output = validated_output
                    logger.info(
                        "%s Groq compatibility parse succeeded: validated text output as %s",
                        specialist_name,
                        expected_output_type.__name__,
                    )
                else:
                    logger.warning(
                        "%s Groq compatibility parse failed: returning raw text output",
                        specialist_name,
                    )
    else:
        logger.warning("%s has no final_output!", specialist_name)

        # =============================================================================
        # PLAIN TEXT OUTPUT FALLBACK
        # =============================================================================
        # Text extracted from SDK message items may be used only for unstructured
        # specialists. Structured extraction output must come from the SDK final_output
        # path so model-authored JSON text cannot become the canonical builder payload.

        output_type = expected_output_type

        # Extract complete text from result.new_items for unstructured specialists.
        text_from_items = None
        try:
            from agents.items import ItemHelpers
            if hasattr(result, 'new_items') and result.new_items:
                logger.info(
                    "%s Checking new_items for text output (%s items)",
                    specialist_name,
                    len(result.new_items),
                )
                for item in reversed(result.new_items):
                    item_type = getattr(item, 'type', None)
                    logger.debug("%s new_items item: type=%s", specialist_name, item_type)
                    if item_type == 'message_output_item':
                        text_from_items = ItemHelpers.text_message_output(item)
                        if text_from_items:
                            logger.info(
                                "%s Found complete text in new_items (%s chars). First 200: %s...",
                                specialist_name,
                                len(text_from_items),
                                text_from_items[:200],
                            )
                            break
            else:
                logger.warning(
                    "%s new_items is empty or missing! hasattr=%s, value=%s",
                    specialist_name,
                    hasattr(result, "new_items"),
                    getattr(result, "new_items", "N/A"),
                )
        except Exception as e:
            logger.warning(
                "%s Error extracting from new_items: %s: %s",
                specialist_name,
                type(e).__name__,
                e,
            )

        if text_from_items and output_type is not None:
            logger.warning(
                "%s produced text in new_items but requires %s structured output; "
                "ignoring text so it cannot become canonical extraction JSON",
                specialist_name,
                output_type.__name__,
            )
        elif text_from_items:
            # Plain text agent - use text_from_items directly as output
            logger.info(
                "%s: Using text from new_items as plain text output (%s chars)",
                specialist_name,
                len(text_from_items),
            )
            final_output = text_from_items
        elif output_type is not None:
            logger.warning(
                "%s: No text found in new_items, cannot extract %s",
                specialist_name,
                output_type.__name__,
            )

        # =============================================================================
        # STREAMING TEXT FALLBACK
        # =============================================================================
        # GPT-5 + reasoning mode may not include a message_output_item in new_items,
        # but the text IS streamed via ResponseTextDeltaEvent and accumulated in
        # result._accumulated_text. Use this as a last-resort fallback for plain text agents.
        if (
            not final_output
            and output_type is None
            and hasattr(result, "_accumulated_text")
            and result._accumulated_text
        ):
            accumulated_text = result._accumulated_text.strip()
            if accumulated_text:
                logger.info(
                    "%s STREAMING TEXT FALLBACK: Using accumulated text from stream (%s chars) since new_items had no message_output_item",
                    specialist_name,
                    len(accumulated_text),
                )
                final_output = accumulated_text

                # Emit audit event for visibility
                add_specialist_event({
                    "type": "SPECIALIST_TEXT_FALLBACK_SUCCESS",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "details": {
                        "specialist": specialist_name,
                        "text_length": len(final_output),
                        "extraction_method": "streaming_text_fallback",
                        "message": f"{specialist_name} output extracted from streaming deltas (GPT-5 reasoning mode workaround)"
                    }
                })

        # If text fallback succeeded, skip the retry mechanism
        if final_output:
            logger.info(
                "%s TEXT FALLBACK: Skipping retry mechanism - output successfully extracted from text",
                specialist_name,
            )
        else:
            # =============================================================================
            # RETRY MECHANISM FOR EMPTY OUTPUT
            # =============================================================================
            # When a specialist completes tool calls but fails to produce structured output,
            # attempt one retry with a nudge prompt asking the model to synthesize its findings.

            # output_type already fetched above for text fallback
            if output_type is not None:
                output_type_name = output_type.__name__

                logger.warning(
                    "%s produced no output but expects %s. Attempting retry with nudge prompt...",
                    specialist_name,
                    output_type_name,
                )

                # Emit retry audit event
                add_specialist_event({
                    "type": "SPECIALIST_RETRY",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "details": {
                        "specialist": specialist_name,
                        "reason": "empty_output",
                        "output_type": output_type_name,
                        "message": f"{specialist_name} completed tool calls but did not produce output. Retrying..."
                    }
                })

                # Nudge prompt - ask model to produce the required structured output
                nudge_prompt = (
                    f"You completed your tool calls but did not produce the required {output_type_name} structured output. "
                    f"Please synthesize your findings from the previous tool calls into the structured output now. "
                    f"You MUST produce the {output_type_name} before finishing."
                )

                try:
                    # Get conversation history from the failed run so the model knows what was searched
                    # This is CRITICAL - without history, the model has no context to synthesize
                    previous_items = result.to_input_list()

                    # Append nudge prompt to the conversation history
                    retry_input = previous_items + [{"role": "user", "content": nudge_prompt}]

                    logger.info(
                        "%s retry: including %s previous items plus nudge prompt",
                        specialist_name,
                        len(previous_items),
                    )

                    # Create a simplified "retry agent" WITHOUT output_guardrails
                    # The original agent's output_guardrail checks for tool calls, but during retry
                    # we're just asking for output synthesis (no new tool calls). This would cause
                    # the guardrail to trip and return final_output=None immediately.
                    # Solution: Create a minimal agent that only focuses on structured output generation.

                    # Get model from original agent, or fall back to configured default model.
                    from .config import get_default_model
                    retry_model = getattr(agent, 'model', None) or get_default_model()

                    retry_agent = Agent(
                        name=f"{specialist_name} (Retry)",
                        instructions=(
                            f"You are completing the work of the {specialist_name}. "
                            f"You have already gathered information through tool calls (shown in the conversation history). "
                            f"Your ONLY task now is to synthesize this information into the required {output_type_name} structured output. "
                            f"Do NOT attempt to call any tools. Just analyze the previous tool results and produce the output."
                        ),
                        model=retry_model,
                        output_type=output_type,
                        # NO tools - we don't want new searches, just synthesis
                        tools=[],
                        # NO output_guardrails - the original guardrail would trip with 0 tool calls
                        output_guardrails=[],
                    )

                    logger.info(
                        "%s retry: created simplified retry agent without tools or guardrails for output synthesis",
                        specialist_name,
                    )

                    # Re-run with nudge (reduced max_turns since we just need output synthesis)
                    logger.info(
                        "%s retry: starting Runner.run_streamed with model=%s",
                        specialist_name,
                        retry_model,
                    )

                    retry_start_time = datetime.now(timezone.utc)
                    retry_result = Runner.run_streamed(
                        retry_agent,  # Use simplified retry agent, NOT original agent
                        input=retry_input,  # Include full conversation history
                        max_turns=5,  # Reduced turns - just need output synthesis
                        run_config=effective_config
                    )

                    # Consume the retry stream with debug logging
                    retry_event_count = 0
                    async for retry_event in retry_result.stream_events():
                        retry_event_count += 1
                        # Log every event type for debugging
                        event_type = getattr(retry_event, 'type', str(type(retry_event).__name__))
                        logger.debug("%s retry event %s: %s", specialist_name, retry_event_count, event_type)

                    retry_duration_ms = (datetime.now(timezone.utc) - retry_start_time).total_seconds() * 1000
                    logger.info(
                        "%s retry stream consumed: %s events in %.0fms",
                        specialist_name,
                        retry_event_count,
                        retry_duration_ms,
                    )

                    # Debug: Log retry_result attributes
                    logger.info(
                        "%s retry result inspection: has final_output attr=%s, final_output value=%s, type=%s",
                        specialist_name,
                        hasattr(retry_result, "final_output"),
                        getattr(retry_result, "final_output", "N/A"),
                        type(getattr(retry_result, "final_output", None)),
                    )

                    # Check retry result
                    if hasattr(retry_result, "final_output") and retry_result.final_output is not None:
                        # Retry succeeded!
                        if hasattr(retry_result.final_output, "model_dump"):
                            final_output = json.dumps(retry_result.final_output.model_dump())
                        else:
                            final_output = str(retry_result.final_output)

                        logger.info(
                            "%s retry SUCCEEDED! Output length: %s",
                            specialist_name,
                            len(final_output),
                        )

                        # Emit success event (warning severity - something unusual happened)
                        add_specialist_event({
                            "type": "SPECIALIST_RETRY_SUCCESS",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "details": {
                                "specialist": specialist_name,
                                "output_type": output_type_name,
                                "output_length": len(final_output),
                                "message": f"{specialist_name} successfully produced output on retry"
                            }
                        })
                    else:
                        # Retry also failed - emit ERROR audit event and raise exception
                        error_message = (
                            f"{specialist_name} failed to produce {output_type_name} output "
                            f"after retry. The specialist completed tool calls but could not "
                            f"synthesize the results into the required format."
                        )

                        logger.error(
                            "%s retry FAILED! Still no output after nudge prompt. Events consumed: %s, Duration: %.0fms",
                            specialist_name,
                            retry_event_count,
                            retry_duration_ms,
                        )

                        # Emit ERROR audit event so it shows in the audit panel
                        add_specialist_event({
                            "type": "SPECIALIST_ERROR",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "details": {
                                "specialist": specialist_name,
                                "output_type": output_type_name,
                                "error": error_message,
                                "retry_events": retry_event_count,
                                "retry_duration_ms": retry_duration_ms,
                                "severity": "error"
                            }
                        })

                        _record_builder_specialist_output_failure(
                            builder_workspace=builder_workspace,
                            specialist_name=specialist_name,
                            tool_name=tool_name,
                            output_type_name=output_type_name,
                            reason="missing_structured_output_after_retry",
                            message=error_message,
                            candidate_id=builder_candidate_id,
                            extra={
                                "retry_events": retry_event_count,
                                "retry_duration_ms": retry_duration_ms,
                            },
                        )
                        raise SpecialistOutputError(
                            specialist_name=specialist_name,
                            output_type_name=output_type_name,
                            message=error_message
                        )

                except SpecialistOutputError:
                    # Re-raise our custom error
                    raise
                except Exception as e:
                    # Retry mechanism itself failed
                    logger.error("%s retry mechanism error: %s", specialist_name, e)
                    error_message = f"{specialist_name} retry failed with error: {str(e)}"
                    _record_builder_specialist_output_failure(
                        builder_workspace=builder_workspace,
                        specialist_name=specialist_name,
                        tool_name=tool_name,
                        output_type_name=output_type_name,
                        reason="structured_output_retry_failed",
                        message=error_message,
                        candidate_id=builder_candidate_id,
                        extra={"error_type": type(e).__name__},
                    )
                    raise SpecialistOutputError(
                        specialist_name=specialist_name,
                        output_type_name=output_type_name,
                        message=error_message,
                    )

    final_output = _canonicalize_structured_output_text(
        final_output,
        expected_output_type=expected_output_type,
    )
    if expected_output_type is not None and final_output:
        try:
            payload = json.loads(final_output)
        except Exception:
            payload = None
        if isinstance(payload, dict):
            canonical_payload = stage_extraction_payload(
                payload,
                workspace=builder_workspace,
                candidate_id=builder_candidate_id,
                evidence_records=live_evidence_records,
            )
            final_output = json.dumps(canonical_payload)

    phase_timings_ms["post_stream_output_ms"] = _elapsed_ms(post_stream_started_at)

    evidence_summary_started_at = time.monotonic()
    try:
        _emit_specialist_evidence_summary_or_raise(
            specialist_name=specialist_name,
            tool_name=tool_name,
            expected_output_type=expected_output_type,
            final_output=final_output,
            live_evidence_records=live_evidence_records,
        )
    except SpecialistOutputError:
        if builder_workspace.finalization is None:
            builder_workspace.record_validation_failure(
                errors=[
                    {
                        "message": (
                            f"{specialist_name} completed extraction output without the "
                            "required verified evidence records."
                        ),
                        "reason": "missing_evidence_records",
                    }
                ],
                candidate_ids=[builder_candidate_id],
            )
        raise
    phase_timings_ms["evidence_summary_ms"] = _elapsed_ms(
        evidence_summary_started_at
    )

    validator_dispatch_started_at = time.monotonic()
    try:
        final_output = await _dispatch_domain_envelope_validators_for_chat(
            final_output,
            expected_output_type=expected_output_type,
            specialist_name=specialist_name,
            tool_name=tool_name,
        )
    except SpecialistOutputError as exc:
        if builder_workspace.finalization is None:
            dispatch_errors = [
                {
                    **dict(error),
                    "specialist_name": specialist_name,
                    "tool_name": tool_name,
                }
                for error in getattr(exc, "details", [])
                if isinstance(error, Mapping)
            ]
            if not dispatch_errors:
                dispatch_errors = [
                    {
                        "message": str(exc),
                        "reason": "domain_validator_dispatch_failed",
                        "specialist_name": specialist_name,
                        "tool_name": tool_name,
                    }
                ]
            builder_workspace.record_validation_failure(
                errors=dispatch_errors,
                candidate_ids=[builder_candidate_id],
            )
        raise
    phase_timings_ms["domain_validator_dispatch_ms"] = _elapsed_ms(
        validator_dispatch_started_at
    )
    retained_evidence_records = extract_evidence_records_from_structured_result(
        final_output
    )

    internal_event_started_at = time.monotonic()
    if tool_name and _is_domain_envelope_output_json(
        final_output,
        expected_output_type=expected_output_type,
    ):
        payload = json.loads(final_output)
        builder_finalization = finalize_extraction_payload(
            payload,
            workspace=builder_workspace,
            candidate_id=builder_candidate_id,
            evidence_records=live_evidence_records,
        )
        final_output = json.dumps(builder_finalization.payload)
        add_specialist_event(
            build_internal_extraction_result_event(
                tool_name=tool_name,
                specialist_name=specialist_name,
                finalization=builder_finalization,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
    phase_timings_ms["internal_extraction_event_ms"] = _elapsed_ms(
        internal_event_started_at
    )

    supervisor_reduction_started_at = time.monotonic()
    final_output = _reduce_specialist_output_for_supervisor(
        final_output,
        expected_output_type=expected_output_type,
    )
    phase_timings_ms["supervisor_output_reduction_ms"] = _elapsed_ms(
        supervisor_reduction_started_at
    )

    total_duration_ms = _elapsed_ms(wall_started_at)
    tool_duration_total_ms = sum(
        duration
        for duration in (tc.duration_ms for tc in tool_calls)
        if duration is not None
    )
    tool_duration_known_count = sum(
        1 for tc in tool_calls if tc.duration_ms is not None
    )
    non_tool_stream_duration_ms = max(
        0,
        phase_timings_ms.get("stream_consume_ms", 0) - tool_duration_total_ms,
    )

    # Emit summary event
    add_specialist_event({
        "type": "SPECIALIST_SUMMARY",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": {
            "specialist": specialist_name,
            "toolCallCount": len(tool_calls),
            "totalDurationMs": total_duration_ms,
            "streamDurationMs": stream_duration_ms,
            "phaseTimingsMs": dict(phase_timings_ms),
            "toolDurationTotalMs": tool_duration_total_ms,
            "toolDurationKnownCount": tool_duration_known_count,
            "toolDurationUnknownCount": len(tool_calls) - tool_duration_known_count,
            "nonToolStreamDurationMs": non_tool_stream_duration_ms,
            "liveEvidenceRecordCount": len(live_evidence_records),
            "liveEvidenceRecords": list(live_evidence_records),
            "retainedEvidenceRecordCount": len(retained_evidence_records),
            "retainedEvidenceRecords": retained_evidence_records,
            "eventCount": total_event_count,
            "eventTypeCounts": dict(event_type_counts),
            "toolCalls": [
                {
                    "name": tc.tool_name,
                    "args": tc.tool_args,
                    "durationMs": tc.duration_ms,
                    "outputPreview": tc.output_preview,
                    "outputSummary": tc.output_summary,
                }
                for tc in tool_calls
            ]
        }
    })

    logger.info(
        "%s complete: %s tool calls, %sms total, output_length=%s",
        specialist_name,
        len(tool_calls),
        total_duration_ms,
        len(final_output),
    )

    # Inject batching nudge if threshold was hit (exactly at threshold, not after)
    if tool_name:
        nudge = _generate_batching_nudge(tool_name, consecutive_count)
        if nudge:
            logger.info(
                "TRIGGERED for %s after %s consecutive calls. Injecting reminder to supervisor about batching %s.",
                tool_name,
                consecutive_count,
                get_batching_config().get(tool_name, {}).get("entity", "items"),
            )
            final_output += nudge

    return final_output
