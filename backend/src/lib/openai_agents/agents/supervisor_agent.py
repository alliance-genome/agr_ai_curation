"""
Supervisor Agent using OpenAI Agents SDK.

This agent coordinates routing to specialized domain agents based on
query intent, using streaming tool wrappers for visibility.

Each specialist agent runs in isolation with its own context window.
Only the specialist's final output returns to the supervisor, preventing
context window explosion from accumulated tool outputs.

STREAMING VISIBILITY:
Unlike as_tool(), our custom streaming wrappers use Runner.run_streamed()
to capture internal tool calls and emit events to the audit panel.

Advanced features used:
- ModelSettings: Per-agent temperature and reasoning configuration
- Reasoning: Extended thinking time for complex routing decisions (GPT-5 models)
- Guardrails: Optional input validation for safety (PII detection, topic relevance)
- Streaming tool wrappers: Specialists run with event capture for audit visibility

DYNAMIC AGENT DISCOVERY:
Specialist agents are discovered from unified `agents` table records where
`visibility='system'` and `supervisor_enabled=true`.
"""

import asyncio
import json
import logging
import re
import time
from typing import Optional, List, Literal, Dict, Any, Callable, Sequence

from agents import Agent, ModelSettings, RunConfig, function_tool

from ..streaming_tools import run_specialist_with_events

# Prompt cache and context tracking imports
from src.lib.context import (
    get_current_session_id,
    get_current_trace_id,
    get_current_user_id,
)
from src.lib.chat_state import document_state
from src.lib.conversation_manager import conversation_manager
from src.lib.curation_workspace import (
    CurationPrepPersistenceContext,
    run_curation_prep,
)
from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
from src.lib.curation_workspace.extraction_results import (
    enrich_extraction_result_scope,
    list_extraction_results,
)
from src.lib.prompts.cache import get_prompt
from src.lib.prompts.context import set_pending_prompts
from src.schemas.curation_prep import CurationPrepScopeConfirmation
from src.schemas.curation_workspace import CurationExtractionSourceKind

# Note: Answer model not used here - supervisor streams plain text for better UX

logger = logging.getLogger(__name__)

# Type alias for reasoning effort levels
ReasoningEffort = Literal["minimal", "low", "medium", "high"]

CURATION_PREP_CONFIRMATION_QUESTION = "Ready to prepare these for curation?"
_CURATION_PREP_TOOL_NAME = "prepare_for_curation"
_SUPERVISOR_BUILTIN_TOOL_NAMES = frozenset({"export_to_file", _CURATION_PREP_TOOL_NAME})
_EXPLICIT_PREP_CONFIRMATION_RE = re.compile(
    r"\b(?:yes|confirm(?:ed)?|i confirm|go ahead|proceed|ready|prepare (?:these|them|it)|please do|do it)\b",
    re.IGNORECASE,
)
_NEGATED_PREP_CONFIRMATION_RE = re.compile(
    r"\b(?:no|not yet|not ready|don't|do not|wait|stop|cancel|hold off)\b",
    re.IGNORECASE,
)


def _tool_response(status: str, message: str, **extra: Any) -> str:
    """Serialize supervisor built-in tool responses consistently."""

    payload = {"status": status, "message": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=True)


def _unique_scope_values(values: Sequence[Optional[str]]) -> list[str]:
    """Return distinct non-empty scope keys in first-seen order."""

    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _normalize_scope_values(values: Sequence[str] | None) -> list[str]:
    """Normalize tool-provided scope values."""

    return _unique_scope_values(list(values or []))


def _latest_assistant_message(session_history: Sequence[Dict[str, Any]]) -> str | None:
    """Return the most recent assistant message stored for the session."""

    for exchange in reversed(session_history):
        assistant_text = str(exchange.get("assistant") or "").strip()
        if assistant_text:
            return assistant_text
    return None


def _assistant_prompted_for_curation_prep(session_history: Sequence[Dict[str, Any]]) -> bool:
    """Return whether the prior assistant turn asked the required prep question."""

    latest_assistant = _latest_assistant_message(session_history)
    if not latest_assistant:
        return False
    return CURATION_PREP_CONFIRMATION_QUESTION.lower() in latest_assistant.lower()


def _is_explicit_curation_prep_confirmation(user_confirmation: str) -> bool:
    """Require an affirmative confirmation and reject negated variants."""

    confirmation_text = str(user_confirmation or "").strip()
    if not confirmation_text:
        return False
    if _NEGATED_PREP_CONFIRMATION_RE.search(confirmation_text):
        return False
    return _EXPLICIT_PREP_CONFIRMATION_RE.search(confirmation_text) is not None


