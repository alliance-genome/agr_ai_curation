"""
Streaming runner for OpenAI Agents SDK with Langfuse observability.

This module provides a streaming runner that adapts OpenAI Agents SDK
streaming events to SSE-compatible events for the existing frontend.

Langfuse Integration:
    Uses a manual Langfuse root observation for request metadata and the
    OpenAI Agents SDK's native tracing pipeline for model/tool/handoff spans.
    OpenInference exports those SDK spans to Langfuse through OpenTelemetry.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncGenerator, Dict, Any, Optional, List

from agents import (
    Agent,
    Runner,
    RunConfig,
    set_default_openai_api,
    set_default_openai_client,
    set_default_openai_responses_transport,
)
from agents.models.openai_provider import OpenAIProvider
from openai import AsyncOpenAI
from openai.types.responses import (
    ResponseTextDeltaEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseReasoningSummaryTextDeltaEvent
)
from pydantic import ValidationError

from langfuse import propagate_attributes

from .langfuse_client import (
    flush_langfuse,
    get_langfuse,
    flush_agent_configs,
    clear_pending_configs,
    is_openai_agents_tracing_enabled,
)
from .agents.supervisor_agent import create_supervisor_agent
from .audit_labels import (
    BUILTIN_SPECIALIST_DISPLAY_NAMES,
    resolve_tool_display_name as _shared_resolve_tool_display_name,
    build_tool_start_friendly_name as _shared_build_tool_start_friendly_name,
    build_tool_complete_friendly_name as _shared_build_tool_complete_friendly_name,
)
from src.lib.config.providers_loader import get_default_runner_provider
from .config import (
    get_api_key,
    get_base_url,
    get_max_turns,
    get_groq_tool_call_max_retries,
    get_groq_tool_call_retry_delay_seconds,
    is_retryable_groq_tool_call_error,
    reasoning_summary_request_settings,
    resolve_model_provider,
)
from .extraction_trace_events import (
    clear_extraction_trace_run,
    get_current_extraction_trace_run,
    start_extraction_trace_run,
    write_extraction_trace_event,
    write_stream_event,
)
from .extraction_builder_workspace import (
    ExtractionBuilderWorkspace,
    reset_active_extraction_builder_workspace,
    set_active_extraction_builder_workspace,
    stage_extraction_payload,
)
from .guardrails import enforce_uncited_negative_guardrail
from .models import Answer
from .evidence_summary import (
    build_record_evidence_summary_record,
    extract_evidence_records_from_structured_result,
    normalize_evidence_records,
    structured_result_missing_evidence_record_refs,
    structured_result_requires_evidence,
)
from .tools.evidence_workspace import (
    reset_active_evidence_records,
    set_active_evidence_records,
)
from .streaming_tools import (
    get_collected_events,
    clear_collected_events,
    set_live_event_list,
    reset_consecutive_call_tracker,
    SpecialistOutputError,
    SpecialistToolCall,
    _StructuredSpecialistFinalizationState,
    _agent_structured_finalization_config,
    _configure_structured_specialist_finalization,
    _extract_stream_tool_call_tracking_id,
    _max_turns_with_structured_specialist_finalization,
    _pop_matching_pending_tool_call,
    _structured_specialist_finalization_max_attempts,
    _structured_specialist_finalization_required,
    _structured_specialist_finalization_tool_name,
    _tool_output_payload_for_finalization,
    _output_type_name,
)
from .curation_context_registry import clear_current_turn_curation_context

# Prompt context tracking for execution logging
from src.lib.prompts.context import (
    clear_prompt_context,
    commit_pending_prompts,
    get_used_prompt_runs,
    get_used_prompts,
)
from src.lib.prompts.service import PromptService
from src.models.sql.database import SessionLocal

# Request-scoped context for tools (trace_id captured via closure)
from src.lib.context import set_current_trace_id
from src.lib.alerts.tool_failure_notifier import notify_tool_failure

if TYPE_CHECKING:
    from src.lib.document_context import DocumentContext

# Logger must be defined early since _create_openai_client_kwargs uses it at module load
logger = logging.getLogger(__name__)

_CONTEXT_MESSAGE_ROLE_ALIASES = {
    "flow": "assistant",
}
_VALID_CONTEXT_MESSAGE_ROLES = {"assistant", "developer", "system", "user"}


def _env_flag_disabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"0", "false", "no", "off"}


def _openai_responses_websocket_enabled(provider: Any) -> bool:
    """Return whether OpenAI Responses WebSocket transport should be enabled."""

    if _env_flag_disabled("OPENAI_RESPONSES_WEBSOCKET_ENABLED"):
        return False
    if str(getattr(provider, "api_mode", "")).strip().lower() != "responses":
        return False
    if str(getattr(provider, "driver", "openai_native")).strip().lower() != "openai_native":
        return False
    return True


def _configure_api_mode():
    """Configure OpenAI SDK API mode based on default runner provider."""
    provider = get_default_runner_provider()
    if provider.api_mode == "chat_completions":
        set_default_openai_api("chat_completions")
    else:
        set_default_openai_api("responses")

    websocket_enabled = _openai_responses_websocket_enabled(provider)
    set_default_openai_responses_transport("websocket" if websocket_enabled else "http")
    logger.info(
        "Using %s API mode for default runner provider=%s (responses_transport=%s)",
        provider.api_mode,
        provider.provider_id,
        "websocket" if websocket_enabled else "http",
    )


# Configure API mode at module load
_configure_api_mode()


def _create_openai_client_kwargs() -> dict:
    """Build kwargs for OpenAI client based on default runner provider config."""
    kwargs = {}

    default_provider = get_default_runner_provider()
    api_key = get_api_key(default_provider.provider_id)
    base_url = get_base_url(default_provider.provider_id)

    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    if "api_key" not in kwargs and not (
        os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_ADMIN_KEY")
    ):
        kwargs["api_key"] = "missing-api-key"
    websocket_base_url = os.getenv("OPENAI_WEBSOCKET_BASE_URL", "").strip()
    if websocket_base_url:
        kwargs["websocket_base_url"] = websocket_base_url

    logger.info(
        "Using provider=%s base_url=%s",
        default_provider.provider_id,
        base_url or "default",
        extra={"provider": default_provider.provider_id, "base_url": base_url or "default"},
    )

    return kwargs


def normalize_context_message_role(raw_role: Any) -> str:
    """Normalize one context-message role into the runner contract."""

    role = str(raw_role or "").strip().lower()
    return _CONTEXT_MESSAGE_ROLE_ALIASES.get(role, role)


def _normalize_context_messages(
    context_messages: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], str]:
    """Validate and normalize ordered runner context messages.

    The runner contract expects callers to provide the full prompt context in
    chronological order, ending with the current user turn. Durable chat callers
    can build this list directly from SQL transcript rows instead of passing a
    separate `conversation_history` plus `user_message`.
    """

    normalized_messages: List[Dict[str, Any]] = []

    if not isinstance(context_messages, list):
        raise TypeError("context_messages must be a list of message dicts")

    for index, raw_message in enumerate(context_messages):
        if not isinstance(raw_message, dict):
            raise ValueError(f"context_messages[{index}] must be a dict")

        role = normalize_context_message_role(raw_message.get("role"))
        if role not in _VALID_CONTEXT_MESSAGE_ROLES:
            raise ValueError(
                f"context_messages[{index}].role must be one of "
                f"{sorted(_VALID_CONTEXT_MESSAGE_ROLES | set(_CONTEXT_MESSAGE_ROLE_ALIASES))}"
            )

        raw_content = raw_message.get("content")
        content = raw_content if isinstance(raw_content, str) else str(raw_content or "")
        if not content.strip():
            continue

        normalized_messages.append(
            {
                "role": role,
                "content": content,
            }
        )

    if not normalized_messages:
        raise ValueError("context_messages must include at least one non-empty message")

    latest_message = normalized_messages[-1]
    if latest_message["role"] != "user":
        raise ValueError("context_messages must end with a user message")

    return normalized_messages, latest_message["content"]


class SafeAsyncOpenAI(AsyncOpenAI):
    """Wrapper that ensures metadata is always a dict before passing to OpenAI.

    The OpenAI Agents SDK sometimes passes metadata=None, which provider APIs
    can reject. This wrapper keeps provider compatibility without adding a
    second Langfuse/OpenAI instrumentation path.

    Supports providers configured as the default runner provider
    in config/providers.yaml.
    """

    def __init__(self, *args, **kwargs):
        # Merge provider-specific kwargs with any passed kwargs
        provider_kwargs = _create_openai_client_kwargs()
        merged_kwargs = {**provider_kwargs, **kwargs}
        super().__init__(*args, **merged_kwargs)
        self._wrap_responses_api()
        self._wrap_chat_api()

    def _wrap_responses_api(self):
        """Wrap responses.create to sanitize metadata."""
        if hasattr(self, 'responses') and hasattr(self.responses, 'create'):
            original_create = self.responses.create

            async def safe_create(*args, **kwargs):
                # Ensure metadata is a dict (SDK sometimes passes None)
                if 'metadata' in kwargs and not isinstance(kwargs.get('metadata'), dict):
                    kwargs['metadata'] = kwargs['metadata'] if kwargs['metadata'] else {}
                return await original_create(*args, **kwargs)

            self.responses.create = safe_create

    def _wrap_chat_api(self):
        """Wrap chat.completions.create to sanitize metadata."""
        if hasattr(self, 'chat') and hasattr(self.chat, 'completions'):
            original_create = self.chat.completions.create

            async def safe_create(*args, **kwargs):
                # Ensure metadata is a dict (SDK sometimes passes None)
                if 'metadata' in kwargs and not isinstance(kwargs.get('metadata'), dict):
                    kwargs['metadata'] = kwargs['metadata'] if kwargs['metadata'] else {}
                return await original_create(*args, **kwargs)

            self.chat.completions.create = safe_create


# Backward-compatible alias for tests and local helpers. Despite the historic
# name, this now uses the plain OpenAI client; Langfuse capture comes from the
# Agents SDK tracing processor.
SafeLangfuseAsyncOpenAI = SafeAsyncOpenAI


# Set our SafeAsyncOpenAI as the default client for all agents
# This ensures nested agent runs via as_tool() also use our safe wrapper
# that handles metadata=None gracefully
_default_client = SafeAsyncOpenAI()
set_default_openai_client(_default_client)


def _build_agents_run_config(
    *,
    model_provider: OpenAIProvider,
    agent: Agent,
    trace_id: str,
    session_id: Optional[str],
    user_id: str,
    document_id: Optional[str],
    document_name: Optional[str],
) -> RunConfig:
    """Create a RunConfig that captures full SDK traces when Langfuse is wired."""
    sdk_tracing_enabled = is_openai_agents_tracing_enabled()
    return RunConfig(
        model_provider=model_provider,
        tracing_disabled=not sdk_tracing_enabled,
        trace_include_sensitive_data=True,
        workflow_name="AI Curation chat",
        group_id=session_id,
        trace_metadata={
            "langfuse_trace_id": trace_id,
            "session_id": session_id,
            "user_id": user_id,
            "document_id": document_id,
            "document_name": document_name,
            "agent_name": getattr(agent, "name", None),
            "openai_agents_tracing": (
                "langfuse_openinference" if sdk_tracing_enabled else "disabled"
            ),
        },
    )


def _now_iso() -> str:
    """Return current UTC time in ISO format for audit events."""
    return datetime.now(timezone.utc).isoformat()


def _set_langfuse_trace_io(langfuse_client: Any, root_span: Any, **kwargs: Any) -> None:
    """Set trace-level IO while detailed payloads remain on child observations."""
    setter = getattr(root_span, "set_trace_io", None)
    if callable(setter):
        try:
            setter(**kwargs)
            return
        except Exception:
            logger.warning("Failed to set Langfuse trace IO via root span", exc_info=True)

    setter = getattr(langfuse_client, "set_current_trace_io", None)
    if callable(setter):
        try:
            setter(**kwargs)
            return
        except Exception:
            logger.warning("Failed to set Langfuse trace IO via client", exc_info=True)


def _merge_evidence_records(
    existing: List[Dict[str, Any]],
    incoming: Any,
) -> List[Dict[str, Any]]:
    """Merge normalized evidence records while preserving first-seen order."""
    merged: List[Dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for record in [*existing, *normalize_evidence_records(incoming)]:
        key = (
            record.get("entity"),
            record.get("verified_quote"),
            record.get("page"),
            record.get("section"),
            record.get("chunk_id"),
            record.get("subsection"),
            record.get("figure_reference"),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(record)

    return merged


def _merge_evidence_tool_names(existing: List[str], incoming: Any) -> List[str]:
    """Merge specialist tool names while preserving first-seen order."""
    merged: List[str] = []
    seen: set[str] = set()

    for tool_name in [*existing, *(incoming or [])]:
        normalized = str(tool_name or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)

    return merged


def _close_langfuse_context(
    context_manager: Any,
    *,
    label: str,
    trace_id: str,
    session_id: Optional[str],
    user_id: str,
) -> None:
    """Best-effort cleanup for Langfuse context managers across async yields."""
    if context_manager is None:
        return

    try:
        context_manager.__exit__(None, None, None)
        logger.info(
            "%s closed",
            label,
            extra={"trace_id": trace_id, "session_id": session_id, "user_id": user_id},
        )
    except ValueError as e:
        if "Token was created in a different Context" in str(e):
            logger.debug(
                "%s detach skipped (async boundary): %s",
                label,
                e,
                extra={"trace_id": trace_id, "session_id": session_id, "user_id": user_id},
            )
        else:
            logger.warning(
                "Unexpected error during %s cleanup: %s",
                label.lower(),
                e,
                extra={"trace_id": trace_id, "session_id": session_id, "user_id": user_id},
            )


def _build_custom_tool_display_names(agent: Agent) -> Dict[str, str]:
    """Map custom specialist tool names to user-facing labels.

    Custom flow tools use names like ask_ca_<uuid>_specialist. We recover a readable
    label from the tool description (typically "Ask the <Agent Name>").
    """
    display_names: Dict[str, str] = {}
    for tool in getattr(agent, "tools", []) or []:
        tool_name = (getattr(tool, "name", None) or "").strip()
        if not tool_name.startswith("ask_ca_") or not tool_name.endswith("_specialist"):
            continue

        description = (getattr(tool, "description", None) or "").strip()
        if not description:
            continue

        lower_desc = description.lower()
        if lower_desc.startswith("ask the "):
            display = description[8:].strip()
        elif lower_desc.startswith("ask "):
            display = description[4:].strip()
        else:
            display = description

        if display:
            display_names[tool_name] = display

    return display_names


# Backward-compatible alias for unit tests and local helpers in this module.
_BUILTIN_SPECIALIST_DISPLAY_NAMES = BUILTIN_SPECIALIST_DISPLAY_NAMES


def _resolve_tool_display_name(tool_name: str, custom_display_names: Dict[str, str]) -> str:
    """Resolve the best user-facing display name for a tool call."""
    return _shared_resolve_tool_display_name(tool_name, custom_display_names)


def _build_tool_start_friendly_name(tool_name: str, custom_display_names: Dict[str, str]) -> str:
    """Build a stable TOOL_START label and guarantee non-empty output."""
    return _shared_build_tool_start_friendly_name(tool_name, custom_display_names)


def _build_tool_complete_friendly_name(tool_name: str, custom_display_names: Dict[str, str]) -> str:
    """Build a stable TOOL_COMPLETE label and guarantee non-empty output."""
    return _shared_build_tool_complete_friendly_name(tool_name, custom_display_names)


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


def _is_groq_runtime_model(model: Any) -> bool:
    """Detect whether runtime model appears to be Groq-backed."""
    model_id = _extract_model_identifier(model).lower()
    if model_id.startswith("groq/"):
        return True
    if "groq" in model_id and "/" in model_id:
        return True

    base_url = str(getattr(model, "base_url", "") or "").lower()
    if "api.groq.com" in base_url:
        return True

    return False


async def _run_agent_with_groq_retry(
    *,
    agent: Agent,
    input_items: List[Dict[str, Any]],
    user_id: str,
    document_id: Optional[str],
    document_name: Optional[str],
    user_message: str,
    trace_id: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Run tracing stream with Groq-specific retry on transient tool-call parse failures."""
    max_retries = get_groq_tool_call_max_retries() if _is_groq_runtime_model(getattr(agent, "model", None)) else 0
    retry_delay_seconds = get_groq_tool_call_retry_delay_seconds()
    attempt = 0

    while True:
        try:
            async for event in _run_agent_with_tracing(
                agent=agent,
                input_items=input_items,
                user_id=user_id,
                document_id=document_id,
                document_name=document_name,
                user_message=user_message,
                trace_id=trace_id,
            ):
                yield event
            return
        except SpecialistOutputError:
            raise
        except Exception as exc:
            if attempt >= max_retries or not is_retryable_groq_tool_call_error(exc):
                raise

            attempt += 1
            sleep_seconds = retry_delay_seconds * attempt
            logger.warning(
                "Retrying Groq run after transient tool-call parse failure "
                "(attempt %s/%s, delay=%ss): %s",
                attempt,
                max_retries,
                round(sleep_seconds, 2),
                exc,
                extra={
                    "trace_id": trace_id,
                    "user_id": user_id,
                    "attempt": attempt,
                    "max_retries": max_retries,
                },
            )
            yield {
                "type": "SUPERVISOR_RETRY",
                "timestamp": _now_iso(),
                "details": {
                    "attempt": attempt,
                    "maxRetries": max_retries,
                    "reason": "groq_tool_call_json_parse",
                    "message": (
                        "Transient Groq tool-call JSON parse failure detected. "
                        "Retrying automatically."
                    ),
                },
            }
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)


