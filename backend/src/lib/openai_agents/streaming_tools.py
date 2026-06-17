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
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import lru_cache
from typing import List, Dict, Any, Mapping, Optional

from agents import (
    Agent,
    AgentOutputSchema,
    ModelSettings,
    Runner,
    RunConfig,
    ToolsToFinalOutputFunction,
    ToolsToFinalOutputResult,
)
from pydantic import ValidationError

from .audit_labels import build_specialist_internal_friendly_name
from .config import (
    get_batching_nudge_threshold,
    get_layer2_force_tool_finalization_enabled,
    get_max_turns,
    get_structured_finalization_hard_max_attempts,
    get_structured_finalization_max_attempts,
    get_structured_finalization_retry_max_turns,
    reasoning_summary_request_settings,
    resolve_model_provider,
)
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
from .extraction_manifest import (
    ExtractionManifestError,
    build_and_render_extraction_manifest,
    build_extraction_manifest_page,
)
from .tools.evidence_workspace import (
    reset_active_evidence_records,
    set_active_evidence_records,
)
from .extraction_builder_workspace import (
    ExtractionBuilderWorkspace,
    build_internal_extraction_result_event,
    get_active_extraction_builder_workspace,
    reset_active_extraction_builder_workspace,
    set_active_extraction_builder_workspace,
)
from .curation_context_registry import register_internal_extraction_event
from .extraction_trace_events import (
    get_current_extraction_trace_run,
    write_extraction_trace_event,
    write_stream_event,
)
from .resolver_call_ledger import (
    RESOLVER_TOOL_NAME,
    ResolverCallLedger,
    reset_active_resolver_call_ledger,
    set_active_resolver_call_ledger,
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
from src.lib.context import (
    get_current_session_id,
    get_current_trace_id,
    get_current_user_id,
)
from src.lib.curation_workspace.extraction_results import (
    InlineExtractionPersistenceResult,
    persist_inline_validated_extraction_result,
)
from src.schemas.curation_workspace import CurationExtractionSourceKind
from src.schemas.domain_validator import is_domain_validator_result_schema
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult

logger = logging.getLogger(__name__)

INTERNAL_EXTRACTION_RESULT_EVENT_TYPE = _INTERNAL_EXTRACTION_RESULT_EVENT_TYPE
_DOCUMENT_REQUIRED_TOOL_NAMES = set(DOCUMENT_REQUIRED_TOOL_NAMES)
_STRUCTURED_FINALIZATION_CHECK_PDF_EVIDENCE = "pdf_evidence"


@dataclass(frozen=True)
class SupervisorExtractionHandoff:
    """Structured extraction-result metadata from the latest specialist run."""

    tool_name: str
    specialist_name: str
    result_ref: str
    extraction_result_id: str
    result_status: str
    object_count: int
    domain_pack_id: Optional[str] = None
    adapter_key: Optional[str] = None
    agent_key: Optional[str] = None
    created_new: Optional[bool] = None


_LAST_SUPERVISOR_EXTRACTION_HANDOFF: ContextVar[
    SupervisorExtractionHandoff | None
] = ContextVar("last_supervisor_extraction_handoff", default=None)


def pop_last_supervisor_extraction_handoff() -> SupervisorExtractionHandoff | None:
    """Return and clear the latest structured extraction-result handoff."""

    handoff = _LAST_SUPERVISOR_EXTRACTION_HANDOFF.get()
    _LAST_SUPERVISOR_EXTRACTION_HANDOFF.set(None)
    return handoff


def _set_last_supervisor_extraction_handoff(
    handoff: SupervisorExtractionHandoff | None,
) -> None:
    _LAST_SUPERVISOR_EXTRACTION_HANDOFF.set(handoff)


def _build_supervisor_extraction_handoff(
    *,
    tool_name: str,
    specialist_name: str,
    payload: Mapping[str, Any],
    inline_persistence: InlineExtractionPersistenceResult,
    adapter_key: str | None,
    agent_key: str | None,
) -> SupervisorExtractionHandoff | None:
    try:
        page = build_extraction_manifest_page(
            payload,
            extraction_result_id=inline_persistence.extraction_result_id,
            result_ref=inline_persistence.result_ref,
            adapter_key=adapter_key,
            agent_key=agent_key,
            limit=1,
        )
    except ExtractionManifestError:
        return None
    return SupervisorExtractionHandoff(
        tool_name=tool_name,
        specialist_name=specialist_name,
        result_ref=inline_persistence.result_ref,
        extraction_result_id=inline_persistence.extraction_result_id,
        result_status=str(page.get("result_status") or "empty_extraction"),
        object_count=int(page.get("object_count") or 0),
        domain_pack_id=str(page.get("domain_pack_id") or "") or None,
        adapter_key=adapter_key,
        agent_key=agent_key,
        created_new=inline_persistence.created_new,
    )
_STRUCTURED_FINALIZATION_CHECK_LOOKUP_PROVENANCE = "lookup_provenance"
# Env-configurable (defaults unchanged); see config.py getters and .env.example:
#   STRUCTURED_FINALIZATION_MAX_ATTEMPTS, STRUCTURED_FINALIZATION_HARD_MAX_ATTEMPTS.
_STRUCTURED_FINALIZATION_DEFAULT_MAX_ATTEMPTS = get_structured_finalization_max_attempts()
_STRUCTURED_FINALIZATION_HARD_MAX_ATTEMPTS = get_structured_finalization_hard_max_attempts()
# Layer 2 (tool_choice=required + ToolsToFinalOutputFunction). Run-loop change;
# gated so it can be disabled instantly during the live 11-agent regression.
# This constant is a temporary validation gate, not a permanent fallback.
# When False, behavior is exactly Layer 1 (the prior structured-finalization loop).
# Remove the gate once regression-validated.
# Env-configurable via LAYER2_FORCE_TOOL_FINALIZATION_ENABLED (default True).
LAYER2_FORCE_TOOL_FINALIZATION_ENABLED = get_layer2_force_tool_finalization_enabled()
_GROQ_SCHEMA_CONSTRAINTS_ADAPTER_KEY = "groq_schema_constraints"


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


def _tool_output_payload_for_finalization(
    tool_name: str,
    output: Any,
) -> Optional[Dict[str, Any]]:
    """Return compact structured tool output used only by finalization checks."""

    lookup_config = _lookup_finalization_config_for_tool(tool_name)
    if lookup_config is None:
        return None

    payload = coerce_tool_event_dict(output)
    if payload is None:
        model_dump = getattr(output, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump(mode="json")
            except TypeError:
                dumped = model_dump()
            if isinstance(dumped, dict):
                payload = dumped
    if not isinstance(payload, dict):
        return None

    compact: Dict[str, Any] = {}
    for key in ("status", "status_code", "message"):
        if key in payload:
            value = payload.get(key)
            if isinstance(value, str) and len(value) > 1000:
                value = f"{value[:1000]}..."
            compact[key] = value

    if "data" in payload:
        data = payload.get("data")
    else:
        data = _lookup_tool_configured_result_payload(payload, config=lookup_config)
    if data is not None:
        compact["data"] = _compact_lookup_tool_data(data)
        compact["scalar_tokens"] = sorted(_lookup_scalar_tokens(data))

    return compact


def _lookup_tool_configured_result_payload(
    payload: Dict[str, Any],
    *,
    config: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Extract package-declared lookup facts from non-REST function-tool results."""

    paths = config.get("tool_output_paths")
    if not isinstance(paths, list):
        return dict(payload)
    selected: Dict[str, Any] = {}
    for path in paths:
        path_text = str(path or "").strip()
        if not path_text:
            continue
        values = _lookup_values_at_path(payload, path_text)
        if not values:
            continue
        selected[path_text] = values[0] if len(values) == 1 else values
    return selected or None


def _compact_lookup_tool_data(value: Any) -> Any:
    """Preserve enough REST payload shape for provenance checks."""

    if isinstance(value, list):
        return {
            "__items": [_compact_lookup_tool_data(item) for item in value[:50]],
            "__full_count": len(value),
            "__truncated": len(value) > 50,
        }
    if isinstance(value, dict):
        compact: Dict[str, Any] = {}
        preferred_keys = (
            "results",
            "associations",
            "numberOfHits",
            "total",
            "total_count",
            "count",
            "id",
            "name",
            "label",
            "symbol",
            "title",
            "short_citation",
        )
        for key in preferred_keys:
            if key in value:
                compact[key] = _compact_lookup_tool_data(value.get(key))
        if compact:
            return compact
        return {
            str(key): _compact_lookup_tool_data(child)
            for key, child in list(value.items())[:20]
        }
    if isinstance(value, str):
        return value[:1000] + "..." if len(value) > 1000 else value
    return value


def _lookup_scalar_tokens(value: Any, *, limit: int = 20000) -> set[str]:
    """Collect normalized scalar API payload values for provenance checks."""

    tokens: set[str] = set()

    def visit(node: Any) -> None:
        if len(tokens) >= limit:
            return
        if isinstance(node, dict):
            for child in node.values():
                visit(child)
            return
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if isinstance(node, (str, int, float)) and not isinstance(node, bool):
            text = str(node).strip().lower()
            if text:
                tokens.add(text)

    visit(value)
    return tokens


def _is_domain_envelope_extraction_output_type(output_type: Any) -> bool:
    """Return whether an output type uses the shared domain-envelope contract."""

    if output_type is None:
        return False
    try:
        return issubclass(output_type, DomainEnvelopeExtractionResult)
    except TypeError:
        return False


def _looks_like_domain_envelope_payload(payload: Any) -> bool:
    """Return whether a parsed payload has the shared domain-envelope shape."""

    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("curatable_objects"), list):
        return True
    return isinstance(payload.get("domain_pack_id"), str) and isinstance(
        payload.get("extracted_objects"),
        list,
    )


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


@dataclass
class _StructuredSpecialistFinalizationState:
    required: bool
    tool_name: str
    agent_name: str
    output_type_name: str
    config: Dict[str, Any] = field(default_factory=dict)
    max_attempts: int = _STRUCTURED_FINALIZATION_DEFAULT_MAX_ATTEMPTS
    attempt_limit_exceeded: bool = False
    accepted_payload: Optional[Dict[str, Any]] = None
    last_rejection: Optional[Dict[str, Any]] = None
    calls: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.accepted_payload is not None


@dataclass
class _StructuredSpecialistFinalizationFeedback:
    accepted_payload: Optional[Dict[str, Any]]
    message: str
    repair_instructions: List[str] = field(default_factory=list)
    field_errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


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


def _run_config_with_full_trace_payloads(run_config: Any) -> Any:
    """Return a run config that preserves tracing state but includes payload data."""
    effective_config = run_config or RunConfig(tracing_disabled=True)
    try:
        return replace(effective_config, trace_include_sensitive_data=True)
    except TypeError:
        setattr(effective_config, "trace_include_sensitive_data", True)
        if not hasattr(effective_config, "tracing_disabled"):
            setattr(effective_config, "tracing_disabled", True)
        return effective_config


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


# Tool-binding metadata flag (in each domain pack's bindings.yaml) that marks a
# tool as a builder-materializer finalize tool. The runtime derives the set of
# finalize-tool names from this flag instead of a hardcoded literal, so adding a
# new builder data type is a domain-pack edit, not a platform edit.
_BUILDER_FINALIZATION_METADATA_KEY = "builder_finalization"


@lru_cache(maxsize=1)
def builder_finalization_tool_names() -> frozenset[str]:
    """Return the registry-derived set of builder-materializer finalize-tool names.

    A tool is a builder finalize tool when its package tool-binding metadata
    declares ``builder_finalization: true``. This makes builder detection a
    domain-pack/registry concern (project-agnostic core) rather than a hardcoded
    per-type literal in the platform runtime.
    """
    return frozenset(
        tool_id
        for tool_id, metadata in _tool_metadata_by_name().items()
        if bool(metadata.get(_BUILDER_FINALIZATION_METADATA_KEY))
    )


def is_builder_materializer_agent(agent: Agent) -> bool:
    """Return whether an agent finalizes backend-materialized builder output."""
    finalization_tool_names = builder_finalization_tool_names()
    return any(
        _extract_tool_name(tool) in finalization_tool_names
        for tool in (getattr(agent, "tools", None) or [])
    )


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
        _emit_specialist_evidence_summary(
            tool_name=tool_name,
            evidence_records=evidence_records,
        )
        return

    if not requires_evidence:
        return

    if live_evidence_records and not missing_record_refs:
        _emit_specialist_evidence_summary(
            tool_name=tool_name,
            evidence_records=live_evidence_records,
        )
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


def _emit_specialist_evidence_summary(
    *,
    tool_name: Optional[str],
    evidence_records: List[Dict[str, Any]],
) -> None:
    if not evidence_records:
        return

    add_specialist_event({
        "type": "evidence_summary",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": tool_name,
        "evidence_records": evidence_records,
    })


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


def _output_type_name(output_type: Any) -> str:
    return str(getattr(output_type, "__name__", None) or "response")


def _normalize_structured_finalization_config(raw_config: Any) -> Dict[str, Any]:
    if not isinstance(raw_config, dict):
        return {}
    if raw_config.get("enabled") is False:
        return {}
    tool_name = str(raw_config.get("tool_name") or "").strip()
    if not tool_name:
        return {}
    return dict(raw_config)


def _agent_structured_finalization_config(
    agent: Agent,
    *,
    tool_name: Optional[str],
) -> Dict[str, Any]:
    direct_config = _normalize_structured_finalization_config(
        getattr(agent, "structured_finalization", None)
    )
    if direct_config:
        return direct_config

    if not tool_name:
        return {}

    try:
        from src.lib.config.agent_loader import get_agent_by_tool_name

        agent_definition = get_agent_by_tool_name(tool_name)
    except Exception:
        logger.debug(
            "Unable to resolve package agent definition for %s",
            tool_name,
            exc_info=True,
        )
        return {}

    if agent_definition is None:
        return {}
    return _normalize_structured_finalization_config(
        getattr(agent_definition, "structured_finalization", None)
    )


def _structured_finalization_has_check(
    config: Mapping[str, Any],
    check_name: str,
) -> bool:
    checks = config.get("checks")
    if isinstance(checks, list):
        return check_name in {str(check).strip() for check in checks}
    return False


def _structured_finalization_input_schema_name(
    config: Mapping[str, Any],
) -> Optional[str]:
    schema_name = str(config.get("input_schema") or "").strip()
    return schema_name or None


def _structured_finalization_input_type(
    config: Mapping[str, Any],
) -> Optional[Any]:
    schema_name = _structured_finalization_input_schema_name(config)
    if not schema_name:
        return None
    try:
        from src.lib.config import schema_discovery

        return schema_discovery.resolve_output_schema(schema_name)
    except Exception:
        logger.warning(
            "Unable to resolve structured finalization input schema %s",
            schema_name,
            exc_info=True,
        )
        return None


def _structured_specialist_finalization_tool_name(
    config: Mapping[str, Any],
) -> Optional[str]:
    tool_name = str(config.get("tool_name") or "").strip()
    return tool_name or None


def _structured_specialist_finalization_max_attempts(
    config: Mapping[str, Any],
) -> int:
    raw_value = config.get("max_attempts")
    if raw_value is None:
        return _STRUCTURED_FINALIZATION_DEFAULT_MAX_ATTEMPTS
    try:
        attempts = int(raw_value)
    except (TypeError, ValueError):
        return _STRUCTURED_FINALIZATION_DEFAULT_MAX_ATTEMPTS
    if attempts < 1:
        return _STRUCTURED_FINALIZATION_DEFAULT_MAX_ATTEMPTS
    return min(attempts, _STRUCTURED_FINALIZATION_HARD_MAX_ATTEMPTS)


def _structured_specialist_finalization_required(
    agent: Agent,
    *,
    expected_output_type: Any,
    builder_materializer_agent: bool,
    finalization_config: Mapping[str, Any],
) -> bool:
    if builder_materializer_agent or expected_output_type is None:
        return False
    if not callable(getattr(expected_output_type, "model_validate", None)):
        return False
    if _structured_specialist_finalization_tool_name(finalization_config) is None:
        return False
    existing_tool_names = _agent_tool_names(agent)
    if any(name in builder_finalization_tool_names() for name in existing_tool_names):
        return False
    return True


def _validation_error_summary(exc: ValidationError) -> str:
    errors: List[str] = []
    for error in exc.errors()[:5]:
        loc = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        errors.append(f"{loc}: {error.get('msg')}")
    if len(exc.errors()) > 5:
        errors.append(f"{len(exc.errors()) - 5} more error(s)")
    return "; ".join(errors)


def _max_turns_with_structured_specialist_finalization(max_turns: int) -> int:
    return max_turns + 2


def _document_tool_was_called(tool_calls: List["SpecialistToolCall"]) -> bool:
    return any(call.tool_name in _DOCUMENT_REQUIRED_TOOL_NAMES for call in tool_calls)


def _live_evidence_records_by_id(
    live_evidence_records: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    records: Dict[str, Dict[str, Any]] = {}
    for record in live_evidence_records:
        if not isinstance(record, dict):
            continue
        record_id = str(record.get("evidence_record_id") or "").strip()
        if record_id:
            records[record_id] = record
    return records


def _evidence_reference_ids_from_payload(value: Any) -> set[str]:
    ids: set[str] = set()

    def visit(node: Any, *, inside_evidence_registry: bool = False) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                child_inside_registry = inside_evidence_registry or key == "evidence_records"
                if key == "evidence_record_ids" and isinstance(child, list):
                    for item in child:
                        text = str(item or "").strip()
                        if text:
                            ids.add(text)
                    continue
                if key == "evidence_record_id" and not inside_evidence_registry:
                    text = str(child or "").strip()
                    if text:
                        ids.add(text)
                    continue
                visit(child, inside_evidence_registry=child_inside_registry)
            return
        if isinstance(node, list):
            for child in node:
                visit(child, inside_evidence_registry=inside_evidence_registry)

    visit(value)
    return ids


def _evidence_registry_records_from_payload(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    records = value.get("evidence_records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _pdf_evidence_registry_errors(
    payload: Dict[str, Any],
    *,
    live_evidence_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    live_by_id = _live_evidence_records_by_id(live_evidence_records)
    errors: List[Dict[str, Any]] = []
    referenced_ids = _evidence_reference_ids_from_payload(payload)
    payload_registry_records = _evidence_registry_records_from_payload(payload)

    if not live_by_id:
        if referenced_ids:
            errors.append({
                "field": "evidence_record_ids",
                "message": (
                    "Payload references evidence IDs, but no record_evidence "
                    "records were produced in this run."
                ),
                "ids": sorted(referenced_ids),
            })
        if payload_registry_records:
            errors.append({
                "field": "evidence_records",
                "message": (
                    "Payload includes evidence registry entries, but no "
                    "record_evidence records were produced in this run."
                ),
            })
        return errors

    unknown_refs = sorted(referenced_ids - set(live_by_id))
    if unknown_refs:
        errors.append({
            "field": "evidence_record_ids",
            "message": (
                "Payload references evidence IDs that were not produced by "
                "record_evidence in this run."
            ),
            "ids": unknown_refs,
        })

    for index, record in enumerate(payload_registry_records):
        record_id = str(record.get("evidence_record_id") or "").strip()
        if not record_id:
            errors.append({
                "field": f"evidence_records[{index}].evidence_record_id",
                "message": "Evidence registry entries must include live evidence_record_id values.",
            })
            continue
        live_record = live_by_id.get(record_id)
        if live_record is None:
            errors.append({
                "field": f"evidence_records[{index}].evidence_record_id",
                "message": "Evidence record was not created by record_evidence in this run.",
                "id": record_id,
            })
            continue
        mismatches = []
        for key in (
            "entity",
            "verified_quote",
            "page",
            "section",
            "subsection",
            "chunk_id",
            "document_id",
            "figure_reference",
        ):
            if key not in record or key not in live_record:
                continue
            if record.get(key) != live_record.get(key):
                mismatches.append(key)
        if mismatches:
            errors.append({
                "field": f"evidence_records[{index}]",
                "message": "Evidence record fields must match the live record_evidence output.",
                "id": record_id,
                "mismatched_fields": mismatches,
            })

    return errors


def _lookup_finalization_config(
    finalization_config: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    if not _structured_finalization_has_check(
        finalization_config,
        _STRUCTURED_FINALIZATION_CHECK_LOOKUP_PROVENANCE,
    ):
        return None
    lookup_config = finalization_config.get("lookup")
    return dict(lookup_config) if isinstance(lookup_config, dict) else None


def _lookup_finalization_config_for_tool(tool_name: str) -> Optional[Dict[str, Any]]:
    """Return package-declared lookup finalization config for a tool."""

    try:
        from src.lib.config.agent_loader import load_agent_definitions

        agent_definitions = load_agent_definitions()
    except Exception:
        logger.debug("Unable to load agent definitions for lookup finalization", exc_info=True)
        return None

    normalized_tool_name = str(tool_name or "").strip()
    if not normalized_tool_name:
        return None
    for agent_definition in agent_definitions.values():
        config = _normalize_structured_finalization_config(
            getattr(agent_definition, "structured_finalization", None)
        )
        lookup_config = _lookup_finalization_config(config)
        if lookup_config is None:
            continue
        configured_tool_name = str(lookup_config.get("tool_name") or "").strip()
        if configured_tool_name == normalized_tool_name:
            return lookup_config
    return None


def _lookup_tool_calls(
    tool_calls: List["SpecialistToolCall"],
    *,
    config: Dict[str, Any],
) -> List["SpecialistToolCall"]:
    tool_name = str(config.get("tool_name") or "")
    return [call for call in tool_calls if call.tool_name == tool_name]


def _lookup_tool_call_succeeded(call: "SpecialistToolCall") -> bool:
    payload = call.output_payload or {}
    status = str(payload.get("status") or "").strip().lower()
    status_code = payload.get("status_code")
    if status in {"ok", "success"}:
        return True
    if isinstance(status_code, int) and 200 <= status_code < 300:
        return True
    return False


def _lookup_tool_call_failed(call: "SpecialistToolCall") -> bool:
    payload = call.output_payload or {}
    status = str(payload.get("status") or "").strip().lower()
    status_code = payload.get("status_code")
    if status == "error":
        return True
    if isinstance(status_code, int) and status_code >= 400:
        return True
    return False


def _lookup_attempts_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    attempts = payload.get("lookup_attempts")
    if not isinstance(attempts, list):
        return []
    return [attempt for attempt in attempts if isinstance(attempt, dict)]


def _lookup_attempt_matches_provider(
    attempt: Dict[str, Any],
    *,
    config: Dict[str, Any],
) -> bool:
    provider = str(attempt.get("provider") or "").strip().lower()
    method = str(attempt.get("method") or "").strip().lower()
    haystack = f"{provider} {method}"
    return any(
        str(term).strip().lower() in haystack
        for term in config.get("provider_terms", ())
        if str(term).strip()
    )


def _scalar_strings(value: Any) -> List[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, list):
        strings: List[str] = []
        for item in value:
            strings.extend(_scalar_strings(item))
        return strings
    if isinstance(value, dict):
        strings: List[str] = []
        for item in value.values():
            strings.extend(_scalar_strings(item))
        return strings
    return []


def _lookup_attempt_matches_tool_call(
    attempt: Dict[str, Any],
    call: "SpecialistToolCall",
) -> bool:
    tool_args = call.tool_args or {}
    query = attempt.get("query")
    if not isinstance(query, dict) or not query:
        return False

    call_url = str(tool_args.get("url") or "").strip()
    query_url = str(query.get("url") or query.get("endpoint") or "").strip()

    if query_url and call_url and query_url == call_url:
        return True

    tool_text = json.dumps(tool_args, sort_keys=True, default=str).lower()
    for value in _scalar_strings(query):
        normalized = value.lower()
        if normalized and normalized in tool_text:
            return True
    return False


def _lookup_tool_data_result_count(payload: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    if not _lookup_tool_call_succeeded(
        SpecialistToolCall(tool_name="", output_payload=payload)
    ):
        return 0
    data = payload.get("data")
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return 1 if data else 0
    full_count = data.get("__full_count")
    if isinstance(full_count, int):
        return full_count
    for key in ("results", "associations", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            full_count = value.get("__full_count")
            if isinstance(full_count, int):
                return full_count
    for key in ("numberOfHits", "total", "total_count"):
        value = data.get(key)
        if isinstance(value, int):
            return value
    return 1 if data else 0


def _lookup_requested_values(
    payload: Dict[str, Any],
    *,
    config: Dict[str, Any],
) -> List[str]:
    request_keys = {str(key).lower() for key in config.get("request_keys", ())}
    values: List[str] = []

    def collect_from_mapping(mapping: Any) -> None:
        if not isinstance(mapping, dict):
            return
        for key, value in mapping.items():
            normalized_key = str(key).lower()
            if normalized_key not in request_keys:
                continue
            if isinstance(value, list):
                values.extend(_scalar_strings(value))
            elif isinstance(value, dict):
                values.extend(_scalar_strings(value))
            elif _looks_like_lookup_identifier(value):
                values.extend(_scalar_strings(value))

    target = payload.get("target")
    if isinstance(target, dict):
        collect_from_mapping(target.get("input_values"))
    collect_from_mapping(payload.get("selected_inputs"))
    collect_from_mapping(payload)

    normalized: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if text and key not in seen:
            normalized.append(text)
            seen.add(key)
    return normalized


def _looks_like_lookup_identifier(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    if re.search(r"\b[A-Z][A-Z0-9_]*:[A-Za-z0-9_.:-]+\b", text):
        return True
    return " " not in text and len(text) <= 80


def _lookup_result_coverage_text(
    payload: Dict[str, Any],
    *,
    config: Dict[str, Any],
) -> str:
    selected: Dict[str, Any] = {}
    for key in config.get("result_collections", ()):
        if key in payload:
            selected[str(key)] = payload.get(key)
    selected["missing_expected_fields"] = payload.get("missing_expected_fields")
    selected["candidates"] = payload.get("candidates")
    return json.dumps(selected, sort_keys=True, default=str).lower()


def _lookup_has_resolved_facts(
    payload: Dict[str, Any],
    *,
    config: Dict[str, Any],
) -> bool:
    resolved_fact_paths = config.get("resolved_fact_paths")
    if isinstance(resolved_fact_paths, list):
        for path in resolved_fact_paths:
            if _lookup_path_has_value(payload, str(path)):
                return True
        return False
    return bool(payload.get("resolved_values") or payload.get("resolved_objects"))


def _lookup_fact_identity_values(
    payload: Dict[str, Any],
    *,
    config: Dict[str, Any],
) -> List[tuple[str, str]]:
    values: List[tuple[str, str]] = []
    identity_paths = config.get("fact_identity_paths")
    if not isinstance(identity_paths, list):
        return values

    seen: set[tuple[str, str]] = set()
    for path in identity_paths:
        path_text = str(path or "").strip()
        if not path_text:
            continue
        for value in _lookup_values_at_path(payload, path_text):
            for scalar in _scalar_strings(value):
                text = str(scalar).strip()
                if not text:
                    continue
                key = (path_text, text.lower())
                if key in seen:
                    continue
                values.append((path_text, text))
                seen.add(key)

    return values


def _lookup_configured_grounded_fact_values(
    payload: Dict[str, Any],
    *,
    config: Dict[str, Any],
) -> List[tuple[str, str]]:
    values: List[tuple[str, str]] = []
    grounded_paths = config.get("grounded_fact_paths")
    if not isinstance(grounded_paths, list):
        return values

    seen: set[tuple[str, str]] = set()
    for path in grounded_paths:
        path_text = str(path or "").strip()
        if not path_text:
            continue
        for value in _lookup_values_at_path(payload, path_text):
            for scalar in _scalar_strings(value):
                text = str(scalar).strip()
                if not text:
                    continue
                key = (path_text, text.lower())
                if key in seen:
                    continue
                values.append((path_text, text))
                seen.add(key)
    return values


def _lookup_path_has_value(payload: Dict[str, Any], path: str) -> bool:
    for value in _lookup_values_at_path(payload, path):
        if isinstance(value, (list, dict)) and value:
            return True
        if _scalar_strings(value):
            return True
    return False


def _lookup_values_at_path(payload: Dict[str, Any], path: str) -> List[Any]:
    parts = [part for part in str(path or "").split(".") if part]
    current: List[Any] = [payload]
    for part in parts:
        next_values: List[Any] = []
        is_list_part = part.endswith("[]")
        key = part[:-2] if is_list_part else part
        for item in current:
            if not isinstance(item, dict) or key not in item:
                continue
            value = item.get(key)
            if is_list_part:
                if isinstance(value, list):
                    next_values.extend(value)
            else:
                next_values.append(value)
        current = next_values
        if not current:
            break
    return current


def _lookup_value_supported_by_tool_calls(
    value: str,
    *,
    tool_calls: List["SpecialistToolCall"],
) -> bool:
    needle = str(value or "").strip().lower()
    if not needle:
        return True
    for call in tool_calls:
        payload = call.output_payload or {}
        scalar_tokens = payload.get("scalar_tokens")
        if isinstance(scalar_tokens, list):
            tokens = {
                str(token).strip().lower()
                for token in scalar_tokens
                if str(token).strip()
            }
        else:
            data = payload.get("data") if isinstance(payload, dict) else None
            tokens = _lookup_scalar_tokens(data) if data is not None else set()
        if needle in tokens:
            return True
    return False


def _lookup_fact_identity_errors(
    payload: Dict[str, Any],
    *,
    config: Dict[str, Any],
    successful_calls: List["SpecialistToolCall"],
) -> List[Dict[str, Any]]:
    if not successful_calls:
        return []
    errors: List[Dict[str, Any]] = []
    for field_path, value in _lookup_fact_identity_values(
        payload,
        config=config,
    ):
        if _lookup_value_supported_by_tool_calls(value, tool_calls=successful_calls):
            continue
        errors.append({
            "field": field_path,
            "message": "Resolved lookup fact identity does not appear in the API tool output.",
            "value": value,
        })
    return errors


def _lookup_grounded_fact_errors(
    payload: Dict[str, Any],
    *,
    config: Dict[str, Any],
    successful_calls: List["SpecialistToolCall"],
) -> List[Dict[str, Any]]:
    if not successful_calls:
        return []
    errors: List[Dict[str, Any]] = []
    for field_path, value in _lookup_configured_grounded_fact_values(
        payload,
        config=config,
    ):
        if _lookup_value_supported_by_tool_calls(value, tool_calls=successful_calls):
            continue
        errors.append({
            "field": field_path,
            "message": "Lookup fact does not appear in the API tool output.",
            "value": value,
        })
    return errors


def _lookup_provenance_finalization_errors(
    payload: Dict[str, Any],
    *,
    output_type_name: str,
    finalization_config: Mapping[str, Any],
    tool_calls: List["SpecialistToolCall"],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    config = _lookup_finalization_config(finalization_config)
    if config is None:
        return [], {}

    expected_tool = str(config.get("tool_name") or "")
    concrete_calls = _lookup_tool_calls(tool_calls, config=config)
    successful_calls = [call for call in concrete_calls if _lookup_tool_call_succeeded(call)]
    failed_calls = [call for call in concrete_calls if _lookup_tool_call_failed(call)]
    attempts = _lookup_attempts_from_payload(payload)
    matching_attempts = [
        attempt
        for attempt in attempts
        if _lookup_attempt_matches_provider(attempt, config=config)
    ]
    status = str(payload.get("status") or "").strip().lower()
    resolved_facts = _lookup_has_resolved_facts(
        payload,
        config=config,
    )
    grounded_fact_values = _lookup_configured_grounded_fact_values(
        payload,
        config=config,
    )
    grounded_facts = bool(grounded_fact_values)
    errors: List[Dict[str, Any]] = []

    if not concrete_calls and (status == "resolved" or resolved_facts or grounded_facts):
        errors.append({
            "field": "tool_calls",
            "message": (
                f"{expected_tool} must complete before returning resolved "
                f"{output_type_name} facts."
            ),
        })

    if status == "resolved" and not successful_calls:
        errors.append({
            "field": "tool_calls",
            "message": (
                f"Resolved {output_type_name} output requires a successful "
                f"{expected_tool} call in this run."
            ),
        })

    if status == "resolved" and not resolved_facts:
        errors.append({
            "field": "status",
            "message": "Resolved lookup output must include resolved API-grounded facts.",
        })

    if grounded_facts and not successful_calls:
        errors.append({
            "field": "tool_calls",
            "message": (
                f"{output_type_name} lookup facts require a successful "
                f"{expected_tool} call in this run."
            ),
        })

    missing_expected_fields = payload.get("missing_expected_fields")
    if status == "resolved" and missing_expected_fields:
        errors.append({
            "field": "missing_expected_fields",
            "message": "Resolved lookup output cannot carry missing expected fields.",
        })

    target = payload.get("target")
    expected_fields = []
    if isinstance(target, dict) and isinstance(target.get("expected_fields"), list):
        expected_fields = target.get("expected_fields") or []
    if (
        status == "unresolved"
        and expected_fields
        and not missing_expected_fields
        and not failed_calls
    ):
        errors.append({
            "field": "missing_expected_fields",
            "message": (
                "Unresolved lookup output with expected fields must list the "
                "fields that could not be filled."
            ),
        })

    if not matching_attempts and (concrete_calls or status == "resolved" or grounded_facts):
        errors.append({
            "field": "lookup_attempts",
            "message": (
                f"lookup_attempts must include at least one {expected_tool} "
                "attempt; finalization calls do not count as lookup provenance."
            ),
        })

    if matching_attempts and len(matching_attempts) < len(concrete_calls):
        errors.append({
            "field": "lookup_attempts",
            "message": "Record one lookup_attempt for every concrete API tool call.",
        })

    success_attempts = [
        attempt for attempt in matching_attempts if attempt.get("outcome") == "success"
    ]
    error_attempts = [
        attempt for attempt in matching_attempts if attempt.get("outcome") == "error"
    ]
    if status == "resolved" and not success_attempts:
        errors.append({
            "field": "lookup_attempts[].outcome",
            "message": "Resolved lookup output requires a successful lookup_attempt.",
        })
    if failed_calls and not successful_calls and status == "resolved":
        errors.append({
            "field": "status",
            "message": (
                "A failed API lookup cannot produce a resolved result unless a "
                "later successful API call supersedes it."
            ),
        })
    if failed_calls and not successful_calls and status == "unresolved" and not error_attempts:
        errors.append({
            "field": "lookup_attempts[].outcome",
            "message": "Unresolved API failures must be recorded with outcome 'error'.",
        })

    if status == "resolved" or resolved_facts:
        errors.extend(_lookup_fact_identity_errors(
            payload,
            config=config,
            successful_calls=successful_calls,
        ))
    if grounded_facts:
        errors.extend(_lookup_grounded_fact_errors(
            payload,
            config=config,
            successful_calls=successful_calls,
        ))

    for index, attempt in enumerate(matching_attempts):
        if not isinstance(attempt.get("query"), dict) or not attempt.get("query"):
            errors.append({
                "field": f"lookup_attempts[{index}].query",
                "message": "lookup_attempts[].query must preserve the API query payload.",
            })
            continue
        if concrete_calls and not any(
            _lookup_attempt_matches_tool_call(attempt, call)
            for call in concrete_calls
        ):
            errors.append({
                "field": f"lookup_attempts[{index}].query",
                "message": "lookup_attempts[].query must correspond to an API tool call made in this run.",
            })
        result_count = attempt.get("result_count")
        if isinstance(result_count, int) and result_count > 0:
            matching_counts = [
                _lookup_tool_data_result_count(call.output_payload)
                for call in concrete_calls
                if _lookup_attempt_matches_tool_call(attempt, call)
            ]
            known_counts = [count for count in matching_counts if count is not None]
            if known_counts and result_count > max(known_counts):
                errors.append({
                    "field": f"lookup_attempts[{index}].result_count",
                    "message": "lookup_attempts[].result_count exceeds the API tool output count.",
                })

    requested_values = _lookup_requested_values(
        payload,
        config=config,
    )
    if len(requested_values) > 1:
        coverage_text = _lookup_result_coverage_text(payload, config=config)
        missing_values = [
            value for value in requested_values if value.lower() not in coverage_text
        ]
        if missing_values:
            errors.append({
                "field": "result_coverage",
                "message": "Every requested lookup input must be resolved or explicitly reported as not found.",
                "missing_inputs": missing_values,
            })

    summary = {
        "lookup_tool": expected_tool,
        "lookup_tool_call_count": len(concrete_calls),
        "successful_lookup_tool_call_count": len(successful_calls),
        "failed_lookup_tool_call_count": len(failed_calls),
        "lookup_attempt_count": len(matching_attempts),
        "requested_input_count": len(requested_values),
    }
    return errors, summary


def _structured_specialist_finalization_feedback(
    raw_result: Any,
    *,
    expected_output_type: Any,
    finalization_config: Mapping[str, Any] | None = None,
    tool_calls: List["SpecialistToolCall"],
    live_evidence_records: List[Dict[str, Any]],
) -> _StructuredSpecialistFinalizationFeedback:
    output_type_name = _output_type_name(expected_output_type)
    finalization_config = finalization_config or {}
    input_schema_name = _structured_finalization_input_schema_name(finalization_config)
    input_output_type = _structured_finalization_input_type(finalization_config)
    if input_schema_name and input_output_type is None:
        return _StructuredSpecialistFinalizationFeedback(
            accepted_payload=None,
            message=(
                f"{output_type_name} rejected: structured finalization input "
                f"schema {input_schema_name!r} could not be resolved."
            ),
            repair_instructions=[
                "Report this agent configuration error; the configured finalization input schema is unavailable."
            ],
        )
    input_output_type = input_output_type or expected_output_type
    input_type_name = _output_type_name(input_output_type)
    try:
        payload = raw_result
        if hasattr(raw_result, "model_dump"):
            payload = raw_result.model_dump()
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise TypeError("finalization payload must be a JSON object")
        validated = input_output_type.model_validate(payload)
    except ValidationError as exc:
        message = (
            f"{input_type_name} rejected: incompatible schema "
            f"({_validation_error_summary(exc)})."
        )
        return _StructuredSpecialistFinalizationFeedback(
            accepted_payload=None,
            message=message,
            repair_instructions=[
                "Repair the reported schema field(s) and call the finalization tool again."
            ],
        )
    except Exception as exc:
        message = f"{input_type_name} rejected: {exc}."
        return _StructuredSpecialistFinalizationFeedback(
            accepted_payload=None,
            message=message,
            repair_instructions=[
                "Submit one JSON object that matches the required structured output schema."
            ],
        )

    raw_payload = validated.model_dump()
    checks_pdf_evidence = _structured_finalization_has_check(
        finalization_config,
        _STRUCTURED_FINALIZATION_CHECK_PDF_EVIDENCE,
    )
    raw_registry_errors: List[Dict[str, Any]] = []
    if checks_pdf_evidence:
        raw_registry_errors = _pdf_evidence_registry_errors(
            raw_payload,
            live_evidence_records=live_evidence_records,
        )

    canonical_payload: Any = raw_payload
    if checks_pdf_evidence:
        canonical_payload = canonicalize_structured_result_payload(
            raw_payload,
            preferred_evidence_records=live_evidence_records,
        )
        if not isinstance(canonical_payload, dict):
            canonical_payload = validated.model_dump()

    try:
        canonical_model = expected_output_type.model_validate(canonical_payload)
        canonical_payload = canonical_model.model_dump()
    except ValidationError as exc:
        message = (
            f"{output_type_name} rejected after evidence canonicalization: "
            f"{_validation_error_summary(exc)}."
        )
        return _StructuredSpecialistFinalizationFeedback(
            accepted_payload=None,
            message=message,
            repair_instructions=[
                "Repair the structured fields while preserving live evidence_record_ids."
            ],
        )

    field_errors: List[Dict[str, Any]] = []
    repair_instructions: List[str] = []

    if raw_registry_errors:
        field_errors.extend(raw_registry_errors)
        repair_instructions.append(
            "Use only evidence_record_id values returned by record_evidence in this run."
        )

    evidence_report: Dict[str, Any] = {}
    lookup_report: Dict[str, Any] = {}

    if checks_pdf_evidence and not _document_tool_was_called(tool_calls):
        field_errors.append({
            "field": "tool_calls",
            "message": "At least one document retrieval tool must be called before finalization.",
        })
        repair_instructions.append(
            "Call search_document, read_section, read_subsection, or read_chunk before finalizing."
        )

    requires_evidence = False
    missing_record_refs = False
    if checks_pdf_evidence:
        requires_evidence = structured_result_requires_evidence(
            canonical_payload,
            expected_output_type=expected_output_type,
        )
        missing_record_refs = (
            structured_result_missing_evidence_record_refs(
                canonical_payload,
                expected_output_type=expected_output_type,
            )
            if requires_evidence
            else False
        )
        evidence_report = structured_result_evidence_reference_report(
            canonical_payload,
            expected_output_type=expected_output_type,
        )

    if requires_evidence and not live_evidence_records:
        field_errors.append({
            "field": "evidence_records",
            "message": "Retained PDF items require live record_evidence output.",
        })
        repair_instructions.append(
            "Use read_chunk evidence_spans and record_evidence, then reference the returned evidence_record_id."
        )
    if missing_record_refs:
        field_errors.append({
            "field": "items[].evidence_record_ids",
            "message": "Each retained item must reference a live evidence_record_id.",
            "evidence_reference_report": evidence_report,
        })
        repair_instructions.append(
            "Add live evidence_record_ids to every retained item, or set kept_count to 0 if no retained item is supported."
        )

    registry_errors = (
        _pdf_evidence_registry_errors(
            canonical_payload,
            live_evidence_records=live_evidence_records,
        )
        if checks_pdf_evidence
        else []
    )
    if registry_errors and not raw_registry_errors:
        field_errors.extend(registry_errors)
        repair_instructions.append(
            "Use only evidence_record_id values returned by record_evidence in this run, without editing their verified quote metadata."
        )

    lookup_errors, lookup_report = _lookup_provenance_finalization_errors(
        canonical_payload,
        output_type_name=output_type_name,
        finalization_config=finalization_config,
        tool_calls=tool_calls,
    )
    if lookup_errors:
        field_errors.extend(lookup_errors)
        repair_instructions.append(
            "Use the required lookup API tool first, record each concrete API call in lookup_attempts, and make the final status match the API evidence."
        )

    if field_errors:
        return _StructuredSpecialistFinalizationFeedback(
            accepted_payload=None,
            message=f"{output_type_name} rejected by finalization checks.",
            field_errors=field_errors,
            repair_instructions=repair_instructions,
            summary={**evidence_report, **lookup_report},
        )

    return _StructuredSpecialistFinalizationFeedback(
        accepted_payload=canonical_payload,
        message=f"{output_type_name} accepted.",
        summary={
            **evidence_report,
            **lookup_report,
            "live_evidence_record_count": len(live_evidence_records),
        },
    )


def _structured_specialist_finalization_tool_payload(
    feedback: _StructuredSpecialistFinalizationFeedback,
) -> Dict[str, Any]:
    if feedback.accepted_payload is not None:
        return {
            "status": "accepted",
            "message": feedback.message,
            "summary": feedback.summary,
            "warnings": feedback.warnings,
        }
    return {
        "status": "rejected",
        "message": feedback.message,
        "repair_instructions": feedback.repair_instructions,
        "field_errors": feedback.field_errors,
        "warnings": feedback.warnings,
    }


def _structured_finalization_rejected_attempt_count(
    state: _StructuredSpecialistFinalizationState,
) -> int:
    return sum(
        1
        for call in state.calls
        if call.get("details", {}).get("status") == "rejected"
    )


def _structured_finalization_attempt_limit_feedback(
    state: _StructuredSpecialistFinalizationState,
) -> _StructuredSpecialistFinalizationFeedback:
    state.attempt_limit_exceeded = True
    return _StructuredSpecialistFinalizationFeedback(
        accepted_payload=None,
        message=(
            f"{state.output_type_name} rejected: finalization attempt limit "
            f"exceeded after {state.max_attempts} rejected attempt(s)."
        ),
        repair_instructions=[
            "Stop calling the finalization tool for this run; the specialist output failed finalization."
        ],
        field_errors=[
            {
                "field": "finalization_attempts",
                "message": (
                    "The structured finalization tool was rejected too many times "
                    "in this run."
                ),
                "max_attempts": state.max_attempts,
            }
        ],
        warnings=["structured_finalization_attempt_limit_exceeded"],
        summary={
            "attempt_limit_exceeded": True,
            "max_attempts": state.max_attempts,
        },
    )


def _record_structured_specialist_finalization_call(
    *,
    state: _StructuredSpecialistFinalizationState,
    feedback: _StructuredSpecialistFinalizationFeedback,
) -> None:
    accepted = feedback.accepted_payload is not None
    event = {
        "type": "STRUCTURED_FINALIZATION",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": {
            "agent": state.agent_name,
            "toolName": state.tool_name,
            "outputType": state.output_type_name,
            "status": "accepted" if accepted else "rejected",
            "message": feedback.message,
            "summary": feedback.summary,
            "fieldErrors": feedback.field_errors,
        },
    }
    state.calls.append(event)
    add_specialist_event(event)


def _build_structured_specialist_finalization_tool(
    *,
    expected_output_type: Any,
    finalization_state: _StructuredSpecialistFinalizationState,
    tool_calls: List["SpecialistToolCall"],
    live_evidence_records: List[Dict[str, Any]],
    function_tool_factory: Any,
) -> Any:
    @function_tool_factory(
        name_override=finalization_state.tool_name,
        strict_mode=False,
    )
    def finalize_structured_specialist_result(result: dict[str, Any]) -> dict[str, Any]:
        """Validate the final structured specialist result before answering."""

        if (
            finalization_state.attempt_limit_exceeded
            or _structured_finalization_rejected_attempt_count(finalization_state)
            >= finalization_state.max_attempts
        ):
            feedback = _structured_finalization_attempt_limit_feedback(
                finalization_state
            )
        else:
            feedback = _structured_specialist_finalization_feedback(
                result,
                expected_output_type=expected_output_type,
                finalization_config=finalization_state.config,
                tool_calls=tool_calls,
                live_evidence_records=live_evidence_records,
            )
        if feedback.accepted_payload is not None:
            finalization_state.accepted_payload = feedback.accepted_payload
            finalization_state.last_rejection = None
        else:
            finalization_state.accepted_payload = None
            if (
                _structured_finalization_rejected_attempt_count(finalization_state) + 1
                >= finalization_state.max_attempts
            ):
                finalization_state.attempt_limit_exceeded = True
                feedback.summary.setdefault("attempt_limit_exceeded", True)
                feedback.summary.setdefault(
                    "max_attempts",
                    finalization_state.max_attempts,
                )
                if "structured_finalization_attempt_limit_exceeded" not in feedback.warnings:
                    feedback.warnings.append(
                        "structured_finalization_attempt_limit_exceeded"
                    )
            finalization_state.last_rejection = _structured_specialist_finalization_tool_payload(
                feedback
            )
        _record_structured_specialist_finalization_call(
            state=finalization_state,
            feedback=feedback,
        )
        return _structured_specialist_finalization_tool_payload(feedback)

    return finalize_structured_specialist_result


def _append_structured_specialist_finalization_instruction(
    runtime_agent: Agent,
    source_agent: Agent,
    *,
    finalization_state: _StructuredSpecialistFinalizationState,
) -> Agent:
    instruction = (
        "Structured result finalization is mandatory. Before your final answer, "
        f"call `{finalization_state.tool_name}` with the complete "
        f"{finalization_state.output_type_name} payload you intend to return. "
        "If the tool returns `status: rejected`, repair only the reported issue(s), "
        "call the finalization tool again, and do not send the final answer until "
        "the tool returns `status: accepted`. After acceptance, your final answer "
        "may be a short acknowledgment; the backend will use the accepted tool "
        "payload as the canonical structured result. You may make at most "
        f"{finalization_state.max_attempts} rejected finalization attempt(s) "
        "in this run; after that the specialist output fails finalization."
    )
    return _append_agent_runtime_instruction(
        runtime_agent,
        source_agent,
        instruction=instruction,
        layer_id_suffix="structured_specialist_finalization",
        title="Structured specialist finalization runtime instruction",
        source_ref="src.lib.openai_agents.streaming_tools:structured_specialist_finalization",
    )


def _configure_structured_specialist_finalization(
    runtime_agent: Agent,
    source_agent: Agent,
    *,
    expected_output_type: Any,
    finalization_state: _StructuredSpecialistFinalizationState,
    tool_calls: List["SpecialistToolCall"],
    live_evidence_records: List[Dict[str, Any]],
) -> Agent:
    from agents import function_tool

    if runtime_agent is source_agent:
        runtime_agent = copy.copy(source_agent)
    runtime_agent.tools = [
        *list(getattr(runtime_agent, "tools", []) or []),
        _build_structured_specialist_finalization_tool(
            expected_output_type=expected_output_type,
            finalization_state=finalization_state,
            tool_calls=tool_calls,
            live_evidence_records=live_evidence_records,
            function_tool_factory=function_tool,
        ),
    ]
    if getattr(runtime_agent, "output_type", None) is expected_output_type:
        runtime_agent.output_type = AgentOutputSchema(
            expected_output_type,
            strict_json_schema=False,
        )
    return _append_structured_specialist_finalization_instruction(
        runtime_agent,
        source_agent,
        finalization_state=finalization_state,
    )


def _build_structured_finalization_tool_use_behavior(
    finalization_state: _StructuredSpecialistFinalizationState,
) -> ToolsToFinalOutputFunction:
    """Build the SDK tool_use_behavior callback for Layer 2 forced finalization.

    The callback closes over the finalization state and ends the run the instant
    the mandatory finalize tool is accepted (its wrapper sets accepted_payload).
    A rejected finalize does NOT set accepted_payload, so the run continues and
    the model is allowed to repair and retry; reject/repair is therefore
    preserved. This is why a plain StopAtTools is wrong: it would stop on a
    rejected finalize too.
    """

    def _structured_finalization_tool_use_behavior(
        run_context: Any,
        tool_results: List[Any],
    ) -> ToolsToFinalOutputResult:
        if finalization_state.accepted:
            return ToolsToFinalOutputResult(
                is_final_output=True,
                final_output=json.dumps(finalization_state.accepted_payload),
            )
        return ToolsToFinalOutputResult(is_final_output=False, final_output=None)

    return _structured_finalization_tool_use_behavior


def _apply_layer2_forced_tool_finalization(
    runtime_agent: Agent,
    finalization_state: _StructuredSpecialistFinalizationState,
) -> Agent:
    """Apply Layer 2 forced-tool finalization to an already Layer-1 runtime agent.

    Layer 2 makes the model physically unable to deliver output as a bare final
    message and ends the run the instant finalize is accepted:
      - tool_use_behavior = a conditional ToolsToFinalOutputFunction that ends the
        run on accepted finalize (and continues otherwise, preserving repair).
      - model_settings.tool_choice = "required" so the model must call a tool every
        turn (no bare-text answers). The source model_settings is cloned, never
        mutated, and all other fields (reasoning, temperature, etc.) are preserved.
      - reset_tool_choice = False so tool_choice stays "required" across turns; the
        SDK otherwise resets it to auto after the first tool call.

    When the kill-switch LAYER2_FORCE_TOOL_FINALIZATION_ENABLED is False, the agent
    is returned unchanged (Layer 1 behavior).
    """

    if not LAYER2_FORCE_TOOL_FINALIZATION_ENABLED:
        return runtime_agent

    source_model_settings = getattr(runtime_agent, "model_settings", None)
    if source_model_settings is None:
        layer2_model_settings = ModelSettings(tool_choice="required")
    else:
        layer2_model_settings = replace(
            source_model_settings,
            tool_choice="required",
        )
    runtime_agent.model_settings = layer2_model_settings
    runtime_agent.tool_use_behavior = (
        _build_structured_finalization_tool_use_behavior(finalization_state)
    )
    runtime_agent.reset_tool_choice = False
    return runtime_agent


def _raise_missing_structured_specialist_finalization(
    *,
    state: _StructuredSpecialistFinalizationState,
    specialist_name: str,
    builder_workspace: ExtractionBuilderWorkspace,
    tool_name: Optional[str],
    candidate_id: str,
) -> None:
    if state.attempt_limit_exceeded:
        error_message = (
            f"{specialist_name} exceeded the {state.max_attempts}-attempt limit "
            f"for mandatory {state.tool_name} without status accepted."
        )
        reason = "structured_finalization_attempt_limit_exceeded"
    elif state.last_rejection is not None:
        error_message = (
            f"{specialist_name} did not complete mandatory {state.tool_name} "
            f"with status accepted. Last rejection: {state.last_rejection.get('message')}"
        )
        reason = "structured_finalization_rejected"
    else:
        error_message = (
            f"{specialist_name} did not call mandatory {state.tool_name} "
            "with status accepted."
        )
        reason = "structured_finalization_missing"

    add_specialist_event({
        "type": "SPECIALIST_ERROR",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": {
            "specialist": specialist_name,
            "output_type": state.output_type_name,
            "error": error_message,
            "reason": reason,
            "severity": "error",
        },
    })
    _record_builder_specialist_output_failure(
        builder_workspace=builder_workspace,
        specialist_name=specialist_name,
        tool_name=tool_name,
        output_type_name=state.output_type_name,
        reason=reason,
        message=error_message,
        candidate_id=candidate_id,
        extra={"finalization_tool": state.tool_name},
    )
    raise SpecialistOutputError(
        specialist_name=specialist_name,
        output_type_name=state.output_type_name,
        message=error_message,
        details=[{
            "reason": reason,
            "finalization_tool": state.tool_name,
            "last_rejection": state.last_rejection,
        }],
    )


def _reduce_specialist_output_for_supervisor(
    final_output: str,
    *,
    expected_output_type: Any,
    finalized_domain_envelope: bool = False,
    extraction_result_id: str | None = None,
    result_ref: str | None = None,
) -> str:
    """Return a supervisor-safe handoff without replaying raw structured JSON."""

    try:
        payload = json.loads(final_output)
    except Exception:
        return final_output

    if not isinstance(payload, dict):
        return final_output

    answer_text = str(payload.get("answer") or "").strip()
    if answer_text:
        return answer_text

    if _is_domain_envelope_extraction_output_type(expected_output_type) or finalized_domain_envelope:
        summary_text = _domain_envelope_supervisor_summary(
            payload,
            extraction_result_id=extraction_result_id,
            result_ref=result_ref,
        )
        if summary_text:
            return summary_text
        return _domain_envelope_supervisor_minimal_summary(payload)

    if _looks_like_domain_envelope_payload(payload):
        return (
            "A domain-envelope-shaped JSON payload was returned, but it was not "
            "accepted through a declared or finalized curation contract. The raw "
            "payload was not passed to the supervisor."
        )

    if expected_output_type is not None:
        validator_summary = _domain_validator_supervisor_summary(
            payload,
            expected_output_type=expected_output_type,
        )
        if validator_summary:
            return validator_summary
        return _structured_specialist_supervisor_summary(
            payload,
            expected_output_type=expected_output_type,
        )

    return final_output


def _domain_envelope_supervisor_summary(
    payload: Dict[str, Any],
    *,
    extraction_result_id: str | None = None,
    result_ref: str | None = None,
) -> str:
    """Build a supervisor-facing manifest from a canonical domain envelope."""

    try:
        return build_and_render_extraction_manifest(
            payload,
            extraction_result_id=extraction_result_id,
            result_ref=result_ref,
        )
    except ExtractionManifestError as exc:
        domain_pack_id = str(payload.get("domain_pack_id") or "domain envelope")
        return (
            f"Validated domain envelope result for {domain_pack_id}, but no safe "
            f"supervisor manifest could be rendered: {exc}. The raw canonical "
            "envelope was not passed to the supervisor."
        )


def _domain_envelope_supervisor_minimal_summary(payload: Dict[str, Any]) -> str:
    """Return a compact non-JSON summary for unusual domain-envelope payloads."""

    domain_pack_id = str(payload.get("domain_pack_id") or "domain envelope")
    extracted_objects = payload.get("extracted_objects")
    object_count = len(extracted_objects) if isinstance(extracted_objects, list) else 0
    lines = [
        (
            f"Validated domain envelope result for {domain_pack_id}. "
            "Full canonical envelope is retained by the specialist runtime; "
            "the supervisor handoff is compact to avoid replaying raw JSON."
        ),
        f"Object count: {object_count}.",
    ]
    findings = payload.get("validation_findings")
    if isinstance(findings, list):
        lines.append(f"Validation finding count: {len(findings)}.")
    return "\n".join(lines)


def _persist_builder_finalization_for_supervisor(
    *,
    builder_finalization: Any,
    builder_workspace: ExtractionBuilderWorkspace,
    tool_name: str,
    specialist_name: str,
    adapter_key: str | None,
    agent_key: str | None,
    trace_id: str | None,
) -> InlineExtractionPersistenceResult:
    document_id = str(builder_workspace.document_id or "").strip()
    normalized_adapter_key = str(adapter_key or "").strip()
    normalized_agent_key = str(agent_key or "").strip()
    normalized_tool_name = str(tool_name or "").strip()
    missing = [
        field_name
        for field_name, value in {
            "document_id": document_id,
            "adapter_key": normalized_adapter_key,
            "agent_key": normalized_agent_key,
            "tool_name": normalized_tool_name,
        }.items()
        if not value
    ]
    if missing:
        raise SpecialistOutputError(
            specialist_name=specialist_name,
            output_type_name="inline_extraction_persistence",
            message=(
                "Validated extraction could not be persisted inline because required "
                f"context is missing: {', '.join(missing)}."
            ),
            details=[
                {
                    "reason": "inline_extraction_persistence_missing_context",
                    "missing": missing,
                    "builder_run_id": builder_workspace.run_id,
                    "builder_invocation_id": builder_workspace.builder_invocation_id,
                }
            ],
        )

    try:
        return persist_inline_validated_extraction_result(
            payload_json=builder_finalization.payload,
            document_id=document_id,
            agent_key=normalized_agent_key,
            adapter_key=normalized_adapter_key,
            tool_name=normalized_tool_name,
            source_kind=CurationExtractionSourceKind.CHAT,
            origin_session_id=get_current_session_id(),
            trace_id=trace_id or get_current_trace_id() or builder_workspace.run_id,
            user_id=get_current_user_id(),
            builder_finalization=builder_finalization,
            metadata={
                "specialist_name": specialist_name,
                "domain_pack_id": builder_workspace.domain_pack_id,
            },
        )
    except SpecialistOutputError:
        raise
    except Exception as exc:
        logger.exception(
            "Inline extraction persistence failed for %s",
            specialist_name,
            extra={
                "specialist_name": specialist_name,
                "tool_name": normalized_tool_name,
                "adapter_key": normalized_adapter_key,
                "agent_key": normalized_agent_key,
                "document_id": document_id,
                "builder_run_id": builder_workspace.run_id,
                "builder_invocation_id": builder_workspace.builder_invocation_id,
                "operation": "inline_extraction_persistence_failed",
            },
        )
        raise SpecialistOutputError(
            specialist_name=specialist_name,
            output_type_name="inline_extraction_persistence",
            message=(
                "Validated extraction could not be persisted inline, so no extraction "
                "result is ready for supervisor handoff."
            ),
            details=[
                {
                    "reason": "inline_extraction_persistence_failed",
                    "error": str(exc),
                    "builder_run_id": builder_workspace.run_id,
                    "builder_invocation_id": builder_workspace.builder_invocation_id,
                }
            ],
        ) from exc


def _first_scalar_value(source: Dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = source.get(key)
        if _is_supervisor_summary_scalar(value):
            text = str(value).strip()
            if text:
                return text
    return ""


def _domain_validator_supervisor_summary(
    payload: Dict[str, Any],
    *,
    expected_output_type: Any,
) -> str:
    """Build a compact supervisor handoff for finalized validator results."""

    if not (
        is_domain_validator_result_schema(expected_output_type)
        or _looks_like_domain_validator_result_payload(payload)
    ):
        return ""

    output_type_name = _output_type_name(expected_output_type)
    status = str(payload.get("status") or "unknown").strip() or "unknown"
    binding_id = str(payload.get("validator_binding_id") or "").strip()
    target = payload.get("target")
    target_label = _domain_validator_target_label(target)
    label_parts = [f"{output_type_name} validator result: status={status}."]
    if binding_id:
        label_parts.append(f"binding={binding_id}.")
    if target_label:
        label_parts.append(f"target={target_label}.")

    lines = [
        " ".join(label_parts)
        + " Full validated payload is retained by the specialist runtime."
    ]

    resolved_values = _compact_supervisor_mapping_fields(
        payload.get("resolved_values"),
        limit=12,
    )
    if resolved_values:
        lines.append(f"Resolved values: {resolved_values}")

    curator_message = str(payload.get("curator_message") or "").strip()
    explanation = str(payload.get("explanation") or "").strip()
    if curator_message:
        lines.append(
            "Curator message: "
            + _truncate_for_supervisor_summary(curator_message, limit=220)
        )
    elif explanation:
        lines.append(
            "Explanation: "
            + _truncate_for_supervisor_summary(explanation, limit=220)
        )

    candidates = payload.get("candidates")
    if isinstance(candidates, list) and candidates:
        candidate_text = _domain_validator_candidate_summary(candidates)
        lines.append(
            f"Candidates: {len(candidates)}"
            + (f" ({candidate_text})" if candidate_text else "")
            + "."
        )

    missing = payload.get("missing_expected_fields")
    if isinstance(missing, list) and missing:
        lines.append(
            "Missing expected fields: "
            + ", ".join(
                _truncate_for_supervisor_summary(str(item), limit=80)
                for item in missing[:8]
            )
            + ("." if len(missing) <= 8 else f", +{len(missing) - 8} more.")
        )

    lookup_attempts = payload.get("lookup_attempts")
    lookup_summary = _domain_validator_lookup_attempt_summary(lookup_attempts)
    if lookup_summary:
        lines.append(f"Lookup attempts: {lookup_summary}")

    return "\n".join(lines)


def _structured_specialist_supervisor_summary(
    payload: Dict[str, Any],
    *,
    expected_output_type: Any,
) -> str:
    """Summarize validated structured payloads without raw JSON transport."""

    output_type_name = _output_type_name(expected_output_type)
    lines = [
        (
            f"{output_type_name} structured result accepted. Full validated "
            "payload is retained by the specialist runtime; the supervisor "
            "handoff is compact to avoid replaying raw JSON."
        )
    ]
    scalar_fields = _compact_supervisor_mapping_fields(
        payload,
        limit=8,
        skip_keys={
            "evidence_records",
            "lookup_attempts",
            "resolved_objects",
            "candidates",
            "candidate_references",
            "metadata",
        },
    )
    if scalar_fields:
        lines.append(f"Top-level fields: {scalar_fields}")

    collection_counts: list[str] = []
    for key, value in sorted(payload.items()):
        if isinstance(value, list):
            collection_counts.append(f"{key}={len(value)}")
        elif isinstance(value, dict):
            collection_counts.append(f"{key}=object")
        if len(collection_counts) >= 8:
            break
    if collection_counts:
        lines.append("Structured collections: " + "; ".join(collection_counts))
    return "\n".join(lines)


def _looks_like_domain_validator_result_payload(payload: Dict[str, Any]) -> bool:
    return {
        "status",
        "validator_binding_id",
        "resolved_values",
        "lookup_attempts",
    }.issubset(payload)


def _domain_validator_target_label(target: Any) -> str:
    if not isinstance(target, dict):
        return ""
    parts: list[str] = []
    for key in ("object_type", "object_id", "object_role", "field_path"):
        value = target.get(key)
        if _is_supervisor_summary_scalar(value):
            parts.append(
                f"{key}={_truncate_for_supervisor_summary(str(value), limit=80)}"
            )
    return "; ".join(parts)


def _compact_supervisor_mapping_fields(
    value: Any,
    *,
    limit: int,
    skip_keys: set[str] | None = None,
) -> str:
    if not isinstance(value, dict):
        return ""
    skip_keys = skip_keys or set()
    selected: list[str] = []
    for key in sorted(value):
        if len(selected) >= limit:
            break
        if key in skip_keys:
            continue
        item = value.get(key)
        if not _is_supervisor_summary_scalar(item):
            continue
        selected.append(
            f"{key}={_truncate_for_supervisor_summary(str(item), limit=120)}"
        )
    return "; ".join(selected)


def _domain_validator_candidate_summary(candidates: list[Any]) -> str:
    summaries: list[str] = []
    for candidate in candidates[:3]:
        if not isinstance(candidate, dict):
            continue
        value = _first_scalar_value(candidate, ("value", "label", "object_type"))
        if value:
            summaries.append(_truncate_for_supervisor_summary(value, limit=80))
    return ", ".join(summaries)


def _domain_validator_lookup_attempt_summary(lookup_attempts: Any) -> str:
    if not isinstance(lookup_attempts, list) or not lookup_attempts:
        return ""
    outcome_counts: dict[str, int] = {}
    for attempt in lookup_attempts:
        if not isinstance(attempt, dict):
            continue
        outcome = str(attempt.get("outcome") or "unknown").strip() or "unknown"
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
    if not outcome_counts:
        return f"{len(lookup_attempts)} total."
    counts = ", ".join(
        f"{outcome}={count}" for outcome, count in sorted(outcome_counts.items())
    )
    return f"{len(lookup_attempts)} total ({counts})."


def _is_supervisor_summary_scalar(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (int, float, bool))


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


# Package tools that need the run-scoped extraction state (builder workspace + resolver
# ledger + evidence records). Maps the LLM-facing tool name to the import path of the raw
# (undecorated) implementation. These tools run as sync function tools, which the Agents
# SDK dispatches on worker threads via asyncio.to_thread; contextvars set on the event
# loop do not reliably appear there, but a per-run CLOSURE does (it rides in the function
# object). So the core rebuilds each of these per run from its raw impl, binding the run
# state inside the worker thread. Package tool bodies are unchanged (they keep calling the
# agr_ai_curation_runtime get_active_* shims). Future work: drive this from binding
# metadata instead of an explicit map so new tools need no core change.
_BUILDER_RUN_STATE_METADATA_KEY = "builder_run_state"


@lru_cache(maxsize=1)
def _run_state_tool_impls() -> Dict[str, str]:
    """Return the registry-derived map of run-state tool name -> raw impl import path.

    A tool is a run-state builder/resolver tool when its package tool-binding metadata declares
    ``builder_run_state: true``. The raw impl path follows the ``_<tool_id>_impl`` convention in
    the same module as the tool's public ``callable`` binding. Deriving this from binding metadata
    (rather than a hardcoded per-type literal) keeps run-state binding a domain-pack/registry
    concern, so adding a new builder data type needs no platform edit. (Mirrors Phase 0's
    ``builder_finalization_tool_names``.)
    """
    from src.lib.packages.tool_registry import load_tool_registry

    registry = load_tool_registry()
    impls: Dict[str, str] = {}
    for binding in registry.bindings:
        metadata = binding.metadata if isinstance(binding.metadata, dict) else {}
        if not bool(metadata.get(_BUILDER_RUN_STATE_METADATA_KEY)):
            continue
        import_path = binding.import_path or ""
        module_name = import_path.split(":", 1)[0] if ":" in import_path else import_path
        if not module_name:
            continue
        impls[binding.tool_id] = f"{module_name}:_{binding.tool_id}_impl"
    return impls


def _build_run_state_bound_tool(
    raw_func: Any,
    existing_tool: Any,
    *,
    builder_workspace: ExtractionBuilderWorkspace,
    resolver_ledger: ResolverCallLedger,
    evidence_records: List[Dict[str, Any]],
) -> Any:
    """Rebuild a package tool so the run-scoped state is bound INSIDE the tool's worker
    thread via a per-run closure.

    The OpenAI Agents SDK runs sync function tools on worker threads (asyncio.to_thread).
    Contextvars set on the event loop do not reliably appear in that thread, but a closure
    does -- it is captured in the function object -- which is exactly why the async
    ``record_evidence`` factory works. We capture the run's workspace/ledger/evidence in a
    closure and bind them into the contextvars at the top of the call (running in-thread),
    where the unchanged tool body's ``get_active_*`` shims then resolve. ``functools.wraps``
    preserves the original signature so the LLM-facing JSON schema is identical, and the
    tool stays sync so concurrency is unchanged (the DB call still runs in the worker
    thread exactly as before).
    """
    import functools

    from agents import function_tool

    @function_tool(
        strict_mode=bool(getattr(existing_tool, "strict_json_schema", True)),
        name_override=getattr(existing_tool, "name", None) or raw_func.__name__,
        description_override=getattr(existing_tool, "description", "") or "",
    )
    @functools.wraps(raw_func)
    def _run_state_bound(*args: Any, **kwargs: Any) -> Any:
        ev_token = set_active_evidence_records(evidence_records)
        bw_token = set_active_extraction_builder_workspace(builder_workspace)
        rl_token = set_active_resolver_call_ledger(resolver_ledger)
        try:
            return raw_func(*args, **kwargs)
        finally:
            reset_active_resolver_call_ledger(rl_token)
            reset_active_extraction_builder_workspace(bw_token)
            reset_active_evidence_records(ev_token)

    return _run_state_bound


def _bind_run_state_into_tools(
    agent: Agent,
    *,
    evidence_records: List[Dict[str, Any]],
    builder_workspace: ExtractionBuilderWorkspace,
    resolver_ledger: ResolverCallLedger,
) -> Agent:
    """Replace each run-state package tool on the agent with a closure-bound rebuild for
    this run. Non-run-state tools are left untouched. Mirrors
    ``_adapt_tools_with_provider_adapter``'s tool-replacement pattern."""
    rebuilt: List[Any] = []
    bound_count = 0
    run_state_tool_impls = _run_state_tool_impls()
    for tool in list(getattr(agent, "tools", []) or []):
        tool_name = _extract_tool_name(tool)
        impl_path = run_state_tool_impls.get(tool_name)
        if impl_path is None:
            rebuilt.append(tool)
            continue
        raw_func = _import_callable(impl_path)
        rebuilt.append(
            _build_run_state_bound_tool(
                raw_func,
                tool,
                builder_workspace=builder_workspace,
                resolver_ledger=resolver_ledger,
                evidence_records=evidence_records,
            )
        )
        bound_count += 1
    agent.tools = rebuilt
    logger.info(
        "Rebuilt %d run-state tool(s) with closure-bound run state for run_id=%s",
        bound_count,
        builder_workspace.run_id,
        extra={"builder_run_id": builder_workspace.run_id},
    )
    return agent


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
                "toolName": "domain_validator_lookup",
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
                "toolName": "domain_validator_lookup",
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


def _agent_runtime_curation_adapter_key(agent: Agent) -> Optional[str]:
    """Return curation adapter metadata attached during runtime agent creation."""

    raw_metadata = getattr(agent, "curation_metadata", None)
    if not isinstance(raw_metadata, Mapping):
        raw_metadata = getattr(agent, "curation", None)
    if not isinstance(raw_metadata, Mapping):
        return None
    if not bool(raw_metadata.get("launchable", False)):
        return None
    adapter_key = str(raw_metadata.get("adapter_key") or "").strip()
    return adapter_key or None


def _agent_runtime_canonical_agent_key(agent: Agent) -> Optional[str]:
    """Return the canonical DB/config agent key attached during runtime creation."""

    for attr_name in ("agent_key", "canonical_agent_key"):
        agent_key = str(getattr(agent, attr_name, "") or "").strip()
        if agent_key:
            return agent_key
    return None


async def _dispatch_domain_envelope_validators_for_chat(
    final_output: str,
    *,
    expected_output_type: Any,
    specialist_name: str,
    tool_name: Optional[str],
    adapter_key: Optional[str] = None,
    source_agent_key: Optional[str] = None,
    is_builder_envelope: bool = False,
    runtime_context: Optional[Any] = None,
) -> str:
    """Run active domain-pack validators before extractor output reaches supervisor.

    Envelope extractors declare a domain-envelope output schema, so the gate below
    recognizes their structured output. Builder/materializer agents have NO output schema
    (expected_output_type is None) and produce their envelope via the builder workspace, so
    that gate would short-circuit them. When the caller already holds a finalized builder
    envelope it passes ``is_builder_envelope=True`` (with ``final_output`` set to that
    envelope's JSON) to run the same validator dispatch on it.
    """

    if not is_builder_envelope and not _is_domain_envelope_output_json(
        final_output,
        expected_output_type=expected_output_type,
    ):
        return final_output

    agent_key = str(source_agent_key or "").strip() or _agent_key_from_specialist_tool_name(tool_name)
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
        from src.lib.curation_workspace.domain_envelope_normalization import (
            domain_envelope_from_extraction_result,
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
        adapter_key=adapter_key,
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
        envelope = domain_envelope_from_extraction_result(extraction_record)
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
                    "object_count": len(envelope.extracted_objects),
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
        dispatch_kwargs: Dict[str, Any] = {
            "event_emitter": _emit_validator_dispatch_event,
            "source_envelope_revision": 1,
            "runtime_context": runtime_context,
        }
        dispatch_result = await asyncio.to_thread(
            dispatch_active_validator_bindings,
            envelope,
            domain_pack,
            **dispatch_kwargs,
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
            # A validator could not RUN its lookup (e.g. a flaky validator tool call). This is NOT
            # fatal: the dispatch already recorded it as an OPEN `validator_error` finding on the
            # envelope, so the extraction persists and the curator reviews the flagged field. Log it
            # (with the underlying error) so legitimate validator failures stay debuggable, and emit
            # a non-fatal event for the UI/trace. Genuine crashes are still raised by `except` below.
            error_details = _validator_dispatch_error_details(dispatch_result)
            logger.warning(
                "%s chat domain-envelope validation recorded %s validator error(s); "
                "persisted as validator_error finding(s) for curator review (non-fatal)",
                specialist_name,
                len(error_details),
                extra={
                    "specialist_name": specialist_name,
                    "tool_name": tool_name,
                    "domain_pack_id": envelope.domain_pack_id,
                    "operation": "chat_domain_envelope_validation",
                    "validator_dispatch_errors": error_details,
                },
            )
            add_specialist_event({
                "type": "SPECIALIST_ERROR",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": {
                    "specialist": specialist_name,
                    "error": (
                        "Domain-envelope validator dispatch recorded validator error(s); "
                        "persisted as validator_error findings for curator review."
                    ),
                    "reason": "domain_validator_dispatch_error",
                    "severity": "warning",
                    "fatal": False,
                    "validatorDispatchErrors": error_details,
                },
            })

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
        # A1: mark the envelope so the curation-stage validator pass reuses these findings
        # instead of re-running the validator agents. Validation happens once, in the chat turn;
        # the curation bootstrap reads the saved findings. (Curator edits re-validate via the
        # session validation service, a separate path.) In-place dict mutation, safe on frozen models.
        dispatch_result.envelope.metadata["inline_validator_dispatch_complete"] = True
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
# inefficient patterns like repeated calls where the package registry says one
# batched call would work.

# Threshold for triggering the nudge (3 consecutive calls to same specialist)
# Env-configurable via BATCHING_NUDGE_THRESHOLD (default 3); see config.py.
BATCHING_NUDGE_THRESHOLD = get_batching_nudge_threshold()


def get_batching_config() -> Dict[str, Any]:
    """
    Generate batching config from AGENT_REGISTRY.

    Returns dict keyed by supervisor tool name (e.g., "ask_gene_specialist")
    with entity and example for batching nudge prompts.
    """
    try:
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY
    except ImportError:
        return {}

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
    - The tool supports batching according to package/catalog metadata
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
    output_payload: Optional[Dict[str, Any]] = None
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
    register_internal_extraction_event(event)

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


def _build_structured_finalization_internal_result_event(
    *,
    tool_name: str,
    specialist_name: str,
    payload: Mapping[str, Any],
    output_type_name: str,
    finalization_tool_name: str,
    timestamp: str | None = None,
) -> Dict[str, Any]:
    """Build the backend-only handoff event for generic structured finalization."""

    canonical_payload = copy.deepcopy(dict(payload))
    canonical_output = json.dumps(canonical_payload)
    return {
        "type": INTERNAL_EXTRACTION_RESULT_EVENT_TYPE,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "details": {
            "toolName": tool_name,
            "friendlyName": f"{specialist_name}: Internal Extraction Result",
            "success": True,
            "isSpecialistInternal": True,
            "structuredFinalization": {
                "output_type": output_type_name,
                "tool_name": finalization_tool_name,
            },
        },
        "internal": {
            "tool_output": canonical_output,
            "canonical_payload": canonical_payload,
            "structured_finalization": {
                "output_type": output_type_name,
                "tool_name": finalization_tool_name,
            },
            "output_length": len(canonical_output),
        },
    }


def _builder_finalizer_tool_calls(
    *,
    tool_calls: List[SpecialistToolCall],
) -> List[SpecialistToolCall]:
    """Return builder finalizer tool calls observed in the specialist stream."""

    finalizer_names = builder_finalization_tool_names()
    return [
        call
        for call in tool_calls
        if str(call.tool_name or "").strip() in finalizer_names
    ]


def _builder_finalization_diagnostics(
    *,
    builder_workspace: ExtractionBuilderWorkspace,
    specialist_name: str,
    tool_name: Optional[str],
    tool_calls: List[SpecialistToolCall],
    final_output: str,
) -> Dict[str, Any]:
    """Build trace-visible diagnostics for the builder finalization handoff."""

    finalization = builder_workspace.finalization
    finalizer_calls = _builder_finalizer_tool_calls(tool_calls=tool_calls)
    candidates = [
        {
            "candidateId": candidate_id,
            "status": candidate.status,
            "evidenceRecordCount": len(candidate.evidence_record_ids),
            "pendingRefCount": len(candidate.pending_ref_ids),
            "resolverSelectionCount": len(candidate.resolver_selection_refs),
        }
        for candidate_id, candidate in builder_workspace.candidates.items()
    ]
    return {
        "specialist": specialist_name,
        "toolName": tool_name,
        "builderRunId": builder_workspace.run_id,
        "builderInvocationId": builder_workspace.builder_invocation_id,
        "workspaceState": builder_workspace.state,
        "finalizationPresent": finalization is not None,
        "finalizationCandidateIds": (
            list(finalization.candidate_ids) if finalization is not None else []
        ),
        "finalizationSourceCandidateIds": (
            list(finalization.source_candidate_ids) if finalization is not None else []
        ),
        "finalizationEvidenceRecordIds": (
            list(finalization.evidence_record_ids) if finalization is not None else []
        ),
        "finalizerToolCalls": [call.tool_name for call in finalizer_calls],
        "finalizerToolCallCount": len(finalizer_calls),
        "allToolCalls": [call.tool_name for call in tool_calls],
        "candidateCount": len(candidates),
        "candidates": candidates,
        "validationErrorCount": len(builder_workspace.validation_errors),
        "finalOutputType": type(final_output).__name__,
        "finalOutputLength": len(final_output or ""),
    }


def _emit_builder_finalization_state(
    *,
    builder_workspace: ExtractionBuilderWorkspace,
    specialist_name: str,
    tool_name: Optional[str],
    tool_calls: List[SpecialistToolCall],
    final_output: str,
) -> Dict[str, Any]:
    diagnostics = _builder_finalization_diagnostics(
        builder_workspace=builder_workspace,
        specialist_name=specialist_name,
        tool_name=tool_name,
        tool_calls=tool_calls,
        final_output=final_output,
    )
    logger.info(
        "%s builder finalization state: present=%s state=%s finalizer_calls=%s",
        specialist_name,
        diagnostics["finalizationPresent"],
        diagnostics["workspaceState"],
        diagnostics["finalizerToolCalls"],
        extra={
            "specialist_name": specialist_name,
            "tool_name": tool_name,
            "builder_run_id": builder_workspace.run_id,
            "builder_invocation_id": builder_workspace.builder_invocation_id,
            "builder_finalization_present": diagnostics["finalizationPresent"],
            "builder_state": diagnostics["workspaceState"],
            "builder_finalizer_tool_calls": diagnostics["finalizerToolCalls"],
            "operation": "builder_finalization_state",
        },
    )
    add_specialist_event({
        "type": "SPECIALIST_BUILDER_FINALIZATION_STATE",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": diagnostics,
    })
    return diagnostics


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
    inline_chat_persistence: bool = True,
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
        inline_chat_persistence: When True (chat supervisor path), validated builder
            finalization is persisted inline as a CHAT-source extraction result and the
            INTERNAL_EXTRACTION_RESULT event carries the persisted identifiers. When
            False (flow execution path), inline CHAT persistence is skipped entirely;
            flows own their FLOW-source persistence separately, so the internal event is
            emitted without CHAT persisted identifiers.

    Returns:
        The specialist's final output as a string
    """
    _set_last_supervisor_extraction_handoff(None)
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

    expected_output_type = getattr(agent, "output_type", None)
    runtime_curation_adapter_key = _agent_runtime_curation_adapter_key(agent)
    runtime_canonical_agent_key = _agent_runtime_canonical_agent_key(agent)
    builder_materializer_agent = is_builder_materializer_agent(agent)
    if builder_materializer_agent and expected_output_type is not None:
        output_type_name = getattr(expected_output_type, "__name__", "response")
        raise SpecialistOutputError(
            specialist_name,
            output_type_name,
            message=(
                f"{specialist_name} is configured as a builder/materializer specialist "
                f"but also declares {output_type_name} structured output. Backend "
                "builder finalization owns canonical extraction output for this agent."
            ),
            details=[
                {
                    "reason": "builder_materializer_output_schema_forbidden",
                    "output_type": output_type_name,
                }
            ],
        )
    finalization_config = _agent_structured_finalization_config(
        agent,
        tool_name=tool_name,
    )
    finalization_tool_name = _structured_specialist_finalization_tool_name(
        finalization_config
    )
    structured_finalization_state = _StructuredSpecialistFinalizationState(
        required=_structured_specialist_finalization_required(
            agent,
            expected_output_type=expected_output_type,
            builder_materializer_agent=builder_materializer_agent,
            finalization_config=finalization_config,
        ),
        tool_name=finalization_tool_name or "finalize_structured_result",
        agent_name=specialist_name,
        output_type_name=(
            _structured_finalization_input_schema_name(finalization_config)
            or _output_type_name(expected_output_type)
        ),
        config=finalization_config,
        max_attempts=_structured_specialist_finalization_max_attempts(
            finalization_config
        ),
    )
    if structured_finalization_state.required:
        max_turns = _max_turns_with_structured_specialist_finalization(max_turns)

    logger.info(
        "Starting specialist=%s (max_turns=%s, structured_finalization=%s)",
        specialist_name,
        max_turns,
        structured_finalization_state.required,
        extra={"specialist_name": specialist_name, "tool_name": tool_name},
    )
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

    if structured_finalization_state.required:
        runtime_agent = _configure_structured_specialist_finalization(
            runtime_agent,
            agent,
            expected_output_type=expected_output_type,
            finalization_state=structured_finalization_state,
            tool_calls=tool_calls,
            live_evidence_records=live_evidence_records,
        )
        logger.info(
            "%s applying mandatory structured finalization tool %s",
            specialist_name,
            structured_finalization_state.tool_name,
            extra={"specialist_name": specialist_name, "tool_name": tool_name},
        )
        # Layer 2: force a tool call every turn (no bare-text final answers) and
        # end the run the instant finalize is accepted. Skip the Groq tool/JSON
        # compatibility path, which has its own provider constraints. Gated by the
        # removable kill-switch so it can be disabled instantly during regression.
        if not groq_tool_json_compat_mode and LAYER2_FORCE_TOOL_FINALIZATION_ENABLED:
            runtime_agent = _apply_layer2_forced_tool_finalization(
                runtime_agent,
                structured_finalization_state,
            )
            logger.info(
                "%s applying Layer 2 forced-tool finalization (tool_choice=required)",
                specialist_name,
                extra={"specialist_name": specialist_name, "tool_name": tool_name},
            )

    # Commit pending prompts for this specialist - moves from pending to used
    # This is where the agent ACTUALLY executes, so we log the prompts now
    commit_pending_prompts(runtime_agent)

    effective_config = _run_config_with_full_trace_payloads(run_config)

    # Bind the run-scoped extraction context (evidence records, builder workspace,
    # resolver ledger) BEFORE starting the streamed run. Runner.run_streamed() snapshots
    # the current context for the SDK's background execution task, so any contextvar bound
    # AFTER it is invisible to the specialist's tools (record_evidence / stage /
    # attach_evidence / finalize), which manifests as "No active extraction builder
    # workspace is bound to this run". This mirrors the supervisor's ordering in runner.py
    # (bind, then run). trace_run is established earlier in the supervisor flow, so the
    # builder run_id still matches the real trace_id.
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
    resolver_call_ledger = ResolverCallLedger(trace_id=builder_workspace.run_id)
    resolver_call_ledger_token = set_active_resolver_call_ledger(resolver_call_ledger)
    logger.info(
        "%s bound extraction builder workspace before run start (run_id=%s, "
        "document_id=%s, domain_pack_id=%s, trace_run_present=%s)",
        specialist_name,
        builder_workspace.run_id,
        builder_workspace.document_id,
        builder_workspace.domain_pack_id,
        trace_run is not None,
        extra={
            "specialist_name": specialist_name,
            "tool_name": tool_name,
            "builder_run_id": builder_workspace.run_id,
        },
    )

    # Rebuild the run-state package tools so the builder workspace + resolver ledger +
    # evidence records are bound INSIDE each tool's worker thread via a per-run closure.
    # The SDK runs sync function tools on worker threads (asyncio.to_thread) where the
    # contextvars set above do not reliably appear; a closure does (it rides in the
    # function object), so each tool resolves its run state regardless of the thread
    # boundary. Tool bodies and the package contract are unchanged.
    runtime_agent = _bind_run_state_into_tools(
        runtime_agent,
        evidence_records=live_evidence_records,
        builder_workspace=builder_workspace,
        resolver_ledger=resolver_call_ledger,
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
                        output_payload = _tool_output_payload_for_finalization(
                            current_tool_name,
                            output,
                        )
                        if isinstance(tool_index, int) and 0 <= tool_index < len(tool_calls):
                            tool_calls[tool_index].output_preview = output_preview
                            tool_calls[tool_index].output_summary = output_summary
                            tool_calls[tool_index].output_payload = output_payload
                            tool_calls[tool_index].duration_ms = duration_ms

                        evidence_record = build_record_evidence_summary_record(
                            tool_name=current_tool_name,
                            tool_input=completed_tool.get("tool_args"),
                            tool_output=output,
                        )
                        if evidence_record is not None:
                            live_evidence_records.append(evidence_record)

                        if current_tool_name == RESOLVER_TOOL_NAME:
                            resolver_call_ledger.record_tool_output(
                                tool_call_id=str(completed_tool.get("tool_id") or "") or None,
                                tool_name=current_tool_name,
                                output=output,
                            )

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

                        # Check if tool output contains FileInfo (file download).
                        # Runtime formatter projection tools return FileInfo as JSON.
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
        reset_active_resolver_call_ledger(resolver_call_ledger_token)
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

    if structured_finalization_state.required and structured_finalization_state.accepted:
        final_output = json.dumps(structured_finalization_state.accepted_payload)
        logger.info(
            "%s using accepted %s payload from %s as canonical output",
            specialist_name,
            structured_finalization_state.output_type_name,
            structured_finalization_state.tool_name,
        )
    elif structured_finalization_state.required:
        _raise_missing_structured_specialist_finalization(
            state=structured_finalization_state,
            specialist_name=specialist_name,
            builder_workspace=builder_workspace,
            tool_name=tool_name,
            candidate_id=builder_candidate_id,
        )
    elif hasattr(result, "final_output") and result.final_output is not None:
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

                    # The retry agent reuses the original specialist's model, which
                    # is always set at load time. A missing model is a bug, so fail
                    # loud rather than silently falling back to a different model.
                    retry_model = getattr(agent, 'model', None)
                    if not retry_model:
                        raise ValueError(
                            f"Cannot build retry agent for {specialist_name}: the "
                            f"specialist agent has no model configured."
                        )

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
                        # Reduced turns - just need output synthesis.
                        # Env-configurable via STRUCTURED_FINALIZATION_RETRY_MAX_TURNS.
                        max_turns=get_structured_finalization_retry_max_turns(),
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
    builder_finalization = builder_workspace.finalization
    if builder_materializer_agent:
        finalization_diagnostics = _emit_builder_finalization_state(
            builder_workspace=builder_workspace,
            specialist_name=specialist_name,
            tool_name=tool_name,
            tool_calls=tool_calls,
            final_output=final_output,
        )
        if builder_finalization is None:
            error_message = (
                f"{specialist_name} is a builder/materializer specialist but did "
                "not leave a finalized backend builder payload after the run."
            )
            logger.error(
                "%s diagnostics=%s",
                error_message,
                finalization_diagnostics,
                extra={
                    "specialist_name": specialist_name,
                    "tool_name": tool_name,
                    "builder_run_id": builder_workspace.run_id,
                    "builder_invocation_id": builder_workspace.builder_invocation_id,
                    "builder_state": builder_workspace.state,
                    "builder_finalizer_tool_calls": finalization_diagnostics[
                        "finalizerToolCalls"
                    ],
                    "operation": "builder_finalization_missing",
                },
            )
            add_specialist_event({
                "type": "SPECIALIST_BUILDER_FINALIZATION_MISSING",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": {
                    **finalization_diagnostics,
                    "reason": "builder_finalization_missing",
                    "message": error_message,
                    "severity": "error",
                },
            })
            raise SpecialistOutputError(
                specialist_name=specialist_name,
                output_type_name="builder_finalization",
                message=error_message,
                details=[
                    {
                        **finalization_diagnostics,
                        "reason": "builder_finalization_missing",
                    }
                ],
            )
        if not tool_name:
            error_message = (
                f"{specialist_name} is a builder/materializer specialist but was "
                "invoked without a supervisor tool name, so its finalized payload "
                "cannot be emitted for persistence."
            )
            logger.error(
                "%s diagnostics=%s",
                error_message,
                finalization_diagnostics,
                extra={
                    "specialist_name": specialist_name,
                    "builder_run_id": builder_workspace.run_id,
                    "builder_invocation_id": builder_workspace.builder_invocation_id,
                    "operation": "builder_materializer_tool_name_missing",
                },
            )
            add_specialist_event({
                "type": "SPECIALIST_BUILDER_TOOL_NAME_MISSING",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": {
                    **finalization_diagnostics,
                    "reason": "builder_materializer_tool_name_missing",
                    "message": error_message,
                    "severity": "error",
                },
            })
            raise SpecialistOutputError(
                specialist_name=specialist_name,
                output_type_name="builder_finalization",
                message=error_message,
                details=[
                    {
                        **finalization_diagnostics,
                        "reason": "builder_materializer_tool_name_missing",
                    }
                ],
            )

    phase_timings_ms["post_stream_output_ms"] = _elapsed_ms(post_stream_started_at)

    evidence_summary_started_at = time.monotonic()
    if builder_finalization is None:
        try:
            _emit_specialist_evidence_summary_or_raise(
                specialist_name=specialist_name,
                tool_name=tool_name,
                expected_output_type=expected_output_type,
                final_output=final_output,
                live_evidence_records=live_evidence_records,
            )
        except SpecialistOutputError:
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
    else:
        builder_evidence_records = live_evidence_records
        if not builder_evidence_records and builder_finalization is not None:
            try:
                builder_evidence_records = extract_evidence_records_from_structured_result(
                    json.dumps(builder_finalization.payload)
                )
            except (TypeError, ValueError):
                builder_evidence_records = []
        _emit_specialist_evidence_summary(
            tool_name=tool_name,
            evidence_records=builder_evidence_records,
        )
    phase_timings_ms["evidence_summary_ms"] = _elapsed_ms(
        evidence_summary_started_at
    )

    validator_dispatch_started_at = time.monotonic()
    if builder_finalization is None:
        try:
            final_output = await _dispatch_domain_envelope_validators_for_chat(
                final_output,
                expected_output_type=expected_output_type,
                specialist_name=specialist_name,
                tool_name=tool_name,
                adapter_key=runtime_curation_adapter_key,
                source_agent_key=runtime_canonical_agent_key,
                runtime_context=_validator_runtime_context_for_chat(
                    document_id=builder_workspace.document_id,
                    user_id=get_current_user_id(),
                ),
            )
        except SpecialistOutputError as exc:
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
            logger.warning(
                "%s chat domain-envelope validation failed after plain output: %s",
                specialist_name,
                dispatch_errors,
                extra={
                    "specialist_name": specialist_name,
                    "tool_name": tool_name,
                    "adapter_key": runtime_curation_adapter_key,
                    "source_agent_key": runtime_canonical_agent_key,
                    "operation": "chat_domain_envelope_validation_failed",
                },
            )
            builder_workspace.record_validation_failure(
                errors=dispatch_errors,
                candidate_ids=[builder_candidate_id],
            )
            raise
        builder_finalization = builder_workspace.finalization
    elif builder_finalization is not None and tool_name:
        # Builder/materializer path: the agent finalized in-loop, so the envelope-path
        # dispatch above was skipped. Run the SAME validator dispatch on the builder's
        # finalized envelope so validation happens in the chat turn (extraction ->
        # validation -> reply), matching the envelope extractors. Fold the validated
        # envelope (DomainEnvelope shape, with findings embedded) back into the
        # finalization so the supervisor summary + persistence carry the validated result.
        try:
            validated_builder_output = await _dispatch_domain_envelope_validators_for_chat(
                json.dumps(builder_finalization.payload),
                expected_output_type=expected_output_type,
                specialist_name=specialist_name,
                tool_name=tool_name,
                adapter_key=runtime_curation_adapter_key,
                source_agent_key=runtime_canonical_agent_key,
                is_builder_envelope=True,
                runtime_context=_validator_runtime_context_for_chat(
                    document_id=builder_workspace.document_id,
                    user_id=get_current_user_id(),
                ),
            )
        except SpecialistOutputError as exc:
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
            logger.warning(
                "%s chat domain-envelope validation failed after builder finalization: %s",
                specialist_name,
                dispatch_errors,
                extra={
                    "specialist_name": specialist_name,
                    "tool_name": tool_name,
                    "adapter_key": runtime_curation_adapter_key,
                    "source_agent_key": runtime_canonical_agent_key,
                    "operation": "chat_domain_envelope_validation_failed",
                },
            )
            finalized_candidate_ids = (
                builder_finalization.candidate_ids or (builder_candidate_id,)
            )
            builder_workspace.record_validation_failure(
                errors=dispatch_errors,
                candidate_ids=finalized_candidate_ids,
            )
            raise
        try:
            validated_builder_payload = json.loads(validated_builder_output)
        except (TypeError, ValueError):
            validated_builder_payload = None
        if isinstance(validated_builder_payload, dict):
            builder_finalization = replace(
                builder_finalization, payload=validated_builder_payload
            )
            builder_workspace.finalization = builder_finalization
    phase_timings_ms["domain_validator_dispatch_ms"] = _elapsed_ms(
        validator_dispatch_started_at
    )
    retained_evidence_source = (
        json.dumps(builder_finalization.payload)
        if builder_finalization is not None
        else final_output
    )
    retained_evidence_records = extract_evidence_records_from_structured_result(
        retained_evidence_source
    )

    inline_persistence: InlineExtractionPersistenceResult | None = None
    internal_event_started_at = time.monotonic()
    if tool_name and builder_finalization is not None:
        final_output = json.dumps(builder_finalization.payload)
        if inline_chat_persistence:
            inline_persistence = _persist_builder_finalization_for_supervisor(
                builder_finalization=builder_finalization,
                builder_workspace=builder_workspace,
                tool_name=tool_name,
                specialist_name=specialist_name,
                adapter_key=runtime_curation_adapter_key,
                agent_key=runtime_canonical_agent_key,
                trace_id=trace_run.trace_id if trace_run is not None else None,
            )
            handoff = _build_supervisor_extraction_handoff(
                tool_name=tool_name,
                specialist_name=specialist_name,
                payload=builder_finalization.payload,
                inline_persistence=inline_persistence,
                adapter_key=runtime_curation_adapter_key,
                agent_key=runtime_canonical_agent_key,
            )
            if handoff is not None:
                _set_last_supervisor_extraction_handoff(handoff)
            add_specialist_event(
                build_internal_extraction_result_event(
                    tool_name=tool_name,
                    specialist_name=specialist_name,
                    finalization=builder_finalization,
                    extraction_result_id=inline_persistence.extraction_result_id,
                    result_ref=inline_persistence.result_ref,
                    persistence_status={
                        "phase": "inline_validated_extraction",
                        "created_new": inline_persistence.created_new,
                        "idempotency_key": inline_persistence.idempotency_key,
                        "payload_hash": inline_persistence.payload_hash,
                    },
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            )
        else:
            # Flow execution path: inline CHAT persistence is a chat-stream concept and
            # must not run here. Flows persist their own FLOW-source rows separately via
            # the executor. Restore the pre-branch flow emission: the internal extraction
            # event WITHOUT CHAT persisted identifiers (no extraction_result_id/result_ref/
            # persistence_status), and no supervisor extraction handoff.
            add_specialist_event(
                build_internal_extraction_result_event(
                    tool_name=tool_name,
                    specialist_name=specialist_name,
                    finalization=builder_finalization,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            )
    elif (
        tool_name
        and structured_finalization_state.required
        and structured_finalization_state.accepted_payload is not None
    ):
        add_specialist_event(
            _build_structured_finalization_internal_result_event(
                tool_name=tool_name,
                specialist_name=specialist_name,
                payload=structured_finalization_state.accepted_payload,
                output_type_name=structured_finalization_state.output_type_name,
                finalization_tool_name=structured_finalization_state.tool_name,
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
        finalized_domain_envelope=builder_finalization is not None,
        extraction_result_id=(
            inline_persistence.extraction_result_id
            if inline_persistence is not None
            else None
        ),
        result_ref=inline_persistence.result_ref if inline_persistence is not None else None,
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


def _validator_runtime_context_for_chat(
    *,
    document_id: Optional[str],
    user_id: Optional[str],
) -> Optional[Any]:
    normalized_document_id = str(document_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if (
        not normalized_document_id
        or not normalized_user_id
        or normalized_document_id == "chat-runtime"
    ):
        return None

    from src.lib.domain_packs.validator_dispatch import ValidatorRuntimeContext

    return ValidatorRuntimeContext(
        document_id=normalized_document_id,
        user_id=normalized_user_id,
    )
