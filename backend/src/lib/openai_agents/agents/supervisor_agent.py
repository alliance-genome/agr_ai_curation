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
from typing import Awaitable, Optional, List, Literal, Dict, Any, Callable, Sequence

from agents import Agent, ModelSettings, RunConfig, RunContextWrapper, function_tool

from ..streaming_tools import (
    SupervisorExtractionHandoff,
    pop_last_supervisor_extraction_handoff,
    run_specialist_with_events,
)

# Prompt cache and context tracking imports
from src.lib.context import (
    get_current_session_id,
    get_current_trace_id,
    get_current_user_id,
)
from src.lib.chat_state import document_state
from src.lib.chat_transcript import latest_assistant_message_for_session
from src.lib.curation_workspace import (
    CurationPrepPersistenceContext,
    run_curation_prep,
)
from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
from src.lib.curation_workspace.extraction_results import (
    list_extraction_results,
)
from src.lib.openai_agents.inspect_results import inspect_results
from src.lib.openai_agents.supervisor_context_tools import (
    inspect_chat_traces,
)
from src.lib.prompts.assembly import build_agent_prompt_layers, prompt_templates_for_bundle
from src.lib.prompts.context import bind_prompt_run, set_pending_prompts
from src.schemas.curation_prep import CurationPrepScopeConfirmation
from src.schemas.curation_workspace import CurationExtractionSourceKind

# Note: Answer model not used here - supervisor streams plain text for better UX

logger = logging.getLogger(__name__)

# Type alias for reasoning effort levels
ReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh"]

CURATION_PREP_CONFIRMATION_QUESTION = "Ready to prepare these for curation?"
_CURATION_PREP_TOOL_NAME = "prepare_for_curation"
_INSPECT_RESULTS_TOOL_NAME = "inspect_results"
_INSPECT_CHAT_TRACES_TOOL_NAME = "inspect_chat_traces"
_SUPERVISOR_BUILTIN_TOOL_NAMES = frozenset(
    {
        "export_to_file",
        _CURATION_PREP_TOOL_NAME,
        _INSPECT_RESULTS_TOOL_NAME,
        _INSPECT_CHAT_TRACES_TOOL_NAME,
    }
)
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


def _assistant_prompted_for_curation_prep(latest_assistant: str | None) -> bool:
    """Return whether the prior assistant turn asked the required prep question."""

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

    latest_assistant_message = latest_assistant_message_for_session(
        session_id=session_id,
        user_id=user_id,
    )
    if not _assistant_prompted_for_curation_prep(latest_assistant_message):
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

    candidate_count = prep_output.review_row_count
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


# Plain-language terminal instructions returned to the supervisor model when the
# no-progress brake fires. These mirror the strict step-order guard that flows
# enforce via flows/, applied to standard chat (which has no such guard).
_LEDGER_REPLAY_INSTRUCTION = (
    "You already asked this and received the result below. Report it to the user; "
    "do not call this specialist with the same request again."
)
_LEDGER_REPLAY_RESULT_GUIDANCE = (
    "This repeated request already produced {status_phrase} at {result_ref}. "
    "For summary, list, confidence, or detail follow-ups, answer from the manifest "
    "below or use inspect_results(result_ref=\"{result_ref}\", action=\"summary\" "
    "or \"objects\"). Rerun a specialist only when the curator changes scope or "
    "asks for a broader/narrower extraction."
)
_LEDGER_REPLAY_EMPTY_RESULT_GUIDANCE = (
    "This repeated request already produced an empty extraction result at "
    "{result_ref}. Report the empty result, ask for clarification, or make one "
    "materially different retry if the curator's intended scope is clear."
)
_LEDGER_BUDGET_EXCEEDED_MESSAGE = (
    "You have made enough specialist lookups for this turn. Stop and summarize what "
    "you have for the user, including anything that could not be resolved."
)


def _normalize_ledger_query(query: str) -> str:
    """Collapse whitespace and lowercase a query for stable dedup keying."""

    return " ".join(str(query or "").split()).strip().lower()