def _log_used_prompts_to_db(
    trace_id: str,
    session_id: Optional[str] = None,
    span: Optional[Any] = None,
) -> int:
    """Log all used prompts to the database and Langfuse trace.

    Called after agent execution completes to record which prompt versions
    were used in this request for audit trail.

    Args:
        trace_id: Langfuse trace ID for correlation
        session_id: Chat session ID (optional)
        span: Langfuse span to add prompt version metadata (optional)

    Returns:
        Number of prompts logged
    """
    used_prompt_runs = get_used_prompt_runs()
    used_prompts = get_used_prompts()
    if not used_prompts and not used_prompt_runs:
        logger.debug("No prompts to log")
        return 0
    if used_prompts and not used_prompt_runs:
        logger.warning(
            "Skipping prompt usage logging because prompt runs are missing assembly metadata",
            extra={"trace_id": trace_id, "session_id": session_id},
        )
        return 0

    missing_assembly_agents = [
        run.agent_name for run in used_prompt_runs if run.assembly is None
    ]
    if missing_assembly_agents:
        logger.error(
            "Skipping prompt usage logging because prompt runs lack assembly metadata: %s",
            missing_assembly_agents,
            extra={"trace_id": trace_id, "session_id": session_id},
        )
        return 0

    used_prompts = [
        prompt
        for run in used_prompt_runs
        for prompt in run.prompts
    ]

    # Add prompt version metadata to Langfuse span if provided
    # Note: Using span.update(metadata=...) since span.event() only exists on trace objects
    if span:
        try:
            prompt_versions = [
                {
                    "agent": p.agent_name,
                    "type": p.prompt_type,
                    "group": p.group_id,
                    "version": p.version,
                    "id": str(p.id),
                }
                for p in used_prompts
            ]
            prompt_assemblies = [
                {
                    "effective_prompt_hash": run.assembly.effective_prompt_hash,
                    "layer_manifest": run.assembly.layer_manifest,
                }
                for run in used_prompt_runs
            ]
            span.update(
                metadata={
                    "prompts_used": prompt_versions,
                    "prompt_count": len(prompt_versions),
                    "prompt_assemblies": prompt_assemblies,
                }
            )
            logger.debug("Added %s prompt versions to span metadata", len(prompt_versions))
        except Exception as e:
            # Non-critical - continue even if Langfuse update fails
            logger.warning("Failed to add prompt versions to span: %s", e)

    try:
        db = SessionLocal()
        try:
            service = PromptService(db)
            entries = []
            for run in used_prompt_runs:
                if not run.prompts:
                    continue
                assert run.assembly is not None
                entries.extend(
                    service.log_all_used_prompts(
                        prompts=run.prompts,
                        trace_id=trace_id,
                        session_id=session_id,
                        effective_prompt_hash=run.assembly.effective_prompt_hash,
                        layer_manifest=run.assembly.layer_manifest,
                    )
                )
            db.commit()
            logger.info(
                "Logged %s prompt usages to database",
                len(entries),
                extra={"trace_id": trace_id, "session_id": session_id},
            )
            return len(entries)
        finally:
            db.close()
    except Exception as e:
        # Log error but don't fail the request - prompt logging is non-critical
        logger.error(
            "Failed to log prompts: %s",
            e,
            extra={"trace_id": trace_id, "session_id": session_id},
            exc_info=True,
        )
        return 0