def _available_scope_from_extraction_results(
    extraction_results: Sequence[Any],
) -> dict[str, list[str]]:
    """Summarize the adapter scope currently available in persisted extraction results."""

    return {
        "adapter_keys": _unique_scope_values(
            [getattr(record, "adapter_key", None) for record in extraction_results]
        ),
    }


def _available_document_ids(extraction_results: Sequence[Any]) -> list[str]:
    """Summarize distinct persisted document ids in first-seen order."""

    return _unique_scope_values([getattr(record, "document_id", None) for record in extraction_results])


def _current_chat_document_id(user_id: str) -> str | None:
    """Return the currently loaded chat document for the active user when present."""

    active_document = document_state.get_document(user_id)
    if not isinstance(active_document, dict):
        return None

    document_id = str(active_document.get("id") or "").strip()
    return document_id or None


def _resolve_confirmed_scope(
    extraction_results: Sequence[Any],
    *,
    adapter_keys: Sequence[str] | None,
) -> tuple[dict[str, list[str]] | None, dict[str, list[str]]]:
    """Resolve confirmed adapter scope without legacy profile/domain narrowing."""

    available_scope = _available_scope_from_extraction_results(extraction_results)
    confirmed_scope = {
        "adapter_keys": _normalize_scope_values(adapter_keys),
    }

    if not confirmed_scope["adapter_keys"]:
        return None, available_scope

    if not any(confirmed_scope.values()):
        return None, available_scope

    return confirmed_scope, available_scope


def _record_matches_scope(record: Any, confirmed_scope: dict[str, list[str]]) -> bool:
    """Return whether one persisted extraction record falls within confirmed adapter scope."""

    adapter_key = str(getattr(record, "adapter_key", None) or "").strip()

    if confirmed_scope["adapter_keys"]:
        if not adapter_key or adapter_key not in confirmed_scope["adapter_keys"]:
            return False

    return True


def _filter_extraction_results_for_scope(
    extraction_results: Sequence[Any],
    confirmed_scope: dict[str, list[str]],
) -> tuple[list[Any], list[str]]:
    """Filter persisted extraction results to the explicitly confirmed scope."""

    scoped_results = [
        record for record in extraction_results if _record_matches_scope(record, confirmed_scope)
    ]
    if scoped_results:
        return scoped_results, []

    return [], []


def _resolved_scope_values(
    confirmed_values: Sequence[str],
    extraction_results: Sequence[Any],
    attr_name: str,
) -> list[str]:
    """Combine confirmed scope with persisted record scope in stable order."""

    return _unique_scope_values(
        [
            *confirmed_values,
            *(getattr(record, attr_name, None) for record in extraction_results),
        ]
    )