class SupervisorCallLedger:
    """Per-chat-turn no-progress brake for supervisor specialist tool calls.

    One ledger is created per ``create_supervisor_agent`` call and shared (via
    closure) across that turn's specialist tools. It provides three protections,
    keyed on ``(tool_name, normalized_query)``:

    1. Concurrent collapse: identical concurrent calls share a single underlying
       run via a per-key ``asyncio.Future``; different queries still run in
       parallel and are never serialized.
    2. Sequential short-circuit: once a key has a cached result, later identical
       calls return the cached text plus a terminal instruction instead of
       re-running.
    3. Invocation budget: a generous per-turn total cap and per-specialist cap
       backstop true runaways without truncating legitimate multi-lookup chats.

    Flow supervisors bypass ``create_supervisor_agent`` entirely, so they never
    receive a ledger and are completely unaffected.
    """

    def __init__(
        self,
        *,
        max_total_calls: int,
        max_calls_per_tool: int,
    ) -> None:
        self._lock = asyncio.Lock()
        self._futures: Dict[tuple[str, str], "asyncio.Future[str]"] = {}
        self._max_total_calls = max_total_calls
        self._max_calls_per_tool = max_calls_per_tool
        self._extraction_handoffs: Dict[
            tuple[str, str], SupervisorExtractionHandoff
        ] = {}
        self._extraction_handoff_order: List[tuple[str, str]] = []
        # Distinct underlying invocations actually started this turn (cache hits
        # and concurrent-collapse awaiters do not count toward the budget).
        self._total_invocations = 0
        self._per_tool_invocations: Dict[str, int] = {}

    def record_extraction_handoff(
        self,
        tool_name: str,
        query: str,
        handoff: SupervisorExtractionHandoff,
    ) -> None:
        """Remember a structured extraction result produced during this turn."""

        if not handoff.result_ref:
            return
        key = (tool_name, _normalize_ledger_query(query))
        if key not in self._extraction_handoffs:
            self._extraction_handoff_order.append(key)
        self._extraction_handoffs[key] = handoff

    def latest_extraction_handoffs(self) -> List[SupervisorExtractionHandoff]:
        """Return same-turn extraction result refs in production order."""

        return [
            self._extraction_handoffs[key]
            for key in self._extraction_handoff_order
            if key in self._extraction_handoffs
        ]

    async def run_or_replay(
        self,
        tool_name: str,
        query: str,
        runner_coro_factory: Callable[[], Awaitable[str]],
    ) -> str:
        """Run the specialist once per key, replaying cached/concurrent results.

        ``runner_coro_factory`` is a zero-arg callable returning the awaitable
        that performs the real specialist run. It is invoked at most once per
        distinct key, and only when budget allows.
        """

        key = (tool_name, _normalize_ledger_query(query))

        async with self._lock:
            existing = self._futures.get(key)
            if existing is not None:
                # Either an in-flight identical concurrent call, or a completed
                # cached result. Await it outside the lock below.
                future = existing
                is_owner = False
            else:
                budget_message = self._budget_block_message_locked(tool_name)
                if budget_message is not None:
                    return budget_message
                future = asyncio.get_running_loop().create_future()
                self._futures[key] = future
                self._total_invocations += 1
                self._per_tool_invocations[tool_name] = (
                    self._per_tool_invocations.get(tool_name, 0) + 1
                )
                is_owner = True

        if not is_owner:
            # Concurrent identical caller or sequential repeat: replay the same
            # result. Done futures resolve immediately; in-flight ones await the
            # owner's run. Either way the underlying specialist runs only once.
            was_done = future.done()
            result = await future
            return self._with_replay_instruction(key, result) if was_done else result

        try:
            result = await runner_coro_factory()
        except Exception as exc:
            # Propagate to any concurrent awaiters, then clear the key so a later
            # legitimate retry can proceed. This is correct future bookkeeping,
            # not a fallback.
            async with self._lock:
                self._futures.pop(key, None)
                self._total_invocations = max(0, self._total_invocations - 1)
                self._per_tool_invocations[tool_name] = max(
                    0, self._per_tool_invocations.get(tool_name, 0) - 1
                )
            if not future.done():
                future.set_exception(exc)
            # Mark the exception retrieved so a lone owner (no concurrent awaiter
            # grabbed this future before it was popped) does not trip asyncio's
            # "Future exception was never retrieved" log noise. Concurrent awaiters
            # still receive the exception when they await the future.
            future.exception()
            raise

        if not future.done():
            future.set_result(result)
        return result

    def _budget_block_message_locked(self, tool_name: str) -> Optional[str]:
        """Return the budget message when a new invocation would exceed a cap.

        Must be called while holding ``self._lock``.
        """

        if self._total_invocations >= self._max_total_calls:
            return _LEDGER_BUDGET_EXCEEDED_MESSAGE
        if self._per_tool_invocations.get(tool_name, 0) >= self._max_calls_per_tool:
            return _LEDGER_BUDGET_EXCEEDED_MESSAGE
        return None

    def _with_replay_instruction(self, key: tuple[str, str], result: str) -> str:
        """Wrap a cached result with the plain-language terminal instruction."""

        handoff = self._extraction_handoffs.get(key)
        if handoff is None:
            return f"{_LEDGER_REPLAY_INSTRUCTION}\n\n{result}"
        return (
            f"{_LEDGER_REPLAY_INSTRUCTION}\n"
            f"{_ledger_extraction_replay_guidance(handoff)}\n\n"
            f"{result}"
        )


