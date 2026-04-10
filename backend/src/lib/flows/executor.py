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
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict, List, Optional, Set
from uuid import uuid4

from agents import Agent, function_tool

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
from src.lib.curation_workspace.curation_prep_constants import (
    CURATION_PREP_AGENT_ID,
)
from src.lib.file_outputs import FileValidationError, sanitize_output_descriptor
from src.models.sql.curation_flow import CurationFlow
from src.lib.agent_studio.catalog_service import (
    get_agent_by_id,
    get_agent_metadata,
)
from src.lib.openai_agents.config import (
    get_agent_config,
    get_model_for_agent,
    build_model_settings,
    resolve_model_provider,
)
from src.lib.openai_agents.evidence_summary import _EvidenceRegistry
from src.lib.openai_agents.agents.supervisor_agent import _create_streaming_tool
from src.lib.document_context import DocumentContext
from src.schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)
from src.schemas.curation_prep import CurationPrepScopeConfirmation

logger = logging.getLogger(__name__)

_FLOW_STEP_OUTPUT_PREVIEW_CHARS = 800
_FLOW_STEP_EVIDENCE_PREVIEW_LIMIT = 10
_FLOW_TEMPLATE_VARIABLE_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")
_FLOW_TEMPLATE_DEFAULT_INPUT_FILENAME = "input"
_FLOW_TEMPLATE_DEFAULT_DESCRIPTOR = "output"
_FLOW_TEMPLATE_DEFAULT_TRACE_ID = "trace"


def _now_iso() -> str:
    """Return current UTC time in ISO format for audit events."""
    return datetime.now(timezone.utc).isoformat()


def _tool_safe_agent_id(agent_id: str) -> str:
    """Normalize agent_id into a valid Python identifier segment for tool names."""
    return agent_id.replace("-", "_")


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


def _truncate_tool_output(value: Any, max_chars: int = _FLOW_STEP_OUTPUT_PREVIEW_CHARS) -> str:
    """Generate a bounded preview string for accumulated flow context."""

    text = _stringify_tool_output(value).strip()
    if len(text) <= max_chars:
        return text
    overflow = len(text) - max_chars
    return f"{text[:max_chars]}... [truncated {overflow} chars]"


def _build_flow_step_instruction_prefix(
    *,
    custom_instructions: Optional[str],
    include_evidence: Optional[bool],
) -> str:
    """Build step-local instructions prepended onto the selected agent."""

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