async def _dispatch_curation_prep_from_chat_context(
    *,
    user_confirmation: str,
    adapter_keys: Sequence[str] | None = None,
    scope_summary: str | None = None,
) -> str:
    """Run curation prep from the current chat session when confirmation is valid."""

    session_id = get_current_session_id()
    user_id = get_current_user_id()
    if not session_id or not user_id:
        return _tool_response(
            "unavailable",
            "Curation prep is only available inside an active chat session.",
        )

    session_history = list(conversation_manager.get_session_history(user_id, session_id))
    if not _assistant_prompted_for_curation_prep(session_history):
        return _tool_response(
            "confirmation_required",
            (
                f'Ask the curator "{CURATION_PREP_CONFIRMATION_QUESTION}" and wait for an explicit '
                "confirmation in the next turn before calling this tool."
            ),
        )

    if not _is_explicit_curation_prep_confirmation(user_confirmation):
        return _tool_response(
            "confirmation_required",
            "The curator has not explicitly confirmed the prep scope yet.",
        )

    active_document_id = _current_chat_document_id(user_id)
    extraction_results = list_extraction_results(
        origin_session_id=session_id,
        user_id=user_id,
        source_kind=CurationExtractionSourceKind.CHAT,
        document_id=active_document_id,
        exclude_agent_keys=(CURATION_PREP_AGENT_ID,),
    )
    extraction_results = [
        enrich_extraction_result_scope(record)
        for record in extraction_results
    ]
    if not extraction_results:
        return _tool_response(
            "no_extraction_context",
            (
                "No persisted chat extraction results are available for the currently loaded "
                "document yet."
                if active_document_id
                else "No persisted chat extraction results are available to prepare yet."
            ),
        )

    available_document_ids = _available_document_ids(extraction_results)
    if active_document_id is None and len(available_document_ids) > 1:
        return _tool_response(
            "scope_confirmation_required",
            (
                "This chat session includes findings from multiple documents. Load the document "
                "you want to prepare, then confirm again so only that document's findings are "
                "prepared."
            ),
            available_document_ids=available_document_ids,
        )

    confirmed_scope, available_scope = _resolve_confirmed_scope(
        extraction_results,
        adapter_keys=adapter_keys,
    )
    if confirmed_scope is None:
        return _tool_response(
            "scope_confirmation_required",
            "The confirmed scope is still ambiguous. Ask the curator to confirm which findings to prepare instead of sweeping everything into curation.",
            available_scope=available_scope,
        )

    scoped_extraction_results, scope_resolution_notes = _filter_extraction_results_for_scope(
        extraction_results,
        confirmed_scope,
    )
    if not scoped_extraction_results:
        return _tool_response(
            "scope_confirmation_required",
            "The confirmed scope did not match any persisted extraction results in this chat session.",
            available_scope=available_scope,
        )

    resolved_adapter_keys = _resolved_scope_values(
        confirmed_scope["adapter_keys"],
        scoped_extraction_results,
        "adapter_key",
    )
    if not resolved_adapter_keys:
        return _tool_response(
            "scope_confirmation_required",
            "The persisted extraction context is missing adapter ownership, so curation prep cannot safely run yet.",
            available_scope=available_scope,
        )

    scope_confirmation = CurationPrepScopeConfirmation(
        confirmed=True,
        adapter_keys=resolved_adapter_keys,
        notes=_unique_scope_values(
            [
                *scope_resolution_notes,
                f"Confirmed from chat session {session_id}.",
                f"Prep requested by user {user_id}.",
                (f"Supervisor scope summary: {scope_summary}" if scope_summary else None),
                (f"Curator confirmation: {user_confirmation}" if user_confirmation else None),
            ]
        ),
    )

    try:
        prep_output = await run_curation_prep(
            scoped_extraction_results,
            scope_confirmation=scope_confirmation,
            persistence_context=CurationPrepPersistenceContext(
                document_id=(
                    active_document_id
                    or (scoped_extraction_results[0].document_id if scoped_extraction_results else None)
                ),
                source_kind=CurationExtractionSourceKind.CHAT,
                origin_session_id=session_id,
                trace_id=get_current_trace_id(),
                user_id=user_id,
            ),
        )
    except ValueError as exc:
        return _tool_response("unable_to_prepare", str(exc))

    candidate_count = len(prep_output.candidates)
    return _tool_response(
        "prepared",
        (
            f"Prepared {candidate_count} candidate annotation"
            f"{'s' if candidate_count != 1 else ''} for curation review."
        ),
        candidate_count=candidate_count,
        document_id=scoped_extraction_results[0].document_id,
        adapter_keys=resolved_adapter_keys,
        warnings=list(prep_output.run_metadata.warnings),
        processing_notes=list(prep_output.run_metadata.processing_notes),
    )


def _fetch_document_sections_sync(document_id: str, user_id: str) -> List[Dict[str, Any]]:
    """
    Synchronously fetch document sections for injection into the PDF agent prompt.

    This wrapper handles the async get_document_sections function in a sync context.
    """
    from src.lib.weaviate_client.chunks import get_document_sections

    try:
        # Try to get the running loop
        try:
            asyncio.get_running_loop()
            # If there's a running loop, we can't use asyncio.run()
            # Create a new event loop in a thread or use run_coroutine_threadsafe
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, get_document_sections(document_id, user_id))
                return future.result(timeout=10)
        except RuntimeError:
            # No running loop, safe to use asyncio.run()
            return asyncio.run(get_document_sections(document_id, user_id))
    except Exception as e:
        logger.warning("Failed to fetch document sections: %s", e)
        return []