def _ledger_extraction_replay_guidance(
    handoff: SupervisorExtractionHandoff,
) -> str:
    """Build non-blocking same-turn guidance for cached extraction replays."""

    if handoff.result_status == "empty_extraction":
        return _LEDGER_REPLAY_EMPTY_RESULT_GUIDANCE.format(
            result_ref=handoff.result_ref
        )
    status_phrase = (
        f"{handoff.object_count} retained object"
        if handoff.object_count == 1
        else f"{handoff.object_count} retained objects"
    )
    return _LEDGER_REPLAY_RESULT_GUIDANCE.format(
        result_ref=handoff.result_ref,
        status_phrase=status_phrase,
    )


def _create_streaming_tool(
    agent: Agent,
    tool_name: str,
    tool_description: str,
    specialist_name: str,
    run_config: Optional[RunConfig] = None,
    ledger: Optional[SupervisorCallLedger] = None,
) -> Callable:
    """
    Create a streaming tool wrapper for a specialist agent.

    Unlike as_tool(), this wrapper uses run_specialist_with_events() to capture
    internal tool calls and emit events to the audit panel.

    Args:
        agent: The specialist agent to wrap
        tool_name: The tool name (e.g., "ask_pdf_extraction_specialist")
        tool_description: Description for the LLM
        specialist_name: Human-readable name for audit events
        run_config: Optional run configuration

    Returns:
        A function_tool decorated async function
    """
    @function_tool(name_override=tool_name, description_override=tool_description)
    async def streaming_tool_wrapper(ctx: RunContextWrapper[Any], query: str) -> str:
        """Ask the specialist a question and get a response."""
        # Reuse the supervisor run's RunConfig (which carries the per-request warm
        # websocket provider) so the nested specialist run shares the same authenticated
        # WebSocket connection instead of opening a new one. The SDK threads the parent
        # run's RunConfig via the tool context in openai-agents 0.17+.
        effective_run_config = getattr(ctx, "run_config", None) or run_config

        async def _runner_coro_factory() -> str:
            result = await run_specialist_with_events(
                agent=agent,
                input_text=query,
                specialist_name=specialist_name,
                run_config=effective_run_config,
                tool_name=tool_name,  # Pass tool_name for batching nudge tracking
            )
            handoff = pop_last_supervisor_extraction_handoff()
            if ledger is not None and handoff is not None:
                ledger.record_extraction_handoff(tool_name, query, handoff)
            return result

        # In standard chat the supervisor is built fresh per turn with a ledger
        # closed over here (NOT a tool argument, so the model-visible schema stays
        # (query)). It collapses identical concurrent calls, short-circuits
        # sequential repeats, and enforces a per-turn invocation budget -- the
        # no-progress brake that flows get from strict step order. Flow supervisors
        # bypass create_supervisor_agent and so have no ledger here.
        if ledger is not None:
            return await ledger.run_or_replay(tool_name, query, _runner_coro_factory)

        return await _runner_coro_factory()

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
    - GPT-5 family models (gpt-5, gpt-5.4-mini)
    - Gemini 3 models (gemini-3.0-pro) - uses "low"/"high" thinking levels
    - Gemini 2.5 models (gemini-2.5-pro, gemini-2.5-flash) - uses thinking budgets

    IMPORTANT: GPT-5 models don't support the temperature parameter -
    they use reasoning instead. Gemini models support both.

    For Gemini, the OpenAI SDK's reasoning_effort parameter maps to:
    - minimal/low -> "low" thinking level (Gemini 3) or 1,024 budget (Gemini 2.5)
    - medium -> "high" thinking level (Gemini 3) or 8,192 budget (Gemini 2.5)
    - high/xhigh -> "high" thinking level (Gemini 3) or 24,576 budget (Gemini 2.5)

    Args:
        model: The model name (e.g., "gpt-5.5", "gpt-5.4-mini", "gemini-3-pro-preview")
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
    notes.append(
        "EXTRACTION RESULT COMPLETION: A non-empty extractor manifest is "
        "normally enough to answer the curator's current request unless the "
        "curator asks to broaden/narrow/rerun or the manifest says the "
        "requested scope was not handled. Answer from the manifest; use "
        "inspect_results to browse existing persisted results, more manifest "
        "objects, evidence, validation findings, or exact YAML-declared field "
        "slices. Do not call extractors again only to summarize existing "
        "results or gain confidence. Use export_to_file only for explicit "
        "export/download requests and prepare_for_curation only after explicit "
        "confirmation. Use inspect_chat_traces for behavior/debug questions "
        "about why a previous answer behaved a certain way or what tools ran."
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
    specialist_model_override: Optional[str] = None,
    specialist_temperature_override: Optional[float] = None,
    specialist_reasoning_override: Optional[str] = None,
    ledger: Optional[SupervisorCallLedger] = None,
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
        if specialist_model_override:
            agent_kwargs["model_id_override"] = specialist_model_override
        if specialist_temperature_override is not None:
            agent_kwargs["model_temperature_override"] = specialist_temperature_override
        if specialist_reasoning_override:
            agent_kwargs["model_reasoning_override"] = specialist_reasoning_override

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
                ledger=ledger,
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
    model_override: Optional[str] = None,
    temperature_override: Optional[float] = None,
    reasoning_override: Optional[ReasoningEffort] = None,
    specialist_model_override: Optional[str] = None,
    specialist_temperature_override: Optional[float] = None,
    specialist_reasoning_override: Optional[str] = None,
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

    effective_model = str(model_override or config.model).strip() or config.model
    effective_temperature = (
        temperature_override if temperature_override is not None else config.temperature
    )
    effective_reasoning = (
        reasoning_override if reasoning_override is not None else config.reasoning
    )

    model_provider = resolve_model_provider(effective_model)

    # Get the model (returns LitellmModel for Gemini/Groq, string for OpenAI)
    model = get_model_for_agent(effective_model, provider_override=model_provider)

    # Build model settings for supervisor
    supervisor_settings = _build_model_settings(
        model=effective_model,
        temperature=effective_temperature,
        reasoning_effort=effective_reasoning,
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
        effective_model,
        effective_temperature,
        effective_reasoning,
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
    # One ledger per chat turn (create_supervisor_agent is called fresh per
    # STANDARD CHAT turn; flow supervisors pass their own prebuilt agent and
    # never reach this function, so they get no ledger and are unaffected). The
    # ledger + budget are the runaway/no-progress brake for chat; we intentionally
    # do NOT lower the shared AGENT_MAX_TURNS here -- this budget supersedes
    # relying on max_turns for runaway protection.
    from ..config import (
        get_supervisor_max_calls_per_specialist,
        get_supervisor_max_specialist_calls_per_turn,
    )

    call_ledger = SupervisorCallLedger(
        max_total_calls=get_supervisor_max_specialist_calls_per_turn(),
        max_calls_per_tool=get_supervisor_max_calls_per_specialist(),
    )

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
        specialist_model_override=specialist_model_override,
        specialist_temperature_override=specialist_temperature_override,
        specialist_reasoning_override=specialist_reasoning_override,
        ledger=call_ledger,
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
            "Prepare persisted canonical extraction results from this chat for curation workspace follow-up. "
            f'Use only after you already asked "{CURATION_PREP_CONFIRMATION_QUESTION}" and the curator '
            "explicitly confirmed in a later turn. Pass the curator's confirmation text verbatim in "
            "`user_confirmation`. Include confirmed adapter_keys when they are clear from the "
            "conversation. This is separate from inspect_results browsing and export_to_file output. "
            "Do not call this tool to ask for confirmation."
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

    @function_tool(
        name_override=_INSPECT_RESULTS_TOOL_NAME,
        description_override=(
            "Inspect persisted canonical extraction results for this chat. Use "
            "action=\"help\" for the contract; action=\"list\" or \"summary\" "
            "for available results; action=\"objects\" or \"object\" for "
            "YAML-declared manifest fields; action=\"field\" for one "
            "YAML-declared scalar field; action=\"evidence\" for bounded "
            "evidence text; and action=\"validation\" for validation findings. "
            "Requires result_ref values in extraction-result:<uuid> form when "
            "addressing a specific result. This tool browses existing results "
            "and does not export, prepare for curation, inspect files, inspect "
            "review sessions, or debug trace behavior."
        ),
    )
    async def inspect_results_tool(
        action: str = "help",
        result_ref: str | None = None,
        target: str = "latest",
        object_ref: str | None = None,
        field_path: str | None = None,
        adapter_keys: List[str] | None = None,
        flow_run_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> str:
        """Inspect bounded persisted extraction results for the active chat."""

        return await inspect_results(
            action=action,
            result_ref=result_ref,
            target=target,
            object_ref=object_ref,
            field_path=field_path,
            adapter_keys=adapter_keys,
            flow_run_id=flow_run_id,
            limit=limit,
            cursor=cursor,
        )

    specialist_tools.append(inspect_results_tool)

    @function_tool(
        name_override=_INSPECT_CHAT_TRACES_TOOL_NAME,
        description_override=(
            "Inspect authorized TraceReview summaries for trace IDs associated with "
            "this main chat session. Use when the curator asks why a prior answer "
            "selected, omitted, searched, validated, or failed something. Trace IDs "
            "must resolve from this chat inventory before TraceReview is queried. "
            "Do not use this for normal extraction-result browsing; use "
            "inspect_results for persisted extraction objects, evidence, fields, "
            "and validation."
        ),
    )
    async def inspect_chat_traces_tool(
        detail: str = "inventory",
        trace_id: str | None = None,
        turn_ref: str | None = None,
        query: str | None = None,
        tool_name: str | None = None,
        event_type: str | None = None,
        candidate_id: str | None = None,
        include_sibling_traces: bool = False,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> str:
        """Inspect bounded TraceReview detail for authorized main-chat traces."""

        return await inspect_chat_traces(
            detail=detail,
            trace_id=trace_id,
            turn_ref=turn_ref,
            query=query,
            tool_name=tool_name,
            event_type=event_type,
            candidate_id=candidate_id,
            include_sibling_traces=include_sibling_traces,
            limit=limit,
            cursor=cursor,
        )

    specialist_tools.append(inspect_chat_traces_tool)

    # Export to File tool (always available - supervisor built-in, not a specialist agent)
    # Allows supervisor to export data as downloadable CSV, TSV, or JSON files
    @function_tool(
        name_override="export_to_file",
        description_override="""Export data to a downloadable file. Use only when the user explicitly asks to:
- Export, download, or save data as CSV, TSV, or JSON
- Get a spreadsheet or file version of results
- "Give me this as CSV", "TSV format please", "Download as JSON"

For existing extraction results, use inspect_results first to select the bounded objects/fields to export. Do not use this tool for ordinary result browsing, summarization, curation prep, or trace debugging.

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

    runtime_prompt_parts = [
        "CURATION PREP RULES:\n"
        f'- If the curator wants to move findings into curation prep, first ask exactly "{CURATION_PREP_CONFIRMATION_QUESTION}"\n'
        "- Do not call prepare_for_curation in the same turn as the confirmation question.\n"
        "- Only call prepare_for_curation after the next user turn explicitly confirms the scope.\n"
        "- When you call prepare_for_curation, pass the user's confirmation text verbatim and include confirmed scope keys when you know them.\n"
        "- If scope is still ambiguous, ask a follow-up clarification question instead of preparing everything."
    ]
    runtime_prompt_parts.append(
        _build_runtime_tool_availability_note(
            tool_specs=tool_specs,
            available_specialist_tools=specialist_tools,
            document_loaded=bool(document_id and user_id),
        )
    )
    prompt_bundle = build_agent_prompt_layers(
        "supervisor",
        group_id=active_groups,
        runtime_context="\n\n".join(part for part in runtime_prompt_parts if part),
    )
    prompts_used = list(prompt_templates_for_bundle(prompt_bundle))
    base_prompt_version = next(
        (
            prompt.version
            for prompt in prompts_used
            if prompt.agent_name == "supervisor" and prompt.prompt_type == "system"
        ),
        None,
    )
    instructions = prompt_bundle.render()

    logger.info(
        "Creating Supervisor agent, model=%s prompt_v=%s groups=%s",
        effective_model,
        base_prompt_version,
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
    prompt_run_id = set_pending_prompts(
        supervisor.name,
        prompts_used,
        effective_prompt_hash=prompt_bundle.hash,
        layer_manifest=prompt_bundle.to_manifest(),
    )
    bind_prompt_run(supervisor, prompt_run_id)

    # Log supervisor configuration to Langfuse for trace visibility
    from ..langfuse_client import log_agent_config as log_agent_config_to_langfuse
    tool_names = [getattr(t, 'name', str(t)) for t in specialist_tools]
    log_agent_config_to_langfuse(
        agent_name="Query Supervisor",
        instructions=instructions,
        model=effective_model,
        tools=tool_names,
        model_settings={
            "temperature": effective_temperature,
            "reasoning": effective_reasoning,
            "prompt_version": base_prompt_version,
        },
        metadata={
            "document_id": document_id,
            "user_id": user_id,
            "specialist_count": len(specialist_tools),
            "effective_prompt_hash": prompt_bundle.hash,
            "layer_manifest": prompt_bundle.to_manifest(),
        }
    )

    logger.info("Supervisor configured with %s specialist tools", len(specialist_tools))

    return supervisor