def _stringify_flow_template_value(value: Any) -> str:
    """Normalize template variable values into stable strings."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            pass
    return str(value)


def _get_task_input_output_key(flow: CurationFlow) -> Optional[str]:
    """Return the configured output_key for the task_input node, if present."""

    for node in flow.flow_definition.get("nodes", []):
        data = node.get("data", {})
        if node.get("type") == "task_input" or data.get("agent_id") == "task_input":
            output_key = str(data.get("output_key") or "").strip()
            return output_key or None
    return None


def _build_initial_flow_template_variables(
    flow: CurationFlow,
    user_query: Optional[str],
) -> dict[str, str]:
    """Bind task_input output_key so downstream templates can reference the flow input."""

    output_key = _get_task_input_output_key(flow)
    if not output_key:
        return {}
    return {output_key: _build_flow_conversation_summary(flow, user_query)}


def _build_flow_template_variables(
    *,
    stored_variables: dict[str, Any],
    document_name: Optional[str],
    flow_run_id: Optional[str],
    timestamp: Optional[str] = None,
) -> dict[str, str]:
    """Assemble built-in and step-bound variables for one template-render pass."""

    variables = {
        key: _stringify_flow_template_value(value)
        for key, value in stored_variables.items()
        if str(key or "").strip()
    }
    variables.update(
        {
            "input_filename": _extract_flow_input_filename(document_name),
            "input_filename_stem": _extract_flow_input_filename_stem(document_name),
            "trace_id": (
                get_current_trace_id()
                or str(flow_run_id or "").strip()
                or _FLOW_TEMPLATE_DEFAULT_TRACE_ID
            ),
            "timestamp": timestamp or _format_flow_template_timestamp(),
        }
    )
    return variables


def _render_flow_template(
    template: Optional[str],
    template_variables: dict[str, str],
) -> str:
    """Render {{variable}} placeholders using the provided flow variable map."""

    if not isinstance(template, str):
        return ""
    return _FLOW_TEMPLATE_VARIABLE_PATTERN.sub(
        lambda match: template_variables.get(match.group(1), ""),
        template,
    )


def _resolve_flow_step_query(
    *,
    input_source: Any,
    custom_input: Optional[str],
    default_query: str,
    template_variables: dict[str, str],
) -> str:
    """Resolve the per-step query.

    Only input_source='custom' overrides the supervisor-provided query. The existing
    supervisor conversation remains authoritative for 'previous_output' and 'user_query'
    so this ticket can ship deterministic custom-input templates without rewriting the
    broader flow prompting model.
    """

    if str(input_source or "previous_output").strip() != "custom":
        return default_query

    rendered = _render_flow_template(custom_input, template_variables).strip()
    return rendered or default_query


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
        return _FLOW_TEMPLATE_DEFAULT_DESCRIPTOR

    try:
        return sanitize_output_descriptor(rendered)
    except FileValidationError:
        return _FLOW_TEMPLATE_DEFAULT_DESCRIPTOR


def _resolve_flow_candidate_adapter_key(candidate: ExtractionEnvelopeCandidate) -> Optional[str]:
    """Return the adapter-owned key already persisted on the extraction envelope."""

    normalized = str(candidate.adapter_key or "").strip()
    return normalized or None


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


def _build_flow_scope_confirmation(
    extraction_results: list[CurationExtractionResultRecord],
    *,
    flow_name: str,
) -> CurationPrepScopeConfirmation:
    """Build deterministic prep scope from upstream flow extraction results."""

    adapter_keys = _unique_non_empty_scope_values(
        [record.adapter_key for record in extraction_results]
    )

    return CurationPrepScopeConfirmation(
        confirmed=True,
        adapter_keys=adapter_keys,
        notes=[f"Confirmed from flow '{flow_name}' execution context."],
    )


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


def _build_step_evidence_counts(
    completed_steps: list[dict[str, Any]],
) -> dict[str, int]:
    """Derive step evidence counts from completed-step entries."""

    step_counts: dict[str, int] = {}
    for step in completed_steps:
        try:
            step_number = int(step.get("step"))
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
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if not source or not target:
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
        return node_type != "task_input" and agent_id not in ("task_input", "supervisor")

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
        "completed_steps": [],
        "evidence_registry": _EvidenceRegistry(),
        "template_variables": _build_initial_flow_template_variables(flow, user_query),
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
        node_data: dict[str, Any],
    ):
        """Enforce strict flow step ordering at runtime."""

        # Always embed a user-facing specialist label in the wrapper description.
        # Runner-side audit formatting reads tool descriptions to recover custom agent
        # names (ask_ca_<uuid>_specialist) for TOOL_START/TOOL_COMPLETE labels.
        description_override = f"Ask the {specialist_label}"

        @function_tool(name_override=tool_name, description_override=description_override)
        async def _ordered_tool(query: str) -> str:
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
            template_variables = _build_flow_template_variables(
                stored_variables=execution_state["template_variables"],
                document_name=document_name,
                flow_run_id=flow_run_id,
                timestamp=template_timestamp,
            )
            resolved_query = _resolve_flow_step_query(
                input_source=node_data.get("input_source"),
                custom_input=node_data.get("custom_input"),
                default_query=query,
                template_variables=template_variables,
            )
            output_filename_descriptor = _resolve_output_filename_descriptor(
                output_filename_template=node_data.get("output_filename_template"),
                template_variables=template_variables,
            )

            # _create_streaming_tool() returns a FunctionTool (not a plain callable).
            # Invoke via on_invoke_tool() so we execute the underlying specialist wrapper.
            output_filename_token = set_current_output_filename_stem(output_filename_descriptor)
            try:
                if hasattr(tool_callable, "on_invoke_tool"):
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

            result_text = _stringify_tool_output(result)
            output_key = str(node_data.get("output_key") or "").strip()
            if output_key:
                execution_state["template_variables"][output_key] = result_text
            candidate, step_evidence_metadata = (
                build_extraction_envelope_candidate_with_evidence(
                    result,
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
                    },
                )
            )
            step_evidence = _accumulate_step_evidence(
                execution_state["evidence_registry"],
                step_evidence_metadata.get("evidence_records", []),
            )
            execution_state["completed_steps"].append(
                {
                    "step": step_number,
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "tool_name": tool_name,
                    "output": result_text,
                    "output_preview": _truncate_tool_output(result_text),
                    "candidate": candidate,
                    **step_evidence,
                }
            )
            execution_state["next_tool_index"] = next_idx + 1
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
        else:
            try:
                agent = get_agent_by_id(agent_id, **context)
            except Exception as e:
                logger.warning("[Flow Executor] Failed to create agent '%s': %s", agent_id, e)
                unavailable_steps.append({
                    "step": step_num,
                    "agent_id": agent_id,
                    "agent_name": entry.get("name", agent_id),
                    "reason": str(e),
                })
                continue

            custom_instr = data.get("custom_instructions")
            include_evidence = _resolve_flow_step_include_evidence(
                entry=entry,
                raw_include_evidence=data.get("include_evidence"),
            )
            step_instruction_prefix = _build_flow_step_instruction_prefix(
                custom_instructions=custom_instr,
                include_evidence=include_evidence,
            )
            if step_instruction_prefix:
                agent.instructions = step_instruction_prefix + (agent.instructions or "")
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

        ordered_tool_names.append(tool_name)
        streaming_tool = _wrap_with_step_order(
            raw_streaming_tool,
            tool_name=tool_name,
            specialist_label=specialist_name,
            agent_id=agent_id,
            agent_name=entry.get("name", agent_id),
            step_number=step_num,
            node_data=data,
            curation_adapter_key=(
                str(entry.get("curation", {}).get("adapter_key") or "").strip() or None
                if isinstance(entry.get("curation"), dict)
                else None
            ),
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

        # Include tool name reference when agent appears in multiple steps
        if is_duplicate:
            step_desc = f"Step {step_num}: {agent_name} (use tool: {tool_ref})"
        else:
            step_desc = f"Step {step_num}: {agent_name}"
        if step_goal:
            step_desc += f" - {step_goal}"
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
- If a step is unavailable, skip it and continue to the next available step
- Pass relevant context from previous steps to subsequent steps
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
) -> None:
    """Persist flow-produced extraction envelopes and propagate failures."""

    if not candidates or not document_id:
        return

    persist_extraction_results(
        [
            CurationExtractionPersistenceRequest(
                document_id=document_id,
                adapter_key=_resolve_flow_candidate_adapter_key(candidate),
                agent_key=candidate.agent_key,
                source_kind=CurationExtractionSourceKind.FLOW,
                origin_session_id=session_id,
                trace_id=trace_id,
                flow_run_id=flow_run_id,
                user_id=user_id,
                candidate_count=candidate.candidate_count,
                conversation_summary=candidate.conversation_summary,
                payload_json=candidate.payload_json,
                metadata=dict(candidate.metadata),
            )
            for candidate in candidates
        ]
    )


def _persist_flow_extraction_candidates_or_build_error(
    *,
    flow_name: str,
    candidates: List[ExtractionEnvelopeCandidate],
    document_id: Optional[str],
    user_id: str,
    session_id: str,
    trace_id: Optional[str],
    flow_run_id: Optional[str],
) -> tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """Persist flow extraction candidates and return a FLOW_ERROR payload on failure."""

    try:
        _persist_flow_extraction_candidates(
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
        )

    return True, None, None


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
    flow_execution_state = supervisor._flow_execution_state
    completed_steps = flow_execution_state["completed_steps"]
    evidence_registry = flow_execution_state["evidence_registry"]

    async for event in run_agent_streamed(
        user_message=prompt,
        user_id=str(user_id),
        session_id=session_id,
        document_id=document_id,
        document_name=document_name,
        conversation_history=None,  # Flows don't use conversation history
        active_groups=active_groups,
        agent=supervisor,  # Pass the flow supervisor
        doc_context=doc_context,  # Pass pre-fetched context (optimization)
    ):
        event_type = event.get("type")
        event_data = event.get("data", {}) or {}

        if event_type == "RUN_STARTED" and "trace_id" in event_data:
            trace_id = event_data.get("trace_id")

        flow_step_evidence_event: Optional[dict[str, Any]] = None
        if event_type == "TOOL_COMPLETE":
            details = event.get("details", {}) or {}
            tool_name = str(details.get("toolName") or "").strip()
            completed_step = _find_completed_step_by_tool_name(completed_steps, tool_name)
            if completed_step is not None:
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
            persisted, failure_reason, flow_error_event = (
                _persist_flow_extraction_candidates_or_build_error(
                    flow_name=flow.name,
                    candidates=_collect_completed_step_candidates(completed_steps),
                    document_id=document_id,
                    user_id=str(user_id),
                    session_id=session_id,
                    trace_id=trace_id,
                    flow_run_id=flow_run_id,
                )
            )
            if not persisted:
                flow_status = "failed"
                if flow_error_event is not None:
                    yield flow_error_event
                break

            extraction_persisted = True
            yield event
            logger.info(
                "[Flow Executor] %s produced - terminating flow '%s'",
                "Output file" if event_type == "FILE_READY" else "Chat output",
                flow.name,
            )
            break
        yield event
        if flow_step_evidence_event is not None:
            yield flow_step_evidence_event

    if flow_status != "failed" and not extraction_persisted:
        persisted, failure_reason, flow_error_event = (
            _persist_flow_extraction_candidates_or_build_error(
                flow_name=flow.name,
                candidates=_collect_completed_step_candidates(completed_steps),
                document_id=document_id,
                user_id=str(user_id),
                session_id=session_id,
                trace_id=trace_id,
                flow_run_id=flow_run_id,
            )
        )
        if not persisted:
            flow_status = "failed"
            if flow_error_event is not None:
                yield flow_error_event

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