async def _run_agent_with_tracing(
    agent: Agent,
    input_items: List[Dict[str, Any]],
    user_id: str,
    document_id: Optional[str],
    document_name: Optional[str],
    user_message: str,
    trace_id: str,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Internal generator that runs the agent within Langfuse trace context.

    This function is called from within an active Langfuse observation context,
    so all OpenAI calls made by the agent are automatically nested.

    REAL-TIME STREAMING:
    Uses an asyncio.Queue to receive specialist events in real-time.
    Events are drained from the queue concurrently with SDK event processing,
    giving immediate visibility into specialist agent activity.
    """
    full_response = ""
    structured_result = None
    tools_called: List[str] = []
    pending_tool_calls: deque[Dict[str, Any]] = deque()
    tool_calls_count = 0
    current_agent = agent.name
    agents_used = [agent.name]
    custom_tool_display_names = _build_custom_tool_display_names(agent)
    is_generating = False  # Track if we've emitted AGENT_GENERATING for current generation phase
    reasoning_summary_chunks: List[str] = []

    openai_client = SafeLangfuseAsyncOpenAI()
    openai_provider = OpenAIProvider(openai_client=openai_client)
    run_config = _build_agents_run_config(
        model_provider=openai_provider,
        agent=agent,
        trace_id=trace_id,
        session_id=getattr(get_current_extraction_trace_run(), "session_id", None),
        user_id=user_id,
        document_id=document_id,
        document_name=document_name,
    )

    # Create live event list for real-time specialist event streaming
    # Events appended to this list are yielded during stream processing
    live_events: List[Dict[str, Any]] = []
    set_live_event_list(live_events)
    logger.info(
        "Live event list enabled for real-time streaming",
        extra={"trace_id": trace_id, "user_id": user_id},
    )
    evidence_records: List[Dict[str, Any]] = []
    evidence_workspace_token = set_active_evidence_records(evidence_records)
    builder_workspace = ExtractionBuilderWorkspace(
        run_id=trace_id,
        document_id=document_id,
        agent_id=current_agent,
    )
    builder_workspace_token = set_active_extraction_builder_workspace(builder_workspace)
    evidence_summary_tool_names: List[str] = []
    structured_tool_calls: List[SpecialistToolCall] = []

    # max_turns from config gives agents more time to think and process complex queries
    max_turns = get_max_turns()
    expected_output_type = getattr(agent, "output_type", None)
    finalization_config = _agent_structured_finalization_config(
        agent,
        tool_name=None,
    )
    finalization_tool_name = _structured_specialist_finalization_tool_name(
        finalization_config
    )
    structured_finalization_state = _StructuredSpecialistFinalizationState(
        required=_structured_specialist_finalization_required(
            agent,
            expected_output_type=expected_output_type,
            builder_materializer_agent=False,
            finalization_config=finalization_config,
        ),
        tool_name=finalization_tool_name or "finalize_structured_result",
        agent_name=current_agent,
        output_type_name=_output_type_name(expected_output_type),
        config=finalization_config,
        max_attempts=_structured_specialist_finalization_max_attempts(
            finalization_config
        ),
    )
    if structured_finalization_state.required:
        max_turns = _max_turns_with_structured_specialist_finalization(max_turns)
        agent = _configure_structured_specialist_finalization(
            agent,
            agent,
            expected_output_type=expected_output_type,
            finalization_state=structured_finalization_state,
            tool_calls=structured_tool_calls,
            live_evidence_records=evidence_records,
        )
    llm_run_start = time.monotonic()
    result = Runner.run_streamed(agent, input=input_items, max_turns=max_turns, run_config=run_config)
    write_extraction_trace_event(
        event_type="model.reasoning_summary.request",
        trace_id=trace_id,
        input_summary=_reasoning_request_metadata(agent),
        metadata={"agent": current_agent},
    )

    # Track position in live_events list for yielding new events
    live_events_yielded = 0

    # Create a concurrent event generator using separate tasks
    # This allows live events to be yielded even while SDK is executing tools
    async def interleaved_events():
        """
        Interleave SDK stream events with live specialist events using concurrent tasks.

        The SDK stream runs in a background task, putting events onto a queue.
        The main loop polls both the queue and live_events list, allowing
        real-time visibility into specialist activity during tool execution.
        """
        nonlocal live_events_yielded

        # Queue for SDK events from background task
        sdk_queue: asyncio.Queue = asyncio.Queue()

        async def sdk_producer():
            """Background task that consumes SDK events and puts them on the queue."""
            try:
                async for event in result.stream_events():
                    await sdk_queue.put(("sdk", event))
            except Exception as e:
                logger.error(
                    "SDK producer error: %s",
                    e,
                    extra={"trace_id": trace_id, "user_id": user_id},
                    exc_info=True,
                )
                await sdk_queue.put(("error", e))
            finally:
                await sdk_queue.put(None)  # Sentinel to signal completion

        # Start SDK producer as background task
        sdk_task = asyncio.create_task(sdk_producer())
        logger.info(
            "Started SDK producer task for concurrent streaming",
            extra={"trace_id": trace_id, "user_id": user_id},
        )

        try:
            while True:
                # First, yield any new live events that have accumulated
                # This runs every iteration, even when SDK is blocked on tools
                while live_events_yielded < len(live_events):
                    specialist_event = live_events[live_events_yielded]
                    live_events_yielded += 1
                    logger.debug(
                        "Yielding live specialist event: %s",
                        specialist_event.get("type"),
                        extra={"trace_id": trace_id, "user_id": user_id},
                    )
                    yield ("live", specialist_event)

                # Try to get SDK event with short timeout
                # Timeout allows us to re-check live_events periodically
                try:
                    item = await asyncio.wait_for(sdk_queue.get(), timeout=0.05)
                    if item is None:
                        # SDK stream completed
                        logger.info(
                            "SDK stream completed",
                            extra={"trace_id": trace_id, "user_id": user_id},
                        )
                        break
                    if item[0] == "error":
                        # CRITICAL: Yield remaining live events BEFORE re-raising
                        # This ensures SPECIALIST_RETRY warnings are visible to users
                        # even when the retry ultimately fails
                        while live_events_yielded < len(live_events):
                            specialist_event = live_events[live_events_yielded]
                            live_events_yielded += 1
                            logger.debug(
                                "Yielding live event before error: %s",
                                specialist_event.get("type"),
                                extra={"trace_id": trace_id, "user_id": user_id},
                            )
                            yield ("live", specialist_event)

                        # Now re-raise SDK errors
                        raise item[1]
                    yield item
                except asyncio.TimeoutError:
                    # No SDK event yet, loop continues to check live_events
                    pass

            # Yield any remaining live events after stream ends
            while live_events_yielded < len(live_events):
                specialist_event = live_events[live_events_yielded]
                live_events_yielded += 1
                logger.debug(
                    "Yielding final live specialist event: %s",
                    specialist_event.get("type"),
                    extra={"trace_id": trace_id, "user_id": user_id},
                )
                yield ("live", specialist_event)

        finally:
            # Clean up background task
            if not sdk_task.done():
                sdk_task.cancel()
                try:
                    await sdk_task
                except asyncio.CancelledError:
                    pass

    try:
        async for event_source, event in interleaved_events():
            # Handle live events (specialist internal tools)
            if event_source == "live":
                if event.get("type") == "evidence_summary":
                    evidence_records[:] = _merge_evidence_records(
                        evidence_records,
                        event.get("evidence_records"),
                    )
                    evidence_summary_tool_names = _merge_evidence_tool_names(
                        evidence_summary_tool_names,
                        [
                            event.get("tool_name"),
                            *(event.get("tool_names") or []),
                        ],
                    )
                    continue
                yield event
                continue

            # Handle SDK events
            event_type = getattr(event, "type", None)

            if event_type == "raw_response_event":
                # Handle raw LLM response events
                data = getattr(event, "data", None)
                if data is not None:
                    # Only stream TEXT deltas to chat (not function call arguments)
                    if isinstance(data, ResponseTextDeltaEvent):
                        delta = getattr(data, "delta", None)
                        if delta:
                            # Emit AGENT_GENERATING once when text streaming starts
                            if not is_generating:
                                is_generating = True
                                logger.debug(
                                    "Agent generating response: %s",
                                    current_agent,
                                    extra={"trace_id": trace_id, "user_id": user_id},
                                )
                                yield {
                                    "type": "AGENT_GENERATING",
                                    "timestamp": _now_iso(),
                                    "details": {
                                        "agentRole": current_agent,
                                        "agentDisplayName": current_agent,
                                        "message": "Agent reasoning"
                                    }
                                }
                            full_response += delta
                            yield {
                                "type": "TEXT_MESSAGE_CONTENT",
                                "data": {"delta": delta}
                            }
                    elif isinstance(data, ResponseFunctionCallArgumentsDeltaEvent):
                        # Function call arguments - send to audit panel only
                        delta = getattr(data, "delta", None)
                        if delta:
                            yield {
                                "type": "TOOL_CALL_ARGS",
                                "data": {"delta": delta}
                            }
                    elif isinstance(data, ResponseReasoningSummaryTextDeltaEvent):
                        # Reasoning summary text - show in audit panel
                        delta = getattr(data, "delta", None)
                        if delta:
                            reasoning_summary_chunks.append(delta)
                            write_extraction_trace_event(
                                event_type="model.reasoning_summary.delta",
                                trace_id=trace_id,
                                output_summary={"summary_text": delta},
                                metadata={"agent": current_agent, "availability": "present"},
                            )
                            yield {
                                "type": "AGENT_THINKING",
                                "timestamp": _now_iso(),
                                "details": {
                                    "agentRole": current_agent,
                                    "agentDisplayName": current_agent,
                                    "message": delta
                                }
                            }
                    elif type(data).__name__ in {
                        "ResponseReasoningSummaryTextDoneEvent",
                        "ResponseReasoningSummaryPartDoneEvent",
                    }:
                        text = getattr(data, "text", None)
                        if text is None:
                            part = getattr(data, "part", None)
                            text = getattr(part, "text", None)
                        summary_text = str(text or "").strip() or "".join(reasoning_summary_chunks).strip()
                        if summary_text:
                            write_extraction_trace_event(
                                event_type="model.reasoning_summary.output",
                                trace_id=trace_id,
                                output_summary={"summary_text": summary_text},
                                metadata={"agent": current_agent, "availability": "present"},
                            )

            elif event_type == "run_item_stream_event":
                # Handle structured events (tool calls, outputs, messages)
                item = getattr(event, "item", None)
                if item is not None:
                    item_type = getattr(item, "type", None)

                    if item_type == "tool_call_item":
                        tool_calls_count += 1
                        is_generating = False  # Reset for next generation phase after tool completes
                        # Try multiple attributes to get tool name
                        tool_name = (
                            getattr(item, "name", None) or
                            getattr(item, "tool_name", None) or
                            getattr(getattr(item, "raw_item", None), "name", None) or
                            "tool"
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
                        tool_id = _extract_stream_tool_call_tracking_id(item)
                        tools_called.append(tool_name)
                        pending_tool_calls.append({
                            "tool_name": tool_name,
                            "tool_input": tool_args,
                            "tool_id": tool_id,
                        })
                        logger.info(
                            "Tool call started: %s",
                            tool_name,
                            extra={
                                "trace_id": trace_id,
                                "user_id": user_id,
                                "tool_name": tool_name,
                                "agent": current_agent,
                            },
                        )
                        # Audit event: TOOL_START
                        tool_start_event = {
                            "type": "TOOL_START",
                            "timestamp": _now_iso(),
                            "details": {
                                "toolName": tool_name,
                                "friendlyName": _build_tool_start_friendly_name(
                                    tool_name,
                                    custom_tool_display_names,
                                ),
                                "agent": current_agent,
                                "toolArgs": tool_args
                            }
                        }
                        write_stream_event(tool_start_event, trace_id=trace_id, tool_call_id=tool_id)
                        yield tool_start_event

                    elif item_type == "tool_call_output_item":
                        output = getattr(item, "output", "")
                        completed_tool = _pop_matching_pending_tool_call(
                            pending_tool_calls,
                            output_item=item,
                        )
                        if completed_tool is None:
                            completed_tool = {
                                "tool_name": tools_called[-1] if tools_called else "tool",
                                "tool_input": None,
                            }
                        # Truncate long outputs for the preview
                        output_preview = str(output)[:300]
                        if len(str(output)) > 300:
                            output_preview += "..."
                        # Get last tool name for the completion event
                        last_tool = str(completed_tool.get("tool_name") or "tool")
                        logger.info(
                            "Tool call completed, output length=%s",
                            len(str(output)),
                            extra={"trace_id": trace_id, "user_id": user_id, "tool_name": last_tool},
                        )

                        evidence_record = build_record_evidence_summary_record(
                            tool_name=last_tool,
                            tool_input=completed_tool.get("tool_input"),
                            tool_output=output,
                        )
                        if evidence_record is not None:
                            evidence_records[:] = _merge_evidence_records(
                                evidence_records,
                                [evidence_record],
                            )
                        structured_tool_calls.append(
                            SpecialistToolCall(
                                tool_name=last_tool,
                                tool_args=completed_tool.get("tool_input") or {},
                                output_payload=_tool_output_payload_for_finalization(
                                    last_tool,
                                    output,
                                ),
                            )
                        )

                        # Emit any remaining collected specialist events (fallback for batch mode)
                        # Most events should have been streamed via queue, this catches any stragglers
                        specialist_events = get_collected_events()
                        if specialist_events:
                            logger.info(
                                "Emitting %s remaining specialist events",
                                len(specialist_events),
                                extra={"trace_id": trace_id, "user_id": user_id},
                            )
                            for specialist_event in specialist_events:
                                if specialist_event.get("type") == "evidence_summary":
                                    evidence_records[:] = _merge_evidence_records(
                                        evidence_records,
                                        specialist_event.get("evidence_records"),
                                    )
                                    evidence_summary_tool_names = _merge_evidence_tool_names(
                                        evidence_summary_tool_names,
                                        [
                                            specialist_event.get("tool_name"),
                                            *(specialist_event.get("tool_names") or []),
                                        ],
                                    )
                                    continue
                                yield specialist_event
                            clear_collected_events()

                        # Audit event: TOOL_COMPLETE
                        tool_complete_event = {
                            "type": "TOOL_COMPLETE",
                            "timestamp": _now_iso(),
                            "details": {
                                "toolName": last_tool,
                                "friendlyName": _build_tool_complete_friendly_name(
                                    last_tool,
                                    custom_tool_display_names,
                                ),
                                "success": True
                            },
                            # Internal payload used by backend-only consumers
                            # (e.g., flow-context memory injection). SSE flatteners
                            # intentionally drop this field, so it is not user-visible.
                            "internal": {
                                "tool_output": output,
                                "tool_input": completed_tool.get("tool_input"),
                                "output_length": len(str(output)),
                                "output_preview": output_preview,
                            },
                        }
                        write_stream_event(
                            tool_complete_event,
                            trace_id=trace_id,
                            tool_call_id=str(completed_tool.get("tool_id") or "") or None,
                        )
                        yield tool_complete_event

                        # Check if chat_output agent completed (for flow termination)
                        # This signals that a chat-based flow has produced its final output
                        if last_tool == "ask_chat_output_specialist":
                            full_output = str(output) if output is not None else ""
                            logger.info(
                                "Chat output agent completed",
                                extra={"trace_id": trace_id, "user_id": user_id},
                            )
                            chat_ready_event = {
                                "type": "CHAT_OUTPUT_READY",
                                "timestamp": _now_iso(),
                                "details": {
                                    "output": full_output,
                                    "output_preview": output_preview,
                                    "output_length": len(full_output),
                                }
                            }
                            write_stream_event(chat_ready_event, trace_id=trace_id)
                            yield chat_ready_event

                        # Check if tool output contains FileInfo (file download)
                        # export_to_file and file formatter tools return FileInfo as JSON
                        if output:
                            try:
                                output_data = json.loads(str(output)) if isinstance(output, str) else output
                                # Check for FileInfo signature: must have file_id and download_url
                                if (
                                    isinstance(output_data, dict) and
                                    output_data.get("file_id") and
                                    output_data.get("download_url") and
                                    output_data.get("filename")
                                ):
                                    logger.info(
                                        "File output detected: %s (%s)",
                                        output_data.get("filename"),
                                        output_data.get("format"),
                                        extra={"trace_id": trace_id, "user_id": user_id},
                                    )
                                    file_ready_event = {
                                        "type": "FILE_READY",
                                        "timestamp": _now_iso(),
                                        "details": {
                                            "file_id": output_data.get("file_id"),
                                            "filename": output_data.get("filename"),
                                            "format": output_data.get("format"),
                                            "size_bytes": output_data.get("size_bytes"),
                                            "mime_type": output_data.get("mime_type"),
                                            "download_url": output_data.get("download_url"),
                                            "created_at": output_data.get("created_at"),
                                        }
                                    }
                                    write_stream_event(file_ready_event, trace_id=trace_id)
                                    yield file_ready_event
                            except (json.JSONDecodeError, TypeError, AttributeError):
                                # Not JSON or not FileInfo - ignore
                                pass

                    elif item_type == "message_output_item":
                        # Final message output - extract text if not already captured
                        try:
                            from agents.items import ItemHelpers
                            message_text = ItemHelpers.text_message_output(item)
                            if message_text and not full_response:
                                full_response = message_text
                        except Exception:
                            pass

                    elif item_type == "handoff_call_item":
                        # Handle handoff to another agent
                        target_agent = getattr(item, "target_agent", None)
                        if target_agent:
                            target_name = getattr(target_agent, "name", "unknown")
                            logger.info(
                                "Handoff to: %s",
                                target_name,
                                extra={"trace_id": trace_id, "user_id": user_id},
                            )
                            yield {
                                "type": "HANDOFF_START",
                                "data": {
                                    "from_agent": current_agent,
                                    "to_agent": target_name
                                }
                            }

                    elif item_type == "handoff_output_item":
                        # Handoff completed
                        source_agent = getattr(item, "source_agent", None)
                        if source_agent:
                            source_name = getattr(source_agent, "name", "unknown")
                            logger.info(
                                "Handoff completed from: %s",
                                source_name,
                                extra={"trace_id": trace_id, "user_id": user_id},
                            )

            elif event_type == "agent_updated_stream_event":
                # Handle agent switches during handoffs
                new_agent = getattr(event, "new_agent", None)
                if new_agent:
                    new_agent_name = getattr(new_agent, "name", "unknown")
                    logger.info(
                        "Agent switched to: %s",
                        new_agent_name,
                        extra={"trace_id": trace_id, "user_id": user_id},
                    )
                    # Emit completion for previous agent
                    yield {
                        "type": "AGENT_COMPLETE",
                        "timestamp": _now_iso(),
                        "details": {
                            "agentRole": current_agent,
                            "agentDisplayName": current_agent
                        }
                    }
                    current_agent = new_agent_name
                    is_generating = False  # Reset for new agent's generation phase
                    if new_agent_name not in agents_used:
                        agents_used.append(new_agent_name)
                    # Audit event: CREW_START for new agent
                    yield {
                        "type": "CREW_START",
                        "timestamp": _now_iso(),
                        "details": {
                            "crewName": new_agent_name,
                            "crewDisplayName": new_agent_name,
                            "agents": [new_agent_name]
                        }
                    }

        # Yield any remaining live events after stream completes
        while live_events_yielded < len(live_events):
            yield live_events[live_events_yielded]
            live_events_yielded += 1

    except asyncio.CancelledError:
        builder_workspace.mark_cancelled(reason="runner stream cancelled")
        raise
    except Exception as exc:
        builder_workspace.mark_aborted(reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        # Clear the live event list reference
        set_live_event_list(None)
        reset_active_evidence_records(evidence_workspace_token)
        reset_active_extraction_builder_workspace(builder_workspace_token)

    # Get final output if not captured from streaming
    if hasattr(result, "final_output"):
        final_output = result.final_output
        if final_output:
            if hasattr(final_output, "model_dump"):
                structured_result = stage_extraction_payload(
                    final_output.model_dump(),
                    workspace=builder_workspace,
                    candidate_id="runner_structured_result",
                    evidence_records=evidence_records,
                )
            elif isinstance(final_output, dict):
                structured_result = stage_extraction_payload(
                    final_output,
                    workspace=builder_workspace,
                    candidate_id="runner_structured_result",
                    evidence_records=evidence_records,
                )
            if not full_response:
                full_response = str(final_output)

    if structured_finalization_state.required:
        if structured_finalization_state.accepted_payload is not None:
            structured_result = stage_extraction_payload(
                structured_finalization_state.accepted_payload,
                workspace=builder_workspace,
                candidate_id="runner_structured_result",
                evidence_records=evidence_records,
            )
            if not full_response:
                full_response = json.dumps(
                    structured_finalization_state.accepted_payload,
                    default=str,
                )
        else:
            if structured_finalization_state.attempt_limit_exceeded:
                error_message = (
                    f"{current_agent} exceeded the "
                    f"{structured_finalization_state.max_attempts}-attempt limit "
                    f"for mandatory {structured_finalization_state.tool_name} "
                    "without status accepted."
                )
                reason = "structured_finalization_attempt_limit_exceeded"
            elif structured_finalization_state.last_rejection is not None:
                error_message = (
                    f"{current_agent} did not complete mandatory "
                    f"{structured_finalization_state.tool_name} with status accepted. "
                    "Last rejection: "
                    f"{structured_finalization_state.last_rejection.get('message')}"
                )
                reason = "structured_finalization_rejected"
            else:
                error_message = (
                    f"{current_agent} did not call mandatory "
                    f"{structured_finalization_state.tool_name} with status accepted."
                )
                reason = "structured_finalization_missing"
            builder_workspace.record_validation_failure(
                errors=[
                    {
                        "message": error_message,
                        "reason": reason,
                    }
                ],
                candidate_ids=["runner_structured_result"],
            )
            run_error_event = {
                "type": "RUN_ERROR",
                "data": {
                    "message": error_message,
                    "error_type": "StructuredFinalizationFailed",
                    "trace_id": trace_id,
                },
            }
            write_stream_event(run_error_event, trace_id=trace_id)
            yield run_error_event
            return

    duration_ms = (time.monotonic() - llm_run_start) * 1000
    logger.info(
        "Run completed",
        extra={
            "trace_id": trace_id,
            "user_id": user_id,
            "response_length": len(full_response),
            "tool_calls": tool_calls_count,
            "agents_used": agents_used,
            "duration_ms": round(duration_ms, 1),
            "operation": "llm_stream_run",
        },
    )

    # Run robust uncited-negative guardrail using actual tool calls (if structured Answer)
    if structured_result is not None:
        expected_output_type = getattr(agent, "output_type", None)
        structured_evidence_records = extract_evidence_records_from_structured_result(structured_result)
        if (
            structured_result_requires_evidence(
                structured_result,
                expected_output_type=expected_output_type,
            )
            and (
                not evidence_records
                or not structured_evidence_records
                or structured_result_missing_evidence_record_refs(
                    structured_result,
                    expected_output_type=expected_output_type,
                )
            )
        ):
            logger.error(
                "Structured extraction result is missing required verified evidence records or references",
                extra={
                    "trace_id": trace_id,
                    "user_id": user_id,
                    "structured_result_keys": sorted(structured_result.keys())
                    if isinstance(structured_result, dict)
                    else None,
                },
            )
            builder_workspace.record_validation_failure(
                errors=[
                    {
                        "message": "Structured extraction result is missing required verified evidence records or references.",
                        "reason": "missing_evidence_records",
                    }
                ],
                candidate_ids=["runner_structured_result"],
            )
            run_error_event = {
                "type": "RUN_ERROR",
                "data": {
                    "message": (
                        "Extraction completed without the required verified evidence records. "
                        "Please report this run so we can investigate."
                    ),
                    "error_type": "MissingEvidenceRecords",
                    "trace_id": trace_id,
                },
            }
            write_stream_event(run_error_event, trace_id=trace_id)
            yield run_error_event
            return

        if structured_evidence_records:
            evidence_records[:] = _merge_evidence_records(
                evidence_records,
                structured_evidence_records,
            )

        finalization = builder_workspace.finalize(
            candidate_ids=["runner_structured_result"],
        )
        structured_result = finalization.payload

        try:
            parsed_answer = Answer.model_validate(structured_result)
            guardrail_message = enforce_uncited_negative_guardrail(parsed_answer, tools_called)
            if guardrail_message:
                run_error_event = {
                    "type": "RUN_ERROR",
                    "data": {
                        "message": guardrail_message,
                        "error_type": "GuardrailTriggered",
                        "trace_id": trace_id
                    }
                }
                write_stream_event(run_error_event, trace_id=trace_id)
                yield run_error_event
                return
        except ValidationError:
            pass

        structured_event = {
            "type": "STRUCTURED_RESULT",
            "data": {
                "result": structured_result,
                "trace_id": trace_id
            }
        }
        write_extraction_trace_event(
            event_type="extraction_builder.structured_result",
            trace_id=trace_id,
            output_summary=structured_result,
            metadata={"agent": current_agent},
        )
        yield structured_event

    # Audit event: SUPERVISOR_COMPLETE
    supervisor_complete_event = {
        "type": "SUPERVISOR_COMPLETE",
        "timestamp": _now_iso(),
        "details": {
            "message": "Query completed successfully",
            "totalSteps": len(agents_used)
        }
    }
    write_stream_event(supervisor_complete_event, trace_id=trace_id)
    yield supervisor_complete_event

    # Emit consolidated evidence summary for verified extraction records.
    if evidence_records:
        evidence_summary_event = {
            "type": "evidence_summary",
            "timestamp": _now_iso(),
            "evidence_records": evidence_records,
        }
        if evidence_summary_tool_names:
            evidence_summary_event["tool_names"] = evidence_summary_tool_names
            if len(evidence_summary_tool_names) == 1:
                evidence_summary_event["tool_name"] = evidence_summary_tool_names[0]
        write_stream_event(evidence_summary_event, trace_id=trace_id)
        yield evidence_summary_event

    # Emit completion event with summary for updating the span
    run_finished_event = {
        "type": "RUN_FINISHED",
        "data": {
            "response": full_response,
            "response_length": len(full_response),
            "tool_calls": tool_calls_count,
            "agents_used": agents_used,
            "trace_id": trace_id
        }
    }
    write_stream_event(run_finished_event, trace_id=trace_id)
    yield run_finished_event


async def run_agent_streamed(
    context_messages: List[Dict[str, Any]],
    user_id: str,
    session_id: Optional[str] = None,
    document_id: Optional[str] = None,
    document_name: Optional[str] = None,
    active_groups: Optional[List[str]] = None,
    supervisor_model: Optional[str] = None,
    specialist_model: Optional[str] = None,
    supervisor_temperature: Optional[float] = None,
    specialist_temperature: Optional[float] = None,
    supervisor_reasoning: Optional[str] = None,
    specialist_reasoning: Optional[str] = None,
    agent: Optional[Agent] = None,
    doc_context: Optional["DocumentContext"] = None,
    trace_context: Optional[Dict[str, str]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Run an agent with streaming output.

    This function runs either a provided agent or creates a supervisor agent
    that routes to specialized domain agents (PDF, Disease Ontology, Gene
    Curation, Chemical Ontology). It yields SSE-compatible event dictionaries.

    All agent settings (model, temperature, reasoning) are configured via
    environment variables. See config.py for available settings.

    Langfuse Tracing:
        Uses start_as_current_observation() to set the ACTIVE context. The Langfuse
        OpenAI wrapper uses OpenTelemetry context propagation to automatically
        nest all LLM calls under this parent span, creating a proper hierarchy.

    Args:
        context_messages: Ordered prompt-context messages ending with the
                          current user message
        user_id: The user's user ID for tenant isolation
        session_id: Optional chat session UUID for Langfuse trace grouping
        document_id: Optional UUID of the PDF document (enables PDF specialist)
        document_name: Optional name of the document for context
        active_groups: Optional list of group IDs (for example ["group-a", "group-b"]) for injecting
                       group-specific rules into agent prompts
        supervisor_model: Optional override for the supervisor model id
        specialist_model: Optional override for specialist model ids
        supervisor_temperature: Optional override for supervisor temperature
        specialist_temperature: Optional override for specialist temperature
        supervisor_reasoning: Optional override for supervisor reasoning level
        specialist_reasoning: Optional override for specialist reasoning level
        agent: Optional pre-built agent to use instead of creating a supervisor.
               Use this for flow execution with custom flow supervisors.
               If None, creates the standard supervisor agent.
        doc_context: Optional pre-fetched DocumentContext. If provided, avoids
                     redundant Weaviate queries. Used by flow executor for optimization.
        trace_context: Optional Langfuse trace identifiers to reuse for retries.

    Yields:
        SSE-compatible event dictionaries with types:
        - RUN_STARTED: Start of agent execution
        - AGENT_UPDATED: Agent handoff occurred
        - TOOL_CALL_START: Tool invocation started
        - TOOL_CALL_END: Tool completed with output preview
        - TEXT_MESSAGE_CONTENT: Text response delta
        - RUN_FINISHED: Agent execution complete
        - ERROR: Error occurred during execution
    """
    input_items, user_message = _normalize_context_messages(context_messages)
    doc_info = f"document {document_id[:8]}..." if document_id else "no document"
    logger.info(
        "Starting streamed run for %s",
        doc_info,
        extra={"user_id": user_id, "session_id": session_id, "query_preview": user_message[:50]},
    )

    # Clear any leftover data from previous runs
    clear_collected_events()
    clear_current_turn_curation_context()
    clear_pending_configs()  # Clear agent configs from previous requests
    reset_consecutive_call_tracker()  # Reset batching nudge tracker for new query

    # Use pre-fetched document context if provided, otherwise fetch
    # This optimization avoids redundant Weaviate queries when called from flow executor
    hierarchy = None
    abstract = None
    if doc_context is not None:
        # Use pre-fetched context (optimization path from flow executor)
        hierarchy = doc_context.hierarchy
        abstract = doc_context.abstract
        logger.debug(
            "Using pre-fetched document context: %s sections",
            doc_context.section_count(),
            extra={"user_id": user_id, "session_id": session_id},
        )
    elif document_id and user_id:
        # Fetch fresh (standard chat path)
        from src.lib.document_context import DocumentContext

        doc_context = DocumentContext.fetch(document_id, user_id, document_name)
        hierarchy = doc_context.hierarchy
        abstract = doc_context.abstract

    # Use provided agent OR create the supervisor agent with all domain specialists
    # All agent settings come from environment variables (see config.py)
    if agent is None:
        clear_prompt_context()  # Clear prompt tracking before creating new runtime agents
        agent = create_supervisor_agent(
            document_id=document_id,
            user_id=user_id,
            document_name=document_name,
            hierarchy=hierarchy,
            abstract=abstract,
            active_groups=active_groups,
            model_override=supervisor_model,
            temperature_override=supervisor_temperature,
            reasoning_override=supervisor_reasoning,
            specialist_model_override=specialist_model,
            specialist_temperature_override=specialist_temperature,
            specialist_reasoning_override=specialist_reasoning,
        )
        agent_name = agent.name
        agent_for_prompt_commit = agent
    else:
        # Custom agent provided (e.g., flow supervisor)
        agent_name = getattr(agent, 'name', 'Custom Agent')
        agent_for_prompt_commit = agent
        logger.info(
            "Using provided agent: %s",
            agent_name,
            extra={"user_id": user_id, "session_id": session_id},
        )

    # Commit pending prompts for whichever agent we're using
    # (supervisor runs immediately after creation, unlike specialists which are on-demand)
    commit_pending_prompts(agent_for_prompt_commit)

    # Generate a fallback trace ID (used when Langfuse not configured)
    doc_prefix = document_id[:8] if document_id else "nodoc"
    fallback_trace_id = f"chat-{doc_prefix}-{uuid.uuid4().hex[:8]}"

    # Get Langfuse client for tracing
    langfuse = get_langfuse()

    # hierarchy is already fetched above and passed to supervisor agent
    # Log if we'll be adding it to trace metadata
    if hierarchy:
        logger.info(
            "Adding document hierarchy to trace: %s sections",
            len(hierarchy.get("sections", [])),
            extra={"user_id": user_id, "session_id": session_id},
        )

    if langfuse:
        # Use start_as_current_observation() to SET THE ACTIVE CONTEXT
        # All OpenAI calls inside will automatically be nested under this span
        try:
            # Build trace metadata with optional hierarchy
            trace_metadata = {
                "supervisor_agent": agent.name,
                "supervisor_model": agent.model,
                "has_document": document_id is not None,
                "document_id": document_id,
                "document_name": document_name,
                "active_groups": active_groups or [],  # Group-specific rules applied to this session
            }
            if hierarchy:
                # Add hierarchy summary to metadata (full structure for trace analysis)
                trace_metadata["document_hierarchy"] = {
                    "top_level_sections": hierarchy.get("top_level_sections", []),
                    "sections": hierarchy.get("sections", []),
                    "section_count": len(hierarchy.get("sections", [])),
                }
            if abstract:
                # Add abstract info to metadata (length only, not full text)
                trace_metadata["document_abstract"] = {
                    "has_abstract": True,
                    "abstract_length": len(abstract),
                }

            # Use start_as_current_observation() to set OTEL context for the
            # OpenInference Agents SDK processor and manual application events.
            #
            # NOTE: We manually call __enter__() and __exit__() because this is an
            # async generator - we can't use a simple `with` block as the generator
            # can be suspended across yield statements. The context manager sets up
            # the OTEL span context on __enter__ and cleans it up on __exit__.
            # Create a short query preview for trace naming (first 50 chars)
            query_preview = user_message[:50] + "..." if len(user_message) > 50 else user_message
            trace_name = f"chat: {query_preview}"

            logger.info(
                "Creating trace: name=%s",
                trace_name,
                extra={"session_id": session_id, "user_id": user_id},
            )

            span_context_manager = langfuse.start_as_current_observation(
                trace_context=trace_context,
                name="chat-flow",
                as_type="span",
                input={"query": user_message, "document_id": document_id, "document_name": document_name},
                metadata=trace_metadata
            )
            root_span = span_context_manager.__enter__()
            trace_id = root_span.trace_id
            trace_final_output: Optional[Dict[str, Any]] = None
            _set_langfuse_trace_io(
                langfuse,
                root_span,
                input={"query": user_message},
            )
            trace_attribute_context_manager = None
            trace_attribute_context_active = False

            try:
                # Build trace tags - include group tags for easy filtering
                trace_tags = ["chat", "openai-agents"]
                if active_groups:
                    # Add a trace tag for each active group.
                    trace_tags.extend([f"group:{grp}" for grp in active_groups])

                # Propagate trace-level attributes onto the active span and all child spans.
                # In Langfuse v4, this replaces the removed span.update_trace(...) API.
                trace_attribute_context_manager = propagate_attributes(
                    user_id=user_id,
                    session_id=session_id,  # Group all chats for same chat session together
                    tags=trace_tags,
                    trace_name=trace_name,  # Keep the trace grouped under the chat-specific name
                )
                trace_attribute_context_manager.__enter__()
                trace_attribute_context_active = True

                # Set trace_id in context for tools (enables closure capture)
                set_current_trace_id(trace_id)
                start_extraction_trace_run(
                    trace_id=trace_id,
                    session_id=session_id,
                    user_id=user_id,
                    observation_id=getattr(root_span, "id", None),
                )

                logger.info(
                    "Trace created",
                    extra={
                        "trace_id": trace_id,
                        "session_id": session_id,
                        "user_id": user_id,
                        "tags": trace_tags,
                        "document_id": document_id,
                    },
                )

                # Flush queued agent configs to the trace as EVENT observations
                # These were collected during agent creation before trace existed
                config_count = flush_agent_configs(root_span)
                logger.info(
                    "Flushed %s agent configs to trace",
                    config_count,
                    extra={"trace_id": trace_id, "session_id": session_id, "user_id": user_id},
                )

                # Emit start event AFTER we have the Langfuse trace_id
                run_started_event = {
                    "type": "RUN_STARTED",
                    "data": {
                        "agent": agent.name,
                        "model": agent.model,
                        "document_id": document_id,
                        "trace_id": trace_id
                    }
                }
                write_stream_event(run_started_event, trace_id=trace_id, observation_id=getattr(root_span, "id", None))
                yield run_started_event
                # Audit event: SUPERVISOR_START
                supervisor_start_event = {
                    "type": "SUPERVISOR_START",
                    "timestamp": _now_iso(),
                    "details": {"message": f"Processing query with {agent.name}"}
                }
                write_stream_event(supervisor_start_event, trace_id=trace_id, observation_id=getattr(root_span, "id", None))
                yield supervisor_start_event

                try:
                    # Run agent inside the active span context
                    # All OpenAI calls will automatically be children of root_span
                    async for event in _run_agent_with_groq_retry(
                        agent=agent,
                        input_items=input_items,
                        user_id=user_id,
                        document_id=document_id,
                        document_name=document_name,
                        user_message=user_message,
                        trace_id=trace_id,
                    ):
                        # Capture completion data to update span
                        if event.get("type") == "RUN_FINISHED":
                            data = event.get("data", {})
                            trace_final_output = {"response": data.get("response", "")}
                            root_span.update(
                                output={
                                    "response": data.get("response", ""),
                                    "response_length": data.get("response_length", 0),
                                    "tool_calls": data.get("tool_calls", 0),
                                    "agents_used": data.get("agents_used", []),
                                }
                            )
                            _set_langfuse_trace_io(
                                langfuse,
                                root_span,
                                output=trace_final_output,
                            )
                            logger.info(
                                "Trace completed",
                                extra={
                                    "trace_id": trace_id,
                                    "session_id": session_id,
                                    "user_id": user_id,
                                    "response_length": data.get("response_length", 0),
                                    "tool_calls": data.get("tool_calls", 0),
                                    "agents_used": data.get("agents_used", []),
                                },
                            )
                            # Note: Prompt logging moved to finally block for guaranteed execution
                        yield event

                except SpecialistOutputError as e:
                    # Specialist failed to produce structured output after retry
                    # This is a specific error that provides clear context to the user
                    logger.error(
                        "Specialist output error: %s",
                        e,
                        extra={
                            "trace_id": trace_id,
                            "session_id": session_id,
                            "user_id": user_id,
                            "specialist_name": e.specialist_name,
                            "output_type": e.output_type_name,
                        },
                        exc_info=True
                    )
                    root_span.update(
                        output={
                            "error": str(e),
                            "error_type": "SpecialistOutputError",
                            "specialist_name": e.specialist_name,
                            "output_type": e.output_type_name,
                        },
                        level="ERROR",
                        status_message=str(e),
                        metadata={"specialist_retry_failed": True}
                    )
                    trace_final_output = {
                        "status": "error",
                        "error": str(e),
                        "error_type": "SpecialistOutputError",
                    }
                    _set_langfuse_trace_io(
                        langfuse,
                        root_span,
                        output=trace_final_output,
                    )
                    _alert_task = asyncio.create_task(
                        notify_tool_failure(
                            error_type="SpecialistOutputError",
                            error_message=str(e),
                            source="infrastructure",
                            specialist_name=e.specialist_name,
                            trace_id=trace_id,
                            session_id=session_id,
                            curator_id=user_id,
                        )
                    )
                    # Audit event: SPECIALIST_ERROR (more specific than SUPERVISOR_ERROR)
                    specialist_error_event = {
                        "type": "SPECIALIST_ERROR",
                        "timestamp": _now_iso(),
                        "details": {
                            "specialist": e.specialist_name,
                            "output_type": e.output_type_name,
                            "error": str(e),
                            "message": (
                                f"The {e.specialist_name} was unable to produce a response. "
                                f"Please report this failure using the feedback option (⋮ menu on messages) "
                                f"so we can investigate. You can also try rephrasing your question or "
                                f"breaking it into smaller parts."
                            )
                        }
                    }
                    write_stream_event(specialist_error_event, trace_id=trace_id)
                    yield specialist_error_event
                    # Note: Prompt logging moved to finally block for guaranteed execution
                    run_error_event = {
                        "type": "RUN_ERROR",
                        "data": {
                            "message": (
                                f"The {e.specialist_name} encountered an issue. "
                                f"Please report this using the feedback option (⋮ menu), then try your query again."
                            ),
                            "error_type": "SpecialistOutputError",
                            "trace_id": trace_id
                        }
                    }
                    write_stream_event(run_error_event, trace_id=trace_id)
                    yield run_error_event

                except Exception as e:
                    logger.error(
                        "Run error: %s",
                        e,
                        extra={
                            "trace_id": trace_id,
                            "session_id": session_id,
                            "user_id": user_id,
                            "error_type": type(e).__name__,
                        },
                        exc_info=True,
                    )
                    root_span.update(
                        output={"error": str(e), "error_type": type(e).__name__},
                        level="ERROR",
                        status_message=str(e)
                    )
                    trace_final_output = {
                        "status": "error",
                        "error": str(e),
                        "error_type": type(e).__name__,
                    }
                    _set_langfuse_trace_io(
                        langfuse,
                        root_span,
                        output=trace_final_output,
                    )
                    _alert_task = asyncio.create_task(
                        notify_tool_failure(
                            error_type=type(e).__name__,
                            error_message=str(e),
                            source="infrastructure",
                            specialist_name=agent.name,
                            trace_id=trace_id,
                            session_id=session_id,
                            curator_id=user_id,
                        )
                    )
                    # Audit event: SUPERVISOR_ERROR
                    supervisor_error_event = {
                        "type": "SUPERVISOR_ERROR",
                        "timestamp": _now_iso(),
                        "details": {
                            "error": str(e),
                            "context": type(e).__name__
                        }
                    }
                    write_stream_event(supervisor_error_event, trace_id=trace_id)
                    yield supervisor_error_event
                    # Note: Prompt logging moved to finally block for guaranteed execution
                    run_error_event = {
                        "type": "RUN_ERROR",
                        "data": {
                            "message": str(e),
                            "error_type": type(e).__name__,
                            "trace_id": trace_id
                        }
                    }
                    write_stream_event(run_error_event, trace_id=trace_id)
                    yield run_error_event

            finally:
                # CRITICAL: Log prompts regardless of how the generator exits (success, error, or client disconnect)
                # This ensures audit trail is complete even if client disconnects mid-stream
                _log_used_prompts_to_db(trace_id=trace_id, session_id=session_id, span=root_span)
                final_trace_io: Dict[str, Any] = {"input": {"query": user_message}}
                if trace_final_output is not None:
                    final_trace_io["output"] = trace_final_output
                _set_langfuse_trace_io(langfuse, root_span, **final_trace_io)

                # Close the attribute propagation context before the root span context.
                if trace_attribute_context_active:
                    _close_langfuse_context(
                        trace_attribute_context_manager,
                        label="Trace attribute context",
                        trace_id=trace_id,
                        session_id=session_id,
                        user_id=user_id,
                    )
                _close_langfuse_context(
                    span_context_manager,
                    label="Span context",
                    trace_id=trace_id,
                    session_id=session_id,
                    user_id=user_id,
                )
                flush_langfuse()
                clear_extraction_trace_run()
                logger.info(
                    "Flushed trace data",
                    extra={"trace_id": trace_id, "session_id": session_id, "user_id": user_id},
                )

        except Exception as e:
            logger.error(
                "Failed to create span context: %s",
                e,
                extra={"trace_id": fallback_trace_id, "session_id": session_id, "user_id": user_id},
                exc_info=True,
            )
            # Fall back to running without tracing
            # Set fallback trace_id in context for tools
            set_current_trace_id(fallback_trace_id)
            start_extraction_trace_run(
                trace_id=fallback_trace_id,
                session_id=session_id,
                user_id=user_id,
            )
            run_started_event = {
                "type": "RUN_STARTED",
                "data": {
                    "agent": agent.name,
                    "model": agent.model,
                    "document_id": document_id,
                    "trace_id": fallback_trace_id
                }
            }
            write_stream_event(run_started_event, trace_id=fallback_trace_id)
            yield run_started_event
            supervisor_start_event = {
                "type": "SUPERVISOR_START",
                "timestamp": _now_iso(),
                "details": {"message": f"Processing query with {agent.name}"}
            }
            write_stream_event(supervisor_start_event, trace_id=fallback_trace_id)
            yield supervisor_start_event
            try:
                async for event in _run_agent_with_groq_retry(
                    agent=agent,
                    input_items=input_items,
                    user_id=user_id,
                    document_id=document_id,
                    document_name=document_name,
                    user_message=user_message,
                    trace_id=fallback_trace_id,
                ):
                    yield event
            finally:
                # Guarantee prompt logging even on client disconnect
                _log_used_prompts_to_db(trace_id=fallback_trace_id, session_id=session_id)
                clear_extraction_trace_run()
    else:
        # No Langfuse configured, run without tracing
        logger.info(
            "Langfuse not configured, running without tracing",
            extra={"trace_id": fallback_trace_id, "session_id": session_id, "user_id": user_id},
        )
        # Set fallback trace_id in context for tools
        set_current_trace_id(fallback_trace_id)
        start_extraction_trace_run(
            trace_id=fallback_trace_id,
            session_id=session_id,
            user_id=user_id,
        )
        run_started_event = {
            "type": "RUN_STARTED",
            "data": {
                "agent": agent.name,
                "model": agent.model,
                "document_id": document_id,
                "trace_id": fallback_trace_id
            }
        }
        write_stream_event(run_started_event, trace_id=fallback_trace_id)
        yield run_started_event
        supervisor_start_event = {
            "type": "SUPERVISOR_START",
            "timestamp": _now_iso(),
            "details": {"message": f"Processing query with {agent.name}"}
        }
        write_stream_event(supervisor_start_event, trace_id=fallback_trace_id)
        yield supervisor_start_event
        try:
            async for event in _run_agent_with_groq_retry(
                agent=agent,
                input_items=input_items,
                user_id=user_id,
                document_id=document_id,
                document_name=document_name,
                user_message=user_message,
                trace_id=fallback_trace_id,
            ):
                yield event
        finally:
            # Guarantee prompt logging even on client disconnect
            _log_used_prompts_to_db(trace_id=fallback_trace_id, session_id=session_id)
            clear_extraction_trace_run()