def fetch_document_hierarchy_sync(document_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Synchronously fetch hierarchical document structure for injection into PDF agent prompt.

    Returns the LLM-resolved hierarchy with top-level sections and subsections.
    This wrapper handles the async get_document_sections_hierarchical in a sync context.

    This is a public function, exported for use by runner.py.
    """
    from src.lib.weaviate_client.chunks import get_document_sections_hierarchical

    try:
        # Try to get the running loop
        try:
            asyncio.get_running_loop()
            # If there's a running loop, we can't use asyncio.run()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, get_document_sections_hierarchical(document_id, user_id))
                return future.result(timeout=10)
        except RuntimeError:
            # No running loop, safe to use asyncio.run()
            return asyncio.run(get_document_sections_hierarchical(document_id, user_id))
    except Exception as e:
        logger.warning("Failed to fetch document hierarchy: %s", e)
        return None


# Import guardrails (optional - won't break if module has issues)
try:
    from ..guardrails import safety_guardrail, biology_topic_guardrail
    GUARDRAILS_AVAILABLE = True
except ImportError:
    GUARDRAILS_AVAILABLE = False
    safety_guardrail = None
    biology_topic_guardrail = None


def _create_streaming_tool(
    agent: Agent,
    tool_name: str,
    tool_description: str,
    specialist_name: str,
    run_config: Optional[RunConfig] = None,
) -> Callable:
    """
    Create a streaming tool wrapper for a specialist agent.

    Unlike as_tool(), this wrapper uses run_specialist_with_events() to capture
    internal tool calls and emit events to the audit panel.

    Args:
        agent: The specialist agent to wrap
        tool_name: The tool name (e.g., "ask_pdf_specialist")
        tool_description: Description for the LLM
        specialist_name: Human-readable name for audit events
        run_config: Optional run configuration

    Returns:
        A function_tool decorated async function
    """
    @function_tool(name_override=tool_name, description_override=tool_description)
    async def streaming_tool_wrapper(query: str) -> str:
        """Ask the specialist a question and get a response."""
        return await run_specialist_with_events(
            agent=agent,
            input_text=query,
            specialist_name=specialist_name,
            run_config=run_config,
            tool_name=tool_name,  # Pass tool_name for batching nudge tracking
        )

    return streaming_tool_wrapper


def _build_model_settings(
    model: str,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[ReasoningEffort] = None,
    provider_override: Optional[str] = None,
) -> Optional[ModelSettings]:
    """
    Build ModelSettings with optional reasoning for models that support it.

    Reasoning is supported on:
    - GPT-5 family models (gpt-5, gpt-5-mini)
    - Gemini 3 models (gemini-3.0-pro) - uses "low"/"high" thinking levels
    - Gemini 2.5 models (gemini-2.5-pro, gemini-2.5-flash) - uses thinking budgets

    IMPORTANT: GPT-5 models don't support the temperature parameter -
    they use reasoning instead. Gemini models support both.

    For Gemini, the OpenAI SDK's reasoning_effort parameter maps to:
    - minimal/low -> "low" thinking level (Gemini 3) or 1,024 budget (Gemini 2.5)
    - medium -> "high" thinking level (Gemini 3) or 8,192 budget (Gemini 2.5)
    - high -> "high" thinking level (Gemini 3) or 24,576 budget (Gemini 2.5)

    Args:
        model: The model name (e.g., "gpt-5", "gpt-4o", "gemini-3.0-pro")
        temperature: Optional temperature override (0.0-1.0)
        reasoning_effort: Optional reasoning effort for models that support it

    Returns:
        ModelSettings instance or None if no settings needed
    """
    from ..config import build_model_settings

    # Delegate to shared builder so provider-specific safeguards (e.g., Groq
    # tool-call stability controls) stay consistent across all agent surfaces.
    return build_model_settings(
        model=model,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        provider_override=provider_override,
    )


def get_supervisor_agent_tools() -> List[str]:
    """
    Get list of tool names for supervisor-enabled system agents.
    """
    tools = _get_supervisor_specialist_specs()
    return [t["tool_name"] for t in tools]


def get_supervisor_tool_agent_map() -> Dict[str, str]:
    """Return the runtime mapping from supervisor tool names to agent keys."""

    return {
        str(spec["tool_name"]): str(spec["agent_key"])
        for spec in _get_supervisor_specialist_specs()
        if spec.get("tool_name") and spec.get("agent_key")
    }


def generate_routing_table() -> str:
    """
    Build supervisor routing table from unified agent records.

    Returns markdown table with tool names and descriptions.
    """
    tools = _get_supervisor_specialist_specs()

    rows = ["| Tool | When to Use |", "|------|-------------|"]

    for tool in tools:
        tool_name = tool["tool_name"]
        description = tool["description"]
        if tool_name and description:
            rows.append(f"| {tool_name} | {description} |")

    return "\n".join(rows)


def _get_supervisor_specialist_specs() -> List[Dict[str, Any]]:
    """Load supervisor-enabled system agents from unified DB records."""
    from src.models.sql.agent import Agent as AgentRecord
    from src.models.sql.database import SessionLocal
    from src.lib.agent_studio.catalog_service import get_agent_metadata

    db = SessionLocal()
    try:
        rows = db.query(AgentRecord).filter(
            AgentRecord.visibility == "system",
            AgentRecord.is_active == True,  # noqa: E712
            AgentRecord.supervisor_enabled == True,  # noqa: E712
        ).order_by(AgentRecord.agent_key.asc()).all()
    finally:
        db.close()

    specs: List[Dict[str, Any]] = []
    for row in rows:
        try:
            metadata = get_agent_metadata(row.agent_key)
            requires_document = bool(metadata.get("requires_document", False))
        except Exception:
            logger.exception(
                "Failed to resolve metadata for supervisor specialist '%s'",
                row.agent_key,
            )
            continue

        specs.append(
            {
                "agent_key": row.agent_key,
                "name": row.name,
                "description": row.supervisor_description or row.description or f"Ask {row.name}",
                "tool_name": f"ask_{row.agent_key.replace('-', '_')}_specialist",
                "requires_document": requires_document,
                "group_rules_enabled": bool(row.group_rules_enabled),
                "batchable": bool(row.supervisor_batchable),
                "batching_entity": row.supervisor_batching_entity,
            }
        )

    return specs


def _build_runtime_tool_availability_note(
    tool_specs: List[Dict[str, Any]],
    available_specialist_tools: List[Callable],
    document_loaded: bool,
) -> str:
    """Describe the specialist/tool runtime state for the current chat."""
    available_tool_names = [
        tool_name
        for tool_name in (
            str(getattr(tool, "name", "") or "").strip()
            for tool in available_specialist_tools
        )
        if tool_name and tool_name not in _SUPERVISOR_BUILTIN_TOOL_NAMES
    ]
    document_tool_names = sorted(
        {
            str(spec.get("tool_name", "") or "").strip()
            for spec in tool_specs
            if spec.get("requires_document") and spec.get("tool_name")
        }
    )
    available_document_tools = [
        tool_name for tool_name in available_tool_names if tool_name in document_tool_names
    ]

    notes: List[str] = []

    if available_tool_names:
        notes.append(
            "RUNTIME TOOL AVAILABILITY: Only these specialist tools are currently "
            "installed and callable in this environment: "
            f"{', '.join(available_tool_names)}. Do not mention or attempt any "
            "other specialist tools."
        )
        notes.append(
            "RUNTIME TOOL DESCRIPTIONS ARE AUTHORITATIVE: If any static prompt "
            "example differs from the live tool names or tool descriptions, "
            "follow the live tool names and tool descriptions."
        )
    else:
        notes.append(
            "CORE-ONLY MODE: No domain specialist tools are currently installed. "
            "Treat this as a minimal general-purpose chat runtime and answer "
            "general questions directly. If the user asks for Alliance-specific "
            "database lookups, document extraction, annotation workflows, or other "
            "specialist tasks, explain briefly that those specialist tools are not "
            "installed in this environment."
        )

    if document_loaded and available_document_tools:
        notes.append(
            "DOCUMENT CONTEXT: A PDF document is loaded. For document-based requests, "
            "use these document-aware specialist tools: "
            f"{', '.join(available_document_tools)}."
        )
    elif not document_loaded and document_tool_names:
        notes.append(
            "No PDF document is currently loaded, so these document-dependent tools "
            "are unavailable in this chat: "
            f"{', '.join(document_tool_names)}."
        )

    notes.append(
        "CURATION PREP HANDOFF: Use prepare_for_curation only after you ask exactly "
        f'"{CURATION_PREP_CONFIRMATION_QUESTION}" and the next user turn explicitly '
        "confirms the scope. Never auto-trigger curation prep."
    )

    notes.append(
        "Use export_to_file only when the user explicitly asks to export or "
        "download results."
    )

    return "\n\n".join(notes)


def _create_dynamic_specialist_tools(
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    document_name: Optional[str] = None,
    sections: Optional[List[str]] = None,
    hierarchy: Optional[Dict[str, Any]] = None,
    abstract: Optional[str] = None,
    active_groups: Optional[List[str]] = None,
    tool_specs: Optional[List[Dict[str, Any]]] = None,
) -> List[Callable]:
    """
    Dynamically create specialist tools based on unified agent records.

    Args:
        document_id: UUID of loaded document (for document-dependent agents)
        user_id: User ID for tenant isolation (for document-dependent agents)
        document_name: Name of the document for context
        sections: Flat list of section names from document
        hierarchy: Hierarchical document structure
        abstract: Paper abstract for context injection
        active_groups: Group IDs for rule injection (e.g., ["MGI", "FB"])

    Returns:
        List of function_tool decorated callables
    """
    from src.lib.agent_studio.catalog_service import get_agent_by_id

    tools_metadata = tool_specs if tool_specs is not None else _get_supervisor_specialist_specs()
    specialist_tools = []

    for tool_meta in tools_metadata:
        tool_name = tool_meta["tool_name"]
        agent_key = tool_meta["agent_key"]
        description = tool_meta["description"]
        requires_document = tool_meta.get("requires_document", False)
        group_rules_enabled = tool_meta.get("group_rules_enabled", False)

        # Skip document-dependent agents if no document is loaded
        if requires_document and (not document_id or not user_id):
            logger.debug("Skipping %s - requires document but none loaded", tool_name)
            continue

        # Build runtime kwargs for unified agent builder
        agent_kwargs: Dict[str, Any] = {}
        if requires_document:
            agent_kwargs.update({
                "document_id": document_id,
                "user_id": user_id,
                "document_name": document_name,
                "sections": sections,
                "hierarchy": hierarchy,
                "abstract": abstract,
            })

        # Group-aware agents (MODs, institutions, teams, etc.)
        if group_rules_enabled and active_groups:
            agent_kwargs["active_groups"] = active_groups

        try:
            # Create the agent instance from unified spec.
            agent = get_agent_by_id(agent_key, **agent_kwargs)

            specialist_name = (
                str(tool_meta.get("name") or agent.name or agent_key)
                .replace(" Agent", "")
                .replace(" Validation", "")
            )

            streaming_tool = _create_streaming_tool(
                agent=agent,
                tool_name=tool_name,
                tool_description=description,
                specialist_name=specialist_name,
            )
            specialist_tools.append(streaming_tool)

            logger.info("Created dynamic tool: %s", tool_name)

        except Exception as e:
            logger.error("Failed to create tool %s for %s: %s", tool_name, agent_key, e)
            continue

    # Warn if no specialist tools were created
    if not specialist_tools:
        logger.warning("No specialist tools created - supervisor may have limited functionality")

    return specialist_tools


def create_supervisor_agent(
    document_id: Optional[str] = None,
    user_id: Optional[str] = None,
    document_name: Optional[str] = None,
    hierarchy: Optional[Dict[str, Any]] = None,
    abstract: Optional[str] = None,
    enable_guardrails: bool = False,  # Enable input guardrails (PII detection, topic check)
    active_groups: Optional[List[str]] = None,  # Group-specific rules to inject (e.g., ["MGI", "FB"])
) -> Agent:
    """
    Create a Supervisor agent with dynamically discovered specialist tools.

    DYNAMIC AGENT DISCOVERY:
    Specialist tools are discovered from unified `agents` table records where
    `visibility='system'` and `supervisor_enabled=true`.
    Document-dependent agents are filtered out if no document is loaded.

    Each specialist runs in isolation with its own context window.
    Only the specialist's final output returns to the supervisor, preventing
    context window explosion from accumulated tool outputs.

    All agent settings (model, temperature, reasoning) are configured via environment
    variables. See config.py for available settings.

    Built-in Tools (always available):
    - export_to_file: Export data to CSV, TSV, or JSON files

    Args:
        document_id: The UUID of the PDF document (for document-dependent specialists)
        user_id: The user's user ID for tenant isolation (for document-dependent specialists)
        document_name: Optional name of the document for context
        hierarchy: Optional pre-fetched document hierarchy (avoids duplicate fetch)
        abstract: Optional pre-fetched paper abstract (injected into specialist prompts)
        enable_guardrails: Enable input guardrails for safety (default: False)
        active_groups: Optional list of group IDs to inject rules for (e.g., ["MGI", "FB"]).
                       Passed to agents with group_rules_enabled=True for group-specific behavior.

    Returns:
        An Agent instance configured as a supervisor with specialist tools
    """
    from ..config import (
        get_agent_config,
        log_agent_config,
        get_model_for_agent,
        resolve_model_provider,
    )
    route_start = time.monotonic()

    # Get supervisor config from registry + environment
    config = get_agent_config("supervisor")
    log_agent_config("Supervisor", config)

    model_provider = resolve_model_provider(config.model)

    # Get the model (returns LitellmModel for Gemini/Groq, string for OpenAI)
    model = get_model_for_agent(config.model, provider_override=model_provider)

    # Build model settings for supervisor
    supervisor_settings = _build_model_settings(
        model=config.model,
        temperature=config.temperature,
        reasoning_effort=config.reasoning,
        provider_override=model_provider,
    )

    # Configure guardrails if enabled
    input_guardrails = []
    if enable_guardrails and GUARDRAILS_AVAILABLE:
        if safety_guardrail:
            input_guardrails.append(safety_guardrail)
        else:
            logger.warning("Guardrails requested but not available")
    elif enable_guardrails:
        logger.warning("Guardrails requested but module not imported")

    logger.info(
        "Creating Supervisor agent with dynamic tool discovery, model=%s temp=%s reasoning=%s",
        config.model,
        config.temperature,
        config.reasoning,
        extra={"operation": "supervisor_routing_setup"},
    )

    # Extract section names from hierarchy for document-dependent agents
    sections = []
    if hierarchy and hierarchy.get("sections"):
        sections = [s.get("name") for s in hierarchy.get("sections", []) if s.get("name")]
        logger.info("Extracted %s sections from pre-fetched hierarchy", len(sections))

    # =========================================================================
    # DYNAMIC SPECIALIST TOOL CREATION
    # =========================================================================
    # Discover enabled agents from unified records and create streaming tool wrappers.
    # Document-dependent agents are automatically filtered if no document is loaded.
    # Group-specific rules are injected for agents with group_rules_enabled=True.
    # =========================================================================
    tool_specs = _get_supervisor_specialist_specs()
    specialist_tools = _create_dynamic_specialist_tools(
        document_id=document_id,
        user_id=user_id,
        document_name=document_name,
        sections=sections,
        hierarchy=hierarchy,
        abstract=abstract,
        active_groups=active_groups,
        tool_specs=tool_specs,
    )

    routing_duration_ms = (time.monotonic() - route_start) * 1000
    logger.info(
        "Dynamic discovery created %s specialist tools",
        len(specialist_tools),
        extra={
            "operation": "supervisor_routing_setup",
            "specialist_tool_count": len(specialist_tools),
            "duration_ms": round(routing_duration_ms, 1),
        },
    )

    @function_tool(
        name_override=_CURATION_PREP_TOOL_NAME,
        description_override=(
            "Prepare the confirmed chat extraction context for curation workspace follow-up. "
            f'Use only after you already asked "{CURATION_PREP_CONFIRMATION_QUESTION}" and the curator '
            "explicitly confirmed in a later turn. Pass the curator's confirmation text verbatim in "
            "`user_confirmation`. Include confirmed adapter_keys when they are clear from the "
            "conversation. Do not call this tool to ask for confirmation."
        ),
    )
    async def prepare_for_curation_tool(
        user_confirmation: str,
        adapter_keys: List[str] | None = None,
        scope_summary: str = "",
    ) -> str:
        """Invoke the curation prep agent after explicit curator confirmation."""

        return await _dispatch_curation_prep_from_chat_context(
            user_confirmation=user_confirmation,
            adapter_keys=adapter_keys,
            scope_summary=scope_summary,
        )

    specialist_tools.append(prepare_for_curation_tool)

    # Export to File tool (always available - supervisor built-in, not a specialist agent)
    # Allows supervisor to export data as downloadable CSV, TSV, or JSON files
    @function_tool(
        name_override="export_to_file",
        description_override="""Export data to a downloadable file. Use when user asks to:
- Export, download, or save data as CSV, TSV, or JSON
- Get a spreadsheet or file version of results
- "Give me this as CSV", "TSV format please", "Download as JSON"

Supported formats: csv, tsv, json

The tool returns file information including a download URL that will render as a download button in the chat."""
    )
    async def export_to_file_tool(
        format_type: str,
        data: str,
        filename_hint: str = "export"
    ) -> str:
        """
        Export data to a downloadable file.

        Args:
            format_type: "csv", "tsv", or "json"
            data: The data to export as JSON string.
                  For CSV/TSV: JSON array of objects (e.g., '[{"gene": "BRCA1", "id": "123"}]')
                  For JSON: Any valid JSON structure
            filename_hint: Suggested filename without extension (e.g., "gene_results")

        Returns:
            JSON string with file information including download_url
        """
        import json as json_module
        from ..tools.file_output_tools import (
            _save_csv_impl,
            _save_tsv_impl,
            _save_json_impl,
        )

        format_type_lower = format_type.lower().strip()

        try:
            if format_type_lower == "csv":
                result = await _save_csv_impl(data, filename_hint)
            elif format_type_lower == "tsv":
                result = await _save_tsv_impl(data, filename_hint)
            elif format_type_lower == "json":
                result = await _save_json_impl(data, filename_hint)
            else:
                return json_module.dumps({
                    "error": f"Unsupported format: {format_type}. Supported formats: csv, tsv, json"
                })

            # Return the file info as JSON string
            return json_module.dumps(result)

        except ValueError as e:
            logger.error("export_to_file validation error: %s", e)
            return json_module.dumps({"error": str(e)})
        except Exception as e:
            logger.error("export_to_file error generating file: %s", e)
            return json_module.dumps({"error": f"Failed to generate file: {str(e)}"})

    specialist_tools.append(export_to_file_tool)

    # Get base prompt from cache (zero DB queries at runtime)
    base_prompt = get_prompt("supervisor")
    prompts_used = [base_prompt]

    # Build instructions from cached prompt
    instructions = base_prompt.content

    instructions += (
        "\n\n"
        "CURATION PREP RULES:\n"
        f'- If the curator wants to move findings into curation prep, first ask exactly "{CURATION_PREP_CONFIRMATION_QUESTION}"\n'
        "- Do not call prepare_for_curation in the same turn as the confirmation question.\n"
        "- Only call prepare_for_curation after the next user turn explicitly confirms the scope.\n"
        "- When you call prepare_for_curation, pass the user's confirmation text verbatim and include confirmed scope keys when you know them.\n"
        "- If scope is still ambiguous, ask a follow-up clarification question instead of preparing everything."
    )

    instructions += "\n\n" + _build_runtime_tool_availability_note(
        tool_specs=tool_specs,
        available_specialist_tools=specialist_tools,
        document_loaded=bool(document_id and user_id),
    )

    # Inject group-specific rules for supervisor dispatch behavior
    if active_groups:
        try:
            from ...group_rules import inject_group_rules

            instructions = inject_group_rules(
                base_prompt=instructions,
                group_ids=active_groups,
                component_type="agents",
                component_name="supervisor",
                prompts_out=prompts_used,  # Collect group prompts for tracking
            )
            logger.info("Supervisor configured with group-specific dispatch rules: %s", active_groups)
        except ImportError as e:
            logger.warning("Could not import group config for supervisor, skipping injection: %s", e)
        except Exception as e:
            # Don't fail if supervisor rules don't exist - they're optional
            logger.debug("No supervisor group rules found or error: %s", e)

    logger.info(
        "Creating Supervisor agent, model=%s prompt_v=%s groups=%s",
        config.model,
        base_prompt.version,
        active_groups,
    )

    # Create the supervisor with specialist tools
    # Note: We don't use output_type=Answer here to preserve streaming text
    # (structured output generates JSON tokens which don't stream nicely)
    # Note: 'model' variable was set earlier via get_model_for_agent()
    # For Gemini: returns LitellmModel (handles thought_signature)
    # For OpenAI: returns model name string
    supervisor = Agent(
        name="Query Supervisor",
        instructions=instructions,
        model=model,  # LitellmModel for Gemini, string for OpenAI
        model_settings=supervisor_settings,
        input_guardrails=input_guardrails,
        tools=specialist_tools,
    )

    # Register prompts for execution logging (committed when agent actually runs)
    set_pending_prompts(supervisor.name, prompts_used)

    # Log supervisor configuration to Langfuse for trace visibility
    from ..langfuse_client import log_agent_config as log_agent_config_to_langfuse
    tool_names = [getattr(t, 'name', str(t)) for t in specialist_tools]
    log_agent_config_to_langfuse(
        agent_name="Query Supervisor",
        instructions=instructions,
        model=config.model,
        tools=tool_names,
        model_settings={
            "temperature": config.temperature,
            "reasoning": config.reasoning,
            "prompt_version": base_prompt.version,
        },
        metadata={
            "document_id": document_id,
            "user_id": user_id,
            "specialist_count": len(specialist_tools)
        }
    )

    logger.info("Supervisor configured with %s specialist tools", len(specialist_tools))

    return supervisor
