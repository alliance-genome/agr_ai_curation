"""Chat API endpoints using OpenAI Agents SDK.

This module provides chat endpoints for the AI Curation Prototype,
using the OpenAI Agents SDK for multi-agent orchestration.

Architecture:
- Supervisor agent routes queries to domain specialists
- Bidirectional handoffs enable multi-step query handling
- Specialists: PDF, Disease Ontology, Gene Curation, Chemical Ontology
"""

import base64
import binascii
import json
import logging
import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from sqlalchemy.exc import SQLAlchemyError

from .auth import get_auth_dependency
from ..lib.chat_history_repository import (
    ASSISTANT_CHAT_KIND,
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
    ChatMessageCursor,
    ChatMessageRecord,
    ChatSessionCursor,
    ChatSessionRecord,
    VALID_CHAT_KINDS,
)
from ..lib.chat_state import document_state
from ..lib.chat_transcript import (
    FLOW_SUMMARY_MESSAGE_TYPE,
    FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY,
    count_session_text_messages,
    extract_flow_assistant_message,
    list_session_text_exchanges,
)
from ..lib.chat_title_generator import (
    ChatTitleSource,
    generate_chat_title,
    normalize_generated_chat_title,
)
from ..lib.curation_workspace import (
    ExtractionEnvelopeCandidate,
    build_extraction_envelope_candidate,
    persist_extraction_results,
)
from ..lib.curation_workspace.extraction_results import get_agent_curation_metadata
from ..lib.openai_agents import run_agent_streamed
from ..lib.openai_agents.runner import normalize_context_message_role
from ..lib.openai_agents.agents.supervisor_agent import get_supervisor_tool_agent_map
from ..lib.openai_agents.evidence_summary import (
    build_record_evidence_summary_record,
    normalize_evidence_records,
)
from ..lib.flows.executor import execute_flow
from ..lib.weaviate_client.documents import get_document
from ..models.sql import CurationFlow, SessionLocal, get_db
from ..schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionSourceKind,
)
from ..schemas.flows import ExecuteFlowRequest
from ..services.user_service import set_global_user_from_cognito
from ..lib.group_rules import get_groups_from_cognito
from ..lib.redis_client import (
    set_cancel_signal,
    check_cancel_signal,
    clear_cancel_signal,
    register_active_stream,
    unregister_active_stream,
    is_stream_active,
    get_stream_owner,
)

# Context variables for file output tools
from ..lib.context import set_current_session_id, set_current_user_id

logger = logging.getLogger(__name__)

# Create router with prefix
router = APIRouter(prefix="/api")


# Request/Response models
class LoadDocumentRequest(BaseModel):
    """Request payload when selecting a document for chat."""
    document_id: str


class ActiveDocument(BaseModel):
    """Details for the currently active document in chat."""
    id: str
    filename: Optional[str] = None
    chunk_count: Optional[int] = None
    vector_count: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None


class DocumentStatusResponse(BaseModel):
    """Response model describing current chat document selection."""
    active: bool
    document: Optional[ActiveDocument] = None
    message: Optional[str] = None


class ChatMessage(BaseModel):
    """Request model for chat messages.

    Supports advanced OpenAI Agents SDK features:
    - Per-agent model selection (supervisor vs specialists)
    - Temperature control for response determinism
    - Reasoning effort for GPT-5 models (extended thinking)
    - Conversation history for multi-turn context
    """
    message: str
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    model: Optional[str] = None
    specialist_model: Optional[str] = None

    # Temperature settings (0.0=deterministic, 1.0=creative)
    supervisor_temperature: Optional[float] = None
    specialist_temperature: Optional[float] = None

    # Reasoning effort for GPT-5 models ("minimal", "low", "medium", "high")
    # Only applies when using gpt-5 family models
    supervisor_reasoning: Optional[str] = None
    specialist_reasoning: Optional[str] = None


def _build_context_messages_from_history(
    history_messages: List[Dict[str, str]],
    *,
    user_message: str,
) -> List[Dict[str, str]]:
    """Convert exchange-style history plus the current turn into runner context."""

    context_messages: List[Dict[str, str]] = []
    for index, message in enumerate(history_messages):
        role = normalize_context_message_role(message.get("role"))
        content = str(message.get("content") or "")
        if not role:
            raise ValueError(f"history_messages[{index}] is missing a role")
        if not content.strip():
            raise ValueError(f"history_messages[{index}] must include non-empty content")
        context_messages.append({"role": role, "content": content})

    context_messages.append({"role": "user", "content": user_message})
    return context_messages


def _build_context_messages_from_durable_messages(
    repository: ChatHistoryRepository,
    *,
    user_id: str,
    session_id: str,
    user_message: str,
) -> List[Dict[str, str]]:
    """Build runner context from durable rows while preserving completed-exchange semantics."""

    history_messages: List[Dict[str, str]] = []
    for durable_user_message, durable_assistant_message in list_session_text_exchanges(
        session_id=session_id,
        user_id=user_id,
        repository=repository,
    ):
        history_messages.append({"role": "user", "content": durable_user_message})
        history_messages.append({"role": "assistant", "content": durable_assistant_message})

    return _build_context_messages_from_history(
        history_messages,
        user_message=user_message,
    )


def _build_extraction_candidate_from_tool_event(
    event: Dict[str, Any],
    *,
    tool_agent_map: Dict[str, str],
    conversation_summary: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[ExtractionEnvelopeCandidate]:
    """Parse a backend-only tool-complete event into a persistable candidate."""

    if event.get("type") != "TOOL_COMPLETE":
        return None

    details = event.get("details", {}) or {}
    internal_payload = event.get("internal", {}) or {}
    tool_name = str(details.get("toolName") or "").strip()
    agent_key = tool_agent_map.get(tool_name)
    if not agent_key or not isinstance(internal_payload, dict):
        return None

    candidate_metadata = dict(metadata or {})
    candidate_metadata.setdefault("tool_name", tool_name)

    return build_extraction_envelope_candidate(
        internal_payload.get("tool_output"),
        agent_key=agent_key,
        conversation_summary=conversation_summary,
        metadata=candidate_metadata,
    )


def _extract_evidence_records(value: Any) -> List[Dict[str, Any]]:
    """Parse a value into a list of normalized evidence records."""
    return normalize_evidence_records(value)


def _build_evidence_record_from_tool_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse a record_evidence tool-complete event into an SSE-ready evidence record."""

    if event.get("type") != "TOOL_COMPLETE":
        return None

    details = event.get("details", {}) or {}
    internal_payload = event.get("internal", {}) or {}
    tool_name = str(details.get("toolName") or "").strip()

    if tool_name != "record_evidence" or not isinstance(internal_payload, dict):
        return None

    return build_record_evidence_summary_record(
        tool_name=tool_name,
        tool_input=internal_payload.get("tool_input"),
        tool_output=internal_payload.get("tool_output"),
    )


def _build_evidence_curation_metadata(
    *,
    event: Dict[str, Any],
    tool_agent_map: Dict[str, str],
) -> Dict[str, Any]:
    """Describe whether one evidence summary is launchable in Review & Curate."""

    if isinstance(event.get("curation_supported"), bool):
        payload = {
            "curation_supported": event.get("curation_supported"),
            "curation_agent_key": event.get("curation_agent_key"),
            "curation_adapter_key": event.get("curation_adapter_key"),
        }
        resolved_tool_name = str(event.get("tool_name") or "").strip()
        if resolved_tool_name:
            payload["tool_name"] = resolved_tool_name
        resolved_tool_names = [
            str(value).strip()
            for value in (event.get("tool_names") or [])
            if str(value).strip()
        ]
        if resolved_tool_names:
            payload["tool_names"] = resolved_tool_names
        return payload

    tool_names: List[str] = []
    seen_tool_names: set[str] = set()
    for value in [event.get("tool_name"), *(event.get("tool_names") or [])]:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen_tool_names:
            continue
        seen_tool_names.add(normalized)
        tool_names.append(normalized)

    if not tool_names:
        return {}

    resolved: List[tuple[str, str, Optional[str], bool]] = []
    for tool_name in tool_names:
        agent_key = tool_agent_map.get(tool_name)
        if not agent_key:
            return {}
        curation = get_agent_curation_metadata(agent_key)
        adapter_key = str((curation or {}).get("adapter_key") or "").strip() or None
        launchable = bool((curation or {}).get("launchable")) and adapter_key is not None
        resolved.append((tool_name, agent_key, adapter_key, launchable))

    launchable_entries = [
        (tool_name, agent_key, adapter_key)
        for tool_name, agent_key, adapter_key, launchable in resolved
        if launchable and adapter_key is not None
    ]
    if len(launchable_entries) == len(resolved):
        agent_keys = {agent_key for _, agent_key, _ in launchable_entries}
        adapter_keys = {adapter_key for _, _, adapter_key in launchable_entries}
        if len(agent_keys) == 1 and len(adapter_keys) == 1:
            payload = {
                "curation_supported": True,
                "curation_agent_key": next(iter(agent_keys)),
                "curation_adapter_key": next(iter(adapter_keys)),
            }
            if len(tool_names) == 1:
                payload["tool_name"] = tool_names[0]
            payload["tool_names"] = tool_names
            return payload

    payload = {
        "curation_supported": False,
        "curation_agent_key": None,
        "curation_adapter_key": None,
    }
    if len(tool_names) == 1:
        payload["tool_name"] = tool_names[0]
    payload["tool_names"] = tool_names
    return payload


def _build_candidate_evidence_curation_metadata(
    candidates: List[ExtractionEnvelopeCandidate],
) -> Dict[str, Any]:
    """Best-effort launchability metadata when evidence summary lacks tool context."""

    if not candidates:
        return {}

    agent_keys = {
        str(candidate.agent_key).strip()
        for candidate in candidates
        if str(candidate.agent_key).strip()
    }
    if len(agent_keys) != 1:
        return {}

    agent_key = next(iter(agent_keys))
    adapter_keys = {
        str(candidate.adapter_key).strip()
        for candidate in candidates
        if str(candidate.adapter_key or "").strip()
    }
    adapter_key = next(iter(adapter_keys)) if len(adapter_keys) == 1 else None

    return {
        "curation_supported": adapter_key is not None,
        "curation_agent_key": agent_key,
        "curation_adapter_key": adapter_key,
    }


def _persist_extraction_candidates(
    *,
    candidates: List[ExtractionEnvelopeCandidate],
    document_id: Optional[str],
    user_id: str,
    session_id: str,
    trace_id: Optional[str],
    source_kind: CurationExtractionSourceKind,
    flow_run_id: Optional[str] = None,
    db: Optional[Session] = None,
) -> None:
    """Persist extraction candidates and propagate failures to the caller."""

    if not candidates or not document_id:
        return

    persist_extraction_results(
        [
            CurationExtractionPersistenceRequest(
                document_id=document_id,
                adapter_key=candidate.adapter_key,
                agent_key=candidate.agent_key,
                source_kind=source_kind,
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
        ],
        db=db,
    )


_FLOW_MEMORY_MAX_VISIBLE_OUTPUT_CHARS = 2500
_FLOW_MEMORY_MAX_SPECIALIST_OUTPUTS = 8
_FLOW_MEMORY_MAX_SPECIALIST_OUTPUT_CHARS = 3500
_FLOW_MEMORY_MAX_SPECIALIST_SUMMARIES = 12
_FLOW_MEMORY_MAX_HIDDEN_JSON_CHARS = 18000
_FLOW_MEMORY_COMPACT_SPECIALIST_OUTPUT_CHARS = 800
_FLOW_TRANSCRIPT_REPLAY_RUN_STARTED_KEY = "_replay_run_started_event"
_FLOW_TRANSCRIPT_REPLAY_TERMINAL_EVENTS_KEY = "_replay_terminal_events"
_FLOW_TRANSCRIPT_INTERNAL_PAYLOAD_KEYS = frozenset(
    {
        FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY,
        _FLOW_TRANSCRIPT_REPLAY_RUN_STARTED_KEY,
        _FLOW_TRANSCRIPT_REPLAY_TERMINAL_EVENTS_KEY,
    }
)
_EXECUTE_FLOW_RUNTIME_STATE_KEY = "_execute_flow_runtime_state"
_EXECUTE_FLOW_RUNTIME_FLOW_RUN_ID_KEY = "flow_run_id"
_EXECUTE_FLOW_RUNTIME_TRACE_ID_KEY = "trace_id"


def _extract_execute_flow_runtime_identifiers(
    payload_json: Dict[str, Any] | List[Any] | None,
) -> tuple[str | None, str | None]:
    """Read persisted execute-flow runtime identifiers from a durable user row."""

    if not isinstance(payload_json, dict):
        return None, None

    runtime_state = payload_json.get(_EXECUTE_FLOW_RUNTIME_STATE_KEY)
    if not isinstance(runtime_state, dict):
        return None, None

    flow_run_id = str(
        runtime_state.get(_EXECUTE_FLOW_RUNTIME_FLOW_RUN_ID_KEY) or ""
    ).strip() or None
    trace_id = str(
        runtime_state.get(_EXECUTE_FLOW_RUNTIME_TRACE_ID_KEY) or ""
    ).strip() or None
    return flow_run_id, trace_id


def _build_execute_flow_runtime_payload(
    payload_json: Dict[str, Any] | List[Any] | None,
    *,
    flow_run_id: str,
    trace_id: Optional[str],
) -> Dict[str, Any]:
    """Merge execute-flow runtime identifiers into a durable user-turn payload."""

    next_payload = dict(payload_json) if isinstance(payload_json, dict) else {}
    runtime_state: Dict[str, Any] = {
        _EXECUTE_FLOW_RUNTIME_FLOW_RUN_ID_KEY: flow_run_id,
    }
    normalized_trace_id = str(trace_id or "").strip() or None
    if normalized_trace_id is not None:
        runtime_state[_EXECUTE_FLOW_RUNTIME_TRACE_ID_KEY] = normalized_trace_id
    next_payload[_EXECUTE_FLOW_RUNTIME_STATE_KEY] = runtime_state
    return next_payload


def _truncate_text(value: Any, max_chars: int) -> str:
    """Convert to string and truncate with deterministic suffix when needed."""
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    overflow = len(text) - max_chars
    return f"{text[:max_chars]}... [truncated {overflow} chars]"


def _dedupe_preserve_order(values: List[str]) -> List[str]:
    """Return unique strings while preserving insertion order."""
    seen = set()
    ordered: List[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _serialize_hidden_flow_payload(payload: Dict[str, Any], max_chars: int) -> str:
    """Serialize hidden payload and compact it as needed while preserving valid JSON."""
    serialized = json.dumps(payload, default=str, ensure_ascii=True)
    if len(serialized) <= max_chars:
        return serialized

    compact_payload = dict(payload)
    compact_payload["truncated"] = True
    compact_payload["truncation_notice"] = "Hidden flow context compacted to fit memory budget."

    # Drop lower-priority collections first.
    for key in ("intermediate_specialist_summaries", "domain_warnings", "files"):
        if compact_payload.get(key):
            compact_payload[key] = []
            serialized = json.dumps(compact_payload, default=str, ensure_ascii=True)
            if len(serialized) <= max_chars:
                return serialized

    # Keep at most one specialist output and tighten output text.
    specialist_outputs = list(compact_payload.get("specialist_outputs") or [])
    if specialist_outputs:
        first_output = dict(specialist_outputs[0])
        first_output["output"] = _truncate_text(
            first_output.get("output"),
            _FLOW_MEMORY_COMPACT_SPECIALIST_OUTPUT_CHARS,
        )
        compact_payload["specialist_outputs"] = [first_output]
        serialized = json.dumps(compact_payload, default=str, ensure_ascii=True)
        if len(serialized) <= max_chars:
            return serialized

    flow_payload = compact_payload.get("flow") or {}
    minimal_payload = {
        "flow": {
            "flow_id": _truncate_text(flow_payload.get("flow_id"), 128),
            "flow_name": _truncate_text(flow_payload.get("flow_name"), 128),
            "session_id": _truncate_text(flow_payload.get("session_id"), 128),
            "status": _truncate_text(flow_payload.get("status"), 64),
            "trace_id": _truncate_text(flow_payload.get("trace_id"), 128),
            "failure_reason": _truncate_text(flow_payload.get("failure_reason"), 512),
        },
        "truncated": True,
        "truncation_notice": "Hidden flow context exceeded size limit and was reduced.",
    }
    serialized = json.dumps(minimal_payload, default=str, ensure_ascii=True)
    if len(serialized) <= max_chars:
        return serialized

    return json.dumps({"truncated": True}, ensure_ascii=True)


def _build_flow_memory_assistant_message(
    *,
    flow_name: str,
    flow_id: str,
    session_id: str,
    status: str,
    trace_id: Optional[str],
    final_user_output: Optional[str],
    agents_used: List[str],
    specialist_outputs: List[Dict[str, Any]],
    specialist_summaries: List[Dict[str, Any]],
    domain_warnings: List[Dict[str, Any]],
    file_outputs: List[Dict[str, Any]],
    failure_reason: Optional[str],
) -> str:
    """Build a flow execution context message for follow-up chat grounding."""
    agents = _dedupe_preserve_order([str(agent) for agent in agents_used if agent])
    visible_output = _truncate_text(final_user_output or "", _FLOW_MEMORY_MAX_VISIBLE_OUTPUT_CHARS)

    bounded_outputs: List[Dict[str, Any]] = []
    for output in specialist_outputs[:_FLOW_MEMORY_MAX_SPECIALIST_OUTPUTS]:
        bounded_outputs.append({
            "tool": output.get("tool"),
            "output_length": output.get("output_length"),
            "output": _truncate_text(output.get("output"), _FLOW_MEMORY_MAX_SPECIALIST_OUTPUT_CHARS),
        })

    hidden_payload = {
        "flow": {
            "flow_id": flow_id,
            "flow_name": flow_name,
            "session_id": session_id,
            "status": status,
            "trace_id": trace_id,
            "failure_reason": failure_reason,
        },
        "specialist_outputs": bounded_outputs,
        "intermediate_specialist_summaries": specialist_summaries[:_FLOW_MEMORY_MAX_SPECIALIST_SUMMARIES],
        "domain_warnings": domain_warnings,
        "files": file_outputs,
    }
    hidden_json = _serialize_hidden_flow_payload(hidden_payload, _FLOW_MEMORY_MAX_HIDDEN_JSON_CHARS)

    agents_line = ", ".join(agents) if agents else "Unknown"
    if visible_output:
        final_output_block = visible_output
    elif status == "failed":
        final_output_block = (
            "Flow failed before producing a final output. "
            f"Reason: {_format_flow_failure_reason(failure_reason)}"
        )
    else:
        final_output_block = "No final user-visible output was emitted."

    return (
        "Flow execution summary for follow-up questions:\n"
        f"- Flow: {flow_name} ({flow_id})\n"
        f"- Status: {status}\n"
        f"- Session: {session_id}\n"
        f"- Trace ID: {trace_id or 'n/a'}\n"
        f"- Agents involved: {agents_line}\n"
        "- Final user-visible output:\n"
        f"{final_output_block}\n\n"
        "Hidden flow context (internal grounding data; not user-visible output):\n"
        "<FLOW_INTERNAL_CONTEXT_JSON>\n"
        f"{hidden_json}\n"
        "</FLOW_INTERNAL_CONTEXT_JSON>"
    )


def _format_flow_failure_reason(failure_reason: Optional[str]) -> str:
    """Render a failed flow reason without masking missing or blank values."""

    normalized = failure_reason.strip() if isinstance(failure_reason, str) else None
    if normalized:
        return normalized
    return repr(failure_reason)


def _parse_event_created_at(value: Any) -> datetime | None:
    """Return a datetime when an optional SSE timestamp string parses cleanly."""

    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None

    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_execute_flow_summary_content(
    *,
    status: str,
    final_user_output: Optional[str],
    failure_reason: Optional[str],
) -> str:
    """Build the user-visible durable transcript content for one completed flow turn."""

    visible_output = str(final_user_output or "").strip()
    if visible_output:
        return visible_output
    if status == "failed":
        return (
            "Flow failed before producing a final output. "
            f"Reason: {_format_flow_failure_reason(failure_reason)}"
        )
    return "No final user-visible output was emitted."


def _build_execute_flow_transcript_row_from_event(
    event_payload: Dict[str, Any],
) -> "ExecuteFlowTranscriptRow | None":
    """Convert one replayable SSE payload into a durable execute-flow transcript row."""

    event_type = str(event_payload.get("type") or "").strip()
    details = event_payload.get("details", {}) or {}
    trace_id = str(event_payload.get("trace_id") or "").strip() or None
    created_at = _parse_event_created_at(event_payload.get("timestamp"))

    if event_type == "DOMAIN_WARNING":
        warning_message = details.get("message") or event_payload.get("message")
        content = warning_message.strip() if isinstance(warning_message, str) else ""
        if not content:
            content = "Flow warning event missing message payload."
        return ExecuteFlowTranscriptRow(
            content=content,
            message_type="text",
            payload_json=dict(event_payload),
            trace_id=trace_id,
            created_at=created_at,
        )

    if event_type == "FLOW_STEP_EVIDENCE":
        step = event_payload.get("step")
        evidence_count = event_payload.get("evidence_count")
        if isinstance(step, int) and isinstance(evidence_count, int):
            quote_label = "quote" if evidence_count == 1 else "quotes"
            content = f"Flow step {step} captured {evidence_count} evidence {quote_label}."
        else:
            content = "Flow step evidence event missing integer step/evidence_count metadata."
        return ExecuteFlowTranscriptRow(
            content=content,
            message_type="flow_step_evidence",
            payload_json=dict(event_payload),
            trace_id=trace_id,
            created_at=created_at,
        )

    if event_type == "FILE_READY":
        filename_value = details.get("filename") or event_payload.get("filename")
        filename = filename_value.strip() if isinstance(filename_value, str) else ""
        content = f"Generated file: {filename}" if filename else "Generated file event missing filename metadata."
        return ExecuteFlowTranscriptRow(
            content=content,
            message_type="file_download",
            payload_json=dict(event_payload),
            trace_id=trace_id,
            created_at=created_at,
        )

    return None


def _build_execute_flow_summary_row(
    *,
    flow_id: str,
    flow_name: str,
    flow_run_id: Optional[str],
    session_id: str,
    document_id: Optional[str],
    status: str,
    trace_id: Optional[str],
    final_user_output: Optional[str],
    failure_reason: Optional[str],
    assistant_message: str,
    run_started_event: Optional[Dict[str, Any]],
    terminal_events: List[Dict[str, Any]],
) -> "ExecuteFlowTranscriptRow":
    """Build the final durable flow summary row used for replay and follow-up grounding."""

    payload_json: Dict[str, Any] = {
        "flow_id": flow_id,
        "flow_name": flow_name,
        "flow_run_id": flow_run_id,
        "session_id": session_id,
        "document_id": document_id,
        "status": status,
        "trace_id": trace_id,
        "failure_reason": failure_reason,
        "final_user_output": str(final_user_output or "").strip() or None,
        FLOW_TRANSCRIPT_ASSISTANT_MESSAGE_KEY: assistant_message,
        _FLOW_TRANSCRIPT_REPLAY_TERMINAL_EVENTS_KEY: [
            dict(event)
            for event in terminal_events
            if isinstance(event, dict) and isinstance(event.get("type"), str)
        ],
    }
    if isinstance(run_started_event, dict) and isinstance(run_started_event.get("type"), str):
        payload_json[_FLOW_TRANSCRIPT_REPLAY_RUN_STARTED_KEY] = dict(run_started_event)

    created_at = None
    for candidate in [*terminal_events[::-1], run_started_event]:
        if not isinstance(candidate, dict):
            continue
        created_at = _parse_event_created_at(candidate.get("timestamp"))
        if created_at is not None:
            break

    return ExecuteFlowTranscriptRow(
        content=_build_execute_flow_summary_content(
            status=status,
            final_user_output=final_user_output,
            failure_reason=failure_reason,
        ),
        message_type=FLOW_SUMMARY_MESSAGE_TYPE,
        payload_json=payload_json,
        trace_id=trace_id,
        created_at=created_at,
    )


def _build_execute_flow_turn_replay(
    messages: List[ChatMessageRecord],
) -> tuple[List[Dict[str, Any]], str] | None:
    """Return replayable SSE payloads plus assistant flow memory for a completed durable turn."""

    summary_message: ChatMessageRecord | None = None
    assistant_message: str | None = None
    for message in reversed(messages):
        assistant_candidate = extract_flow_assistant_message(message)
        if assistant_candidate is None:
            continue
        summary_message = message
        assistant_message = assistant_candidate
        break

    if summary_message is None or assistant_message is None:
        return None

    replay_events: List[Dict[str, Any]] = []
    summary_payload = summary_message.payload_json if isinstance(summary_message.payload_json, dict) else {}
    run_started_event = summary_payload.get(_FLOW_TRANSCRIPT_REPLAY_RUN_STARTED_KEY)
    if isinstance(run_started_event, dict) and isinstance(run_started_event.get("type"), str):
        replay_events.append(dict(run_started_event))

    for message in messages:
        if message.message_id == summary_message.message_id:
            continue
        if not isinstance(message.payload_json, dict):
            continue
        event_type = message.payload_json.get("type")
        if not isinstance(event_type, str) or not event_type.strip():
            continue
        replay_events.append(dict(message.payload_json))

    terminal_events = summary_payload.get(_FLOW_TRANSCRIPT_REPLAY_TERMINAL_EVENTS_KEY) or []
    if isinstance(terminal_events, list):
        replay_events.extend(
            dict(event)
            for event in terminal_events
            if isinstance(event, dict) and isinstance(event.get("type"), str)
        )

    return replay_events, assistant_message


class ChatResponse(BaseModel):
    """Response model for non-streaming chat."""
    response: str
    session_id: str


class SessionResponse(BaseModel):
    """Response model for durable session creation."""
    session_id: str
    created_at: datetime
    updated_at: datetime
    title: Optional[str] = None
    active_document_id: Optional[str] = None
    active_document: Optional[ActiveDocument] = None


class CreateSessionRequest(BaseModel):
    """Request payload for durable session creation."""

    chat_kind: Literal["assistant_chat", "agent_studio"]


class ChatSessionSummaryResponse(BaseModel):
    """Compact session payload for history browsing and mutations."""

    session_id: str
    chat_kind: str
    title: Optional[str] = None
    active_document_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_message_at: Optional[datetime] = None
    recent_activity_at: datetime


class ChatSessionListResponse(BaseModel):
    """Paginated durable history response."""

    chat_kind: str
    total_sessions: int
    limit: int
    query: Optional[str] = None
    document_id: Optional[str] = None
    next_cursor: Optional[str] = None
    sessions: List[ChatSessionSummaryResponse]


class ChatSessionMessageResponse(BaseModel):
    """Transcript row returned by durable session detail responses."""

    message_id: str
    session_id: str
    chat_kind: str
    turn_id: Optional[str] = None
    role: str
    message_type: str
    content: str
    payload_json: Optional[Dict[str, Any] | List[Any]] = None
    trace_id: Optional[str] = None
    created_at: datetime


class ChatSessionDetailResponse(BaseModel):
    """Durable session detail response for resume flows."""

    session: ChatSessionSummaryResponse
    active_document: Optional[ActiveDocument] = None
    messages: List[ChatSessionMessageResponse]
    message_limit: int
    next_message_cursor: Optional[str] = None


class RenameSessionRequest(BaseModel):
    """Rename payload for one durable chat session."""

    title: str = Field(..., max_length=255)


class RenameSessionResponse(BaseModel):
    """Response payload for one renamed durable session."""

    session: ChatSessionSummaryResponse


class BulkDeleteSessionsRequest(BaseModel):
    """Bulk soft-delete payload for durable chat sessions."""

    session_ids: List[str] = Field(..., min_length=1, max_length=100)


class BulkDeleteSessionsResponse(BaseModel):
    """Bulk soft-delete outcome for durable chat sessions."""

    requested_count: int
    deleted_count: int
    deleted_session_ids: List[str]


class ConversationStatusResponse(BaseModel):
    """Response model for conversation status."""
    is_active: bool
    conversation_id: Optional[str]
    memory_stats: Optional[Dict[str, Any]]
    message: str


class ConversationResetResponse(BaseModel):
    """Response model for conversation reset."""
    success: bool
    message: str
    memory_stats: Optional[Dict[str, Any]]
    session_id: Optional[str] = None


class StopRequest(BaseModel):
    """Request model for stopping a chat stream."""
    session_id: str


class AssistantRescueRequest(BaseModel):
    """Payload used to backfill a durable assistant row; same-turn retries must match."""

    turn_id: str
    content: str
    trace_id: Optional[str] = None


class AssistantRescueResponse(BaseModel):
    """Outcome of an assistant-rescue write."""

    session_id: str
    turn_id: str
    created: bool
    trace_id: Optional[str] = None


def _assistant_rescue_conflicting_fields(
    *,
    existing_turn: ChatMessageRecord,
    content: str,
    trace_id: Optional[str],
) -> list[str]:
    """List request fields that disagree with an existing rescued assistant turn."""

    conflicting_fields: list[str] = []
    if existing_turn.content != content:
        conflicting_fields.append("content")

    normalized_trace_id = str(trace_id or "").strip() or None
    if existing_turn.trace_id != normalized_trace_id:
        conflicting_fields.append("trace_id")

    return conflicting_fields


@dataclass(frozen=True)
class PreparedChatStreamTurn:
    """Durable stream state prepared before the runner starts."""

    turn_id: str
    effective_user_message: str
    context_messages: List[Dict[str, str]]
    replay_assistant_turn: ChatMessageRecord | None = None


@dataclass(frozen=True)
class PreparedExecuteFlowTurn:
    """Durable execute-flow state prepared before the flow runner starts."""

    turn_id: str
    flow_run_id: str
    effective_user_message: str
    replay_events: List[Dict[str, Any]]
    replay_assistant_message: str | None = None
    resume_trace_id: str | None = None


@dataclass(frozen=True)
class ExecuteFlowTranscriptRow:
    """One durable transcript row captured from execute-flow output."""

    content: str
    message_type: str
    payload_json: Dict[str, Any] | List[Any] | None = None
    trace_id: str | None = None
    created_at: datetime | None = None


class ChatStreamAssistantSaveFailedError(RuntimeError):
    """Raised when a completed stream only needs the assistant row to be rescued."""


# Local fallback for cancel events (used alongside Redis for immediate in-process cancellation)
# Redis provides cross-worker cancellation; this provides immediate same-worker cancellation
_LOCAL_CANCEL_EVENTS: Dict[str, asyncio.Event] = {}
_LOCAL_SESSION_OWNERS: Dict[str, str] = {}
_LOCAL_NON_STREAM_TURN_OWNERS: Dict[str, str] = {}
_TITLE_BACKFILL_MESSAGE_LIMIT = 20


@dataclass
class _ActiveStreamLifecycle:
    """Shared ownership and cleanup bookkeeping for one active stream."""

    session_id: str
    user_id: str
    stream_token: str
    cancel_event: asyncio.Event
    cleanup_done: bool = False

    async def cleanup(self, target_session_id: str | None = None) -> None:
        """Release active stream ownership once, even if multiple paths call cleanup."""

        session_id = target_session_id if target_session_id is not None else self.session_id
        if self.cleanup_done:
            return

        self.cleanup_done = True
        _LOCAL_CANCEL_EVENTS.pop(session_id, None)
        if _LOCAL_SESSION_OWNERS.get(session_id) == self.user_id:
            _LOCAL_SESSION_OWNERS.pop(session_id, None)

        await unregister_active_stream(
            session_id,
            user_id=self.user_id,
            stream_token=self.stream_token,
        )
        await clear_cancel_signal(session_id)

    async def finalize(self, generated_title_candidate: str | None) -> None:
        """Release stream bookkeeping, then backfill a durable title."""

        await self.cleanup()
        await asyncio.to_thread(
            _backfill_chat_session_generated_title,
            self.session_id,
            self.user_id,
            generated_title_candidate,
        )

    def background_task(
        self,
        generated_title_getter: Callable[[], str | None],
    ) -> BackgroundTask:
        """Return the shared background finalize task for SSE responses."""

        return BackgroundTask(self._finalize_with_title_getter, generated_title_getter)

    async def _finalize_with_title_getter(
        self,
        generated_title_getter: Callable[[], str | None],
    ) -> None:
        await self.finalize(generated_title_getter())


async def _claim_active_stream_lifecycle(
    *,
    session_id: str,
    user_id: str,
) -> _ActiveStreamLifecycle:
    """Claim local and cross-worker stream ownership or raise an HTTP error."""

    existing_owner = _LOCAL_SESSION_OWNERS.get(session_id)
    if existing_owner:
        if existing_owner != user_id:
            raise HTTPException(status_code=403, detail="Session is active for a different user")
        raise HTTPException(status_code=409, detail="Session is already active")

    stream_token = str(uuid.uuid4())
    cancel_event = asyncio.Event()
    _LOCAL_SESSION_OWNERS[session_id] = user_id
    _LOCAL_CANCEL_EVENTS[session_id] = cancel_event

    if not await register_active_stream(session_id, user_id=user_id, stream_token=stream_token):
        _LOCAL_CANCEL_EVENTS.pop(session_id, None)
        _LOCAL_SESSION_OWNERS.pop(session_id, None)
        raise HTTPException(status_code=403, detail="Session is active for a different user")

    return _ActiveStreamLifecycle(
        session_id=session_id,
        user_id=user_id,
        stream_token=stream_token,
        cancel_event=cancel_event,
    )


def _build_active_document(document_payload: Dict[str, Any]) -> ActiveDocument:
    """Convert stored document payload to response model."""
    return ActiveDocument(
        id=str(document_payload.get("id") or ""),
        filename=document_payload.get("filename"),
        chunk_count=document_payload.get("chunk_count") or document_payload.get("chunkCount"),
        vector_count=document_payload.get("vector_count") or document_payload.get("vectorCount"),
        metadata=document_payload.get("metadata") if isinstance(document_payload.get("metadata"), dict) else None,
    )


def _require_user_sub(user: Dict[str, Any]) -> str:
    """Return the authenticated user subject or raise 401."""

    user_id = str(user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")
    return user_id


def _get_chat_history_repository(db: Session) -> ChatHistoryRepository:
    """Construct the durable chat history repository for one request."""

    return ChatHistoryRepository(db)


def _latest_visible_chat_session(
    repository: ChatHistoryRepository,
    *,
    user_id: str,
) -> ChatSessionRecord | None:
    """Return the most recent visible durable chat session for the user."""

    page = repository.list_sessions(
        user_auth_sub=user_id,
        chat_kind=ASSISTANT_CHAT_KIND,
        limit=1,
    )
    if not page.items:
        return None
    return page.items[0]


def _build_durable_conversation_stats(
    repository: ChatHistoryRepository,
    *,
    user_id: str,
    current_session: ChatSessionRecord | None = None,
) -> dict[str, Any]:
    """Build the conversation-status payload from durable chat rows."""

    session = current_session or _latest_visible_chat_session(repository, user_id=user_id)
    exchange_count = 0
    conversation_id = None

    if session is not None:
        conversation_id = session.session_id
        exchange_count = count_session_text_messages(
            session_id=session.session_id,
            user_id=user_id,
            repository=repository,
        ) // 2

    return {
        "conversation_id": conversation_id,
        "memory_sizes": {
            "short_term": {
                "file_count": exchange_count,
                "size_bytes": 0,
                "size_mb": 0,
            },
        },
        "user_id": user_id,
        "session_count": repository.count_sessions(
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
        ),
    }


def _active_document_uuid_from_state(user_id: str) -> UUID | None:
    """Return the currently loaded document UUID for the authenticated user."""

    active_document = document_state.get_document(user_id)
    if not isinstance(active_document, dict):
        return None

    raw_document_id = str(active_document.get("id") or "").strip()
    if not raw_document_id:
        return None

    try:
        return UUID(raw_document_id)
    except ValueError:
        logger.warning(
            "Ignoring invalid active document id while creating durable chat session",
            extra={"user_id": user_id, "document_id": raw_document_id},
        )
        return None


def _parse_document_filter(document_id: Optional[str]) -> UUID | None:
    """Parse an optional document filter into a UUID or raise 400."""

    if document_id is None:
        return None

    normalized_document_id = document_id.strip()
    if not normalized_document_id:
        raise HTTPException(status_code=400, detail="document_id cannot be blank")

    try:
        return UUID(normalized_document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="document_id must be a valid UUID") from exc


def _encode_cursor(payload: Dict[str, str]) -> str:
    """Encode a pagination cursor into a URL-safe opaque token."""

    raw_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw_payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: Optional[str], *, kind: str) -> Dict[str, str] | None:
    """Decode a pagination cursor or raise 400 when malformed."""

    if cursor is None:
        return None

    normalized_cursor = cursor.strip()
    if not normalized_cursor:
        raise HTTPException(status_code=400, detail=f"{kind} cursor cannot be blank")

    padding = "=" * (-len(normalized_cursor) % 4)
    try:
        decoded_bytes = base64.urlsafe_b64decode(f"{normalized_cursor}{padding}")
        payload = json.loads(decoded_bytes.decode("utf-8"))
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {kind} cursor") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail=f"Invalid {kind} cursor")
    return {str(key): str(value) for key, value in payload.items()}


def _decode_session_cursor(cursor: Optional[str]) -> ChatSessionCursor | None:
    """Decode an opaque history cursor into the repository representation."""

    payload = _decode_cursor(cursor, kind="session")
    if payload is None:
        return None

    session_id = payload.get("session_id", "").strip()
    recent_activity_at = payload.get("recent_activity_at", "").strip()
    if not session_id or not recent_activity_at:
        raise HTTPException(status_code=400, detail="Invalid session cursor")

    try:
        return ChatSessionCursor(
            session_id=session_id,
            recent_activity_at=datetime.fromisoformat(recent_activity_at),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid session cursor") from exc


def _encode_session_cursor(cursor: ChatSessionCursor | None) -> str | None:
    """Encode one repository session cursor for API responses."""

    if cursor is None:
        return None

    return _encode_cursor(
        {
            "recent_activity_at": cursor.recent_activity_at.isoformat(),
            "session_id": cursor.session_id,
        }
    )


def _decode_message_cursor(cursor: Optional[str]) -> ChatMessageCursor | None:
    """Decode an opaque message cursor into the repository representation."""

    payload = _decode_cursor(cursor, kind="message")
    if payload is None:
        return None

    message_id = payload.get("message_id", "").strip()
    created_at = payload.get("created_at", "").strip()
    if not message_id or not created_at:
        raise HTTPException(status_code=400, detail="Invalid message cursor")

    try:
        return ChatMessageCursor(
            created_at=datetime.fromisoformat(created_at),
            message_id=UUID(message_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid message cursor") from exc


def _encode_message_cursor(cursor: ChatMessageCursor | None) -> str | None:
    """Encode one repository message cursor for API responses."""

    if cursor is None:
        return None

    return _encode_cursor(
        {
            "created_at": cursor.created_at.isoformat(),
            "message_id": str(cursor.message_id),
        }
    )


def _build_title_sources_from_messages(
    messages: List[ChatMessageRecord],
) -> List[ChatTitleSource]:
    """Convert transcript rows into ordered title-candidate snippets."""

    title_sources: List[ChatTitleSource] = []
    for message in messages:
        normalized_content = (message.content or "").strip()
        if message.role == "flow":
            assistant_message = extract_flow_assistant_message(message)
            if assistant_message:
                title_sources.append(ChatTitleSource(role="assistant", content=assistant_message))
            if normalized_content:
                title_sources.append(ChatTitleSource(role="flow", content=normalized_content))
            continue

        if normalized_content:
            title_sources.append(ChatTitleSource(role=message.role, content=normalized_content))
    return title_sources


def _generate_title_from_turn(
    *,
    user_message: Optional[str],
    assistant_message: Optional[str] = None,
) -> str | None:
    """Build one title candidate from the just-completed exchange."""

    title_sources: List[ChatTitleSource] = []
    if isinstance(user_message, str) and user_message.strip():
        title_sources.append(ChatTitleSource(role="user", content=user_message))
    if isinstance(assistant_message, str) and assistant_message.strip():
        title_sources.append(ChatTitleSource(role="assistant", content=assistant_message))
    return generate_chat_title(title_sources)


def _generate_title_from_messages(
    messages: List[ChatMessageRecord],
) -> str | None:
    """Build one title candidate from persisted transcript rows."""

    return generate_chat_title(_build_title_sources_from_messages(messages))


def _require_persisted_session_chat_kind(
    chat_kind: str | None,
    *,
    session_id: str,
    operation: str,
) -> str:
    """Validate persisted session chat kind before downstream use."""

    normalized_chat_kind = chat_kind.strip() if isinstance(chat_kind, str) else None
    if not normalized_chat_kind:
        raise ValueError(
            f"Session {session_id} is missing chat_kind during {operation}"
        )
    if normalized_chat_kind not in VALID_CHAT_KINDS:
        raise ValueError(
            f"Session {session_id} has invalid chat_kind {normalized_chat_kind!r} during {operation}"
        )
    return normalized_chat_kind


def _backfill_chat_session_generated_title(
    session_id: str,
    user_id: str,
    preferred_generated_title: str | None = None,
) -> None:
    """Persist one generated title using a fresh SQL session."""

    completion_db = SessionLocal()
    try:
        repository = _get_chat_history_repository(completion_db)
        session = repository.get_session(
            session_id=session_id,
            user_auth_sub=user_id,
        )
        if session is None or session.effective_title is not None:
            return
        session_chat_kind = _require_persisted_session_chat_kind(
            session.chat_kind,
            session_id=session_id,
            operation="durable title backfill",
        )

        generated_title = normalize_generated_chat_title(preferred_generated_title)
        if generated_title is None:
            message_page = repository.list_messages(
                session_id=session_id,
                user_auth_sub=user_id,
                chat_kind=session_chat_kind,
                limit=_TITLE_BACKFILL_MESSAGE_LIMIT,
            )
            generated_title = _generate_title_from_messages(message_page.items)
        if generated_title is None:
            return

        repository.set_generated_title(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=session_chat_kind,
            generated_title=generated_title,
        )
        completion_db.commit()
    except ChatHistorySessionNotFoundError:
        completion_db.rollback()
        logger.info(
            "Skipping durable chat title backfill because session is no longer available",
            extra={"session_id": session_id, "user_id": user_id},
        )
    except (SQLAlchemyError, ValueError):
        completion_db.rollback()
        logger.warning(
            "Failed to generate durable chat title",
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
    finally:
        completion_db.close()


def _queue_chat_title_backfill(
    background_tasks: BackgroundTasks | None,
    *,
    session_id: str,
    user_id: str,
    preferred_generated_title: str | None = None,
) -> None:
    """Schedule one best-effort background title backfill."""

    if background_tasks is None:
        logger.warning(
            "Skipping title backfill because background tasks are unavailable",
            extra={"session_id": session_id, "user_id": user_id},
        )
        return

    background_tasks.add_task(
        _backfill_chat_session_generated_title,
        session_id,
        user_id,
        preferred_generated_title,
    )


def _serialize_session(
    record: ChatSessionRecord,
    *,
    title_override: str | None = None,
) -> ChatSessionSummaryResponse:
    """Convert a repository session record into the API summary payload."""

    effective_title = title_override
    if effective_title is None:
        effective_title = record.effective_title

    return ChatSessionSummaryResponse(
        session_id=record.session_id,
        chat_kind=_require_persisted_session_chat_kind(
            record.chat_kind,
            session_id=record.session_id,
            operation="session serialization",
        ),
        title=effective_title,
        active_document_id=str(record.active_document_id) if record.active_document_id else None,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_message_at=record.last_message_at,
        recent_activity_at=record.recent_activity_at,
    )


def _serialize_message(record: ChatMessageRecord) -> ChatSessionMessageResponse:
    """Convert a repository message row into the API detail payload."""

    payload_json: Dict[str, Any] | List[Any] | None = None
    if isinstance(record.payload_json, dict):
        payload_json = {
            key: value
            for key, value in record.payload_json.items()
            if not (
                (record.role == "flow" and key in _FLOW_TRANSCRIPT_INTERNAL_PAYLOAD_KEYS)
                or key == _EXECUTE_FLOW_RUNTIME_STATE_KEY
            )
        }
    elif isinstance(record.payload_json, list):
        payload_json = list(record.payload_json)

    return ChatSessionMessageResponse(
        message_id=str(record.message_id),
        session_id=record.session_id,
        chat_kind=record.chat_kind,
        turn_id=record.turn_id,
        role=record.role,
        message_type=record.message_type,
        content=record.content,
        payload_json=payload_json,
        trace_id=record.trace_id,
        created_at=record.created_at,
    )


def _resolve_session_create_active_document(
    *,
    repository: ChatHistoryRepository,
    user_id: str,
) -> tuple[UUID | None, ActiveDocument | None]:
    """Resolve the loaded chat document into a durable, user-visible session reference."""

    active_document_payload = document_state.get_document(user_id)
    active_document = (
        _build_active_document(active_document_payload)
        if isinstance(active_document_payload, dict) and active_document_payload.get("id")
        else None
    )

    active_document_id = _active_document_uuid_from_state(user_id)
    if active_document_id is None:
        return None, None

    visible_document_id = repository.get_visible_document_id(
        document_id=active_document_id,
        user_auth_sub=user_id,
    )
    if visible_document_id is None:
        logger.warning(
            "Ignoring unavailable active document while creating durable chat session",
            extra={"user_id": user_id, "document_id": str(active_document_id)},
        )
        return None, None

    return visible_document_id, active_document


async def _load_session_active_document(
    *,
    user_id: str,
    active_document_id: UUID | None,
) -> ActiveDocument | None:
    """Hydrate active document metadata for durable session detail."""

    if active_document_id is None:
        return None

    try:
        document_detail = await get_document(user_id, str(active_document_id))
    except HTTPException as exc:
        if exc.status_code in {403, 404}:
            logger.warning(
                "Active document %s is no longer available for chat session resume",
                active_document_id,
                extra={"user_id": user_id, "document_id": str(active_document_id)},
            )
            return None
        raise
    except ValueError as exc:
        if str(exc) == f"Document {active_document_id} not found":
            logger.warning(
                "Active document %s is no longer available for chat session resume",
                active_document_id,
                extra={"user_id": user_id, "document_id": str(active_document_id)},
            )
            return None
        raise

    document_payload = document_detail.get("document")
    if not isinstance(document_payload, dict):
        return None
    return _build_active_document(document_payload)


def _rollback_and_raise(db: Session, *, status_code: int, detail: str, exc: Exception) -> None:
    """Rollback the current transaction and raise an HTTP exception."""

    db.rollback()
    raise HTTPException(status_code=status_code, detail=detail) from exc


def _stream_event_payload(
    event_type: str,
    *,
    session_id: str,
    turn_id: str,
    trace_id: Optional[str] = None,
    **payload: Any,
) -> Dict[str, Any]:
    """Build one SSE payload with consistent stream/session identifiers."""

    event_payload: Dict[str, Any] = {
        "type": event_type,
        "session_id": session_id,
        "turn_id": turn_id,
    }
    if trace_id:
        event_payload["trace_id"] = trace_id
    event_payload.update(payload)
    return event_payload


def _stream_event_sse(event_payload: Dict[str, Any]) -> str:
    """Serialize one SSE payload."""

    return f"data: {json.dumps(event_payload, default=str)}\n\n"


def _build_terminal_turn_event(
    event_type: str,
    *,
    session_id: str,
    turn_id: str,
    trace_id: Optional[str] = None,
    message: Optional[str] = None,
    error_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one closed terminal stream event."""

    payload = _stream_event_payload(
        event_type,
        session_id=session_id,
        turn_id=turn_id,
        trace_id=trace_id,
    )
    if message:
        payload["message"] = message
    if error_type:
        payload["error_type"] = error_type
    return payload


def _prepare_chat_stream_turn(
    *,
    repository: ChatHistoryRepository,
    db: Session,
    session_id: str,
    user_id: str,
    user_message: str,
    requested_turn_id: Optional[str],
    active_document_id: UUID | None,
) -> PreparedChatStreamTurn:
    """Persist the durable user turn and build runner context for streaming."""

    turn_id = requested_turn_id or uuid.uuid4().hex
    effective_user_message = user_message

    repository.get_or_create_session(
        session_id=session_id,
        user_auth_sub=user_id,
        chat_kind=ASSISTANT_CHAT_KIND,
        active_document_id=active_document_id,
    )
    user_turn = repository.append_message(
        session_id=session_id,
        user_auth_sub=user_id,
        chat_kind=ASSISTANT_CHAT_KIND,
        role="user",
        content=user_message,
        turn_id=turn_id,
    )
    db.commit()

    replay_assistant_turn: ChatMessageRecord | None = None
    if not user_turn.created:
        effective_user_message = user_turn.message.content
        replay_assistant_turn = repository.get_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=turn_id,
            role="assistant",
        )
        if replay_assistant_turn is not None:
            logger.info(
                "Returning durable replay for streaming chat turn %s",
                turn_id,
                extra={"session_id": session_id, "user_id": user_id, "turn_id": turn_id},
            )
            return PreparedChatStreamTurn(
                turn_id=turn_id,
                effective_user_message=effective_user_message,
                context_messages=[],
                replay_assistant_turn=replay_assistant_turn,
            )

        logger.info(
            "Retrying incomplete streaming chat turn %s after prior request ended",
            turn_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": turn_id},
        )
        if effective_user_message != user_message:
            logger.info(
                "Reusing stored user content for retried streaming turn %s",
                turn_id,
                extra={"session_id": session_id, "user_id": user_id, "turn_id": turn_id},
            )

    context_messages = _build_context_messages_from_durable_messages(
        repository,
        user_id=user_id,
        session_id=session_id,
        user_message=effective_user_message,
    )
    return PreparedChatStreamTurn(
        turn_id=turn_id,
        effective_user_message=effective_user_message,
        context_messages=context_messages,
    )


def _persist_completed_chat_stream_turn(
    *,
    session_id: str,
    user_id: str,
    turn_id: str,
    user_message: str,
    assistant_message: str,
    trace_id: Optional[str],
    extraction_candidates: List[ExtractionEnvelopeCandidate],
    document_id: Optional[str],
) -> ChatMessageRecord:
    """Persist the completed stream assistant turn using a fresh SQL session."""

    completion_db = SessionLocal()
    try:
        repository = _get_chat_history_repository(completion_db)
        session = repository.get_session(
            session_id=session_id,
            user_auth_sub=user_id,
        )
        if session is None:
            raise ChatHistorySessionNotFoundError("Chat session not found")

        existing_assistant_turn = repository.get_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=turn_id,
            role="assistant",
        )
        if existing_assistant_turn is not None:
            return existing_assistant_turn

        _persist_extraction_candidates(
            candidates=extraction_candidates,
            document_id=document_id,
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
            source_kind=CurationExtractionSourceKind.CHAT,
            db=completion_db,
        )
        completion_db.commit()

        try:
            assistant_turn = repository.append_message(
                session_id=session_id,
                user_auth_sub=user_id,
                chat_kind=ASSISTANT_CHAT_KIND,
                role="assistant",
                content=assistant_message,
                turn_id=turn_id,
                trace_id=trace_id,
            )
            completion_db.commit()
        except Exception as exc:
            completion_db.rollback()
            raise ChatStreamAssistantSaveFailedError(
                "Failed to persist stream assistant turn"
            ) from exc

        return assistant_turn.message
    except ChatHistorySessionNotFoundError:
        completion_db.rollback()
        raise
    except ChatStreamAssistantSaveFailedError:
        raise
    except Exception:
        completion_db.rollback()
        raise
    finally:
        completion_db.close()


def _prepare_execute_flow_turn(
    *,
    repository: ChatHistoryRepository,
    db: Session,
    flow: CurationFlow,
    session_id: str,
    user_id: str,
    user_message: str,
    requested_turn_id: Optional[str],
    active_document_id: UUID | None,
) -> PreparedExecuteFlowTurn:
    """Persist the durable execute-flow user turn and detect completed replays."""

    turn_id = requested_turn_id or uuid.uuid4().hex
    flow_run_id = str(uuid.uuid4())
    effective_user_message = user_message

    repository.get_or_create_session(
        session_id=session_id,
        user_auth_sub=user_id,
        chat_kind=ASSISTANT_CHAT_KIND,
        active_document_id=active_document_id,
    )
    user_turn = repository.append_message(
        session_id=session_id,
        user_auth_sub=user_id,
        chat_kind=ASSISTANT_CHAT_KIND,
        role="user",
        content=user_message,
        turn_id=turn_id,
        payload_json=_build_execute_flow_runtime_payload(
            None,
            flow_run_id=flow_run_id,
            trace_id=None,
        ),
    )

    if user_turn.created:
        flow.execution_count += 1
        flow.last_executed_at = datetime.now(timezone.utc)
        db.commit()
        return PreparedExecuteFlowTurn(
            turn_id=turn_id,
            flow_run_id=flow_run_id,
            effective_user_message=effective_user_message,
            replay_events=[],
        )

    effective_user_message = user_turn.message.content
    stored_flow_run_id, stored_trace_id = _extract_execute_flow_runtime_identifiers(
        user_turn.message.payload_json,
    )
    if stored_flow_run_id is None:
        stored_flow_run_id = flow_run_id
        repository.update_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=turn_id,
            role="user",
            payload_json=_build_execute_flow_runtime_payload(
                user_turn.message.payload_json,
                flow_run_id=stored_flow_run_id,
                trace_id=stored_trace_id,
            ),
            trace_id=stored_trace_id,
        )
        db.commit()

    replay = _build_execute_flow_turn_replay(
        repository.list_messages_for_turn(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            turn_id=turn_id,
        )
    )
    if replay is not None:
        replay_events, replay_assistant_message = replay
        logger.info(
            "Returning durable replay for execute-flow turn %s",
            turn_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": turn_id},
        )
        return PreparedExecuteFlowTurn(
            turn_id=turn_id,
            flow_run_id=stored_flow_run_id,
            effective_user_message=effective_user_message,
            replay_events=replay_events,
            replay_assistant_message=replay_assistant_message,
            resume_trace_id=stored_trace_id,
        )

    logger.info(
        "Retrying incomplete execute-flow turn %s after prior request ended",
        turn_id,
        extra={"session_id": session_id, "user_id": user_id, "turn_id": turn_id},
    )
    if effective_user_message != user_message:
        logger.info(
            "Reusing stored user content for retried execute-flow turn %s",
            turn_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": turn_id},
        )
    if stored_trace_id:
        logger.info(
            "Reusing persisted trace context for retried execute-flow turn %s",
            turn_id,
            extra={
                "session_id": session_id,
                "user_id": user_id,
                "turn_id": turn_id,
                "trace_id": stored_trace_id,
                "flow_run_id": stored_flow_run_id,
            },
        )

    return PreparedExecuteFlowTurn(
        turn_id=turn_id,
        flow_run_id=stored_flow_run_id,
        effective_user_message=effective_user_message,
        replay_events=[],
        resume_trace_id=stored_trace_id,
    )


def _persist_execute_flow_runtime_state(
    *,
    session_id: str,
    user_id: str,
    turn_id: str,
    flow_run_id: str,
    trace_id: Optional[str],
) -> None:
    """Persist execute-flow runtime identifiers on the durable user row."""

    completion_db = SessionLocal()
    try:
        repository = _get_chat_history_repository(completion_db)
        user_turn = repository.get_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=turn_id,
            role="user",
        )
        if user_turn is None:
            raise LookupError("Chat user turn not found")

        existing_flow_run_id, existing_trace_id = _extract_execute_flow_runtime_identifiers(
            user_turn.payload_json,
        )
        effective_flow_run_id = existing_flow_run_id or flow_run_id
        effective_trace_id = str(trace_id or existing_trace_id or "").strip() or None
        if (
            existing_flow_run_id == effective_flow_run_id
            and existing_trace_id == effective_trace_id
            and user_turn.trace_id == effective_trace_id
        ):
            return

        repository.update_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=turn_id,
            role="user",
            payload_json=_build_execute_flow_runtime_payload(
                user_turn.payload_json,
                flow_run_id=effective_flow_run_id,
                trace_id=effective_trace_id,
            ),
            trace_id=effective_trace_id,
        )
        completion_db.commit()
    except Exception:
        completion_db.rollback()
        raise
    finally:
        completion_db.close()


def _persist_completed_execute_flow_turn(
    *,
    session_id: str,
    user_id: str,
    turn_id: str,
    user_message: str,
    transcript_rows: List[ExecuteFlowTranscriptRow],
) -> None:
    """Persist completed execute-flow transcript rows using a fresh SQL session."""

    completion_db = SessionLocal()
    try:
        repository = _get_chat_history_repository(completion_db)
        session = repository.get_session(
            session_id=session_id,
            user_auth_sub=user_id,
        )
        if session is None:
            raise ChatHistorySessionNotFoundError("Chat session not found")

        existing_replay = _build_execute_flow_turn_replay(
            repository.list_messages_for_turn(
                session_id=session_id,
                user_auth_sub=user_id,
                chat_kind=ASSISTANT_CHAT_KIND,
                turn_id=turn_id,
            )
        )
        if existing_replay is not None:
            return

        for row in transcript_rows:
            repository.append_message(
                session_id=session_id,
                user_auth_sub=user_id,
                chat_kind=ASSISTANT_CHAT_KIND,
                role="flow",
                content=row.content,
                message_type=row.message_type,
                turn_id=turn_id,
                payload_json=row.payload_json,
                trace_id=row.trace_id,
                created_at=row.created_at,
            )
        completion_db.commit()

    except Exception:
        completion_db.rollback()
        raise
    finally:
        completion_db.close()


# Document Management Endpoints

@router.post("/chat/document/load", response_model=DocumentStatusResponse)
async def load_document_for_chat(
    payload: LoadDocumentRequest,
    user: Dict[str, Any] = get_auth_dependency()
) -> DocumentStatusResponse:
    """Select a document for chat interactions."""
    user_id = user.get("sub")
    logger.info(
        "Loading document for chat: %s",
        payload.document_id,
        extra={"user_id": user_id, "document_id": payload.document_id},
    )

    try:
        document_detail = await get_document(user["sub"], payload.document_id)
        logger.info(
            "Successfully retrieved document: %s",
            payload.document_id,
            extra={"user_id": user_id, "document_id": payload.document_id},
        )
    except ValueError as exc:
        logger.warning(
            "Document not found: %s",
            payload.document_id,
            extra={"user_id": user_id, "document_id": payload.document_id},
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(
            "Error loading document %s: %s",
            payload.document_id,
            exc,
            extra={"user_id": user_id, "document_id": payload.document_id},
        )
        raise HTTPException(status_code=500, detail="Failed to load document for chat") from exc

    document_summary = document_detail.get("document")
    if not document_summary:
        logger.error(
            "Document payload missing summary for %s",
            payload.document_id,
            extra={"user_id": user_id, "document_id": payload.document_id},
        )
        raise HTTPException(status_code=500, detail="Document summary unavailable")

    document_state.set_document(user['sub'], document_summary)

    # Invalidate document metadata cache to ensure fresh data for new document
    from src.lib.document_cache import invalidate_cache
    invalidate_cache(user['sub'], payload.document_id)

    active_document = _build_active_document(document_summary)
    return DocumentStatusResponse(
        active=True,
        document=active_document,
        message=f"Document '{active_document.filename or active_document.id}' loaded for chat",
    )


@router.get("/chat/document", response_model=DocumentStatusResponse)
async def get_loaded_document(user: Dict[str, Any] = get_auth_dependency()) -> DocumentStatusResponse:
    """Return information about the currently loaded document."""
    document_summary = document_state.get_document(user['sub'])
    if not document_summary:
        return DocumentStatusResponse(active=False, message="No document selected")

    return DocumentStatusResponse(active=True, document=_build_active_document(document_summary))


@router.delete("/chat/document", response_model=DocumentStatusResponse)
async def clear_loaded_document(user: Dict[str, Any] = get_auth_dependency()) -> DocumentStatusResponse:
    """Clear the current document selection."""
    document_summary = document_state.get_document(user['sub'])
    if not document_summary:
        return DocumentStatusResponse(active=False, message="No document was loaded")

    active_document = _build_active_document(document_summary)
    document_state.clear_document(user['sub'])
    return DocumentStatusResponse(
        active=False,
        document=active_document,
        message="Document selection cleared",
    )


# Session Management Endpoints

@router.post("/chat/session", response_model=SessionResponse)
async def create_session(
    request: CreateSessionRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
):
    """Create and persist one durable chat session for the authenticated user."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)
    session_id = str(uuid.uuid4())
    active_document_id, active_document = _resolve_session_create_active_document(
        repository=repository,
        user_id=user_id,
    )

    try:
        session = repository.create_session(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=request.chat_kind,
            active_document_id=active_document_id,
        )
        db.commit()
    except Exception as exc:
        logger.error(
            "Failed to create durable chat session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to create chat session",
            exc=exc,
        )

    logger.info(
        "Created durable chat session: %s",
        session_id,
        extra={"session_id": session_id, "user_id": user_id},
    )
    return SessionResponse(
        session_id=session.session_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        title=session.title,
        active_document_id=str(session.active_document_id) if session.active_document_id else None,
        active_document=active_document,
    )


# Chat Endpoints (using OpenAI Agents SDK)

@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    chat_message: ChatMessage,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = None,
):
    """Process a chat message and return a response (non-streaming)."""
    session_id = chat_message.session_id or str(uuid.uuid4())
    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    # Set context variables for file output tools
    set_current_session_id(session_id)
    set_current_user_id(user_id)

    # Get active document (optional)
    active_doc = document_state.get_document(user_id)
    document_id = active_doc.get("id") if active_doc else None
    document_name = active_doc.get("filename") if active_doc else None

    # Extract active groups from user's Cognito groups for prompt injection
    # Note: Cognito uses "cognito:groups" as the claim key
    cognito_groups = user.get("cognito:groups", [])
    active_groups = get_groups_from_cognito(cognito_groups)
    effective_user_message = chat_message.message
    turn_claim_key: Optional[str] = None
    turn_claim_token: Optional[str] = None
    turn_claim_acquired = False

    async def _release_non_stream_turn_claim() -> None:
        nonlocal turn_claim_acquired

        if not turn_claim_acquired or turn_claim_key is None or turn_claim_token is None:
            return

        turn_claim_acquired = False
        if _LOCAL_NON_STREAM_TURN_OWNERS.get(turn_claim_key) == turn_claim_token:
            _LOCAL_NON_STREAM_TURN_OWNERS.pop(turn_claim_key, None)
        await unregister_active_stream(turn_claim_key, user_id=turn_claim_token)

    if active_groups:
        logger.info(
            "User has active groups: %s (from Cognito groups: %s)",
            active_groups,
            cognito_groups,
            extra={"session_id": session_id, "user_id": user_id},
        )

    try:
        if chat_message.turn_id:
            turn_claim_key = f"non-stream-turn:{session_id}:{chat_message.turn_id}"
            # Use a per-request claim token so same-turn retries stay exclusive across workers.
            turn_claim_token = uuid.uuid4().hex

            if turn_claim_key in _LOCAL_NON_STREAM_TURN_OWNERS:
                raise HTTPException(status_code=409, detail="Chat turn is already in progress")

            _LOCAL_NON_STREAM_TURN_OWNERS[turn_claim_key] = turn_claim_token
            if not await register_active_stream(turn_claim_key, user_id=turn_claim_token):
                _LOCAL_NON_STREAM_TURN_OWNERS.pop(turn_claim_key, None)
                turn_claim_key = None
                turn_claim_token = None
                raise HTTPException(status_code=409, detail="Chat turn is already in progress")

            turn_claim_acquired = True

        active_document_id, _ = _resolve_session_create_active_document(
            repository=repository,
            user_id=user_id,
        )
        repository.get_or_create_session(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            active_document_id=active_document_id,
        )
        user_turn = repository.append_message(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            role="user",
            content=chat_message.message,
            turn_id=chat_message.turn_id,
        )
        db.commit()
    except HTTPException:
        await _release_non_stream_turn_claim()
        raise
    except ValueError as exc:
        await _release_non_stream_turn_claim()
        _rollback_and_raise(db, status_code=400, detail=str(exc), exc=exc)
    except Exception as exc:
        await _release_non_stream_turn_claim()
        logger.error(
            "Failed to persist durable non-stream user turn for session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to persist chat request",
            exc=exc,
        )

    if chat_message.turn_id and not user_turn.created:
        effective_user_message = user_turn.message.content
        try:
            assistant_turn = repository.get_message_by_turn_id(
                session_id=session_id,
                user_auth_sub=user_id,
                turn_id=chat_message.turn_id,
                role="assistant",
            )
        except ValueError as exc:
            await _release_non_stream_turn_claim()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if assistant_turn is not None:
            _queue_chat_title_backfill(
                background_tasks,
                session_id=session_id,
                user_id=user_id,
                preferred_generated_title=_generate_title_from_turn(
                    user_message=effective_user_message,
                    assistant_message=assistant_turn.content,
                ),
            )
            logger.info(
                "Returning durable replay for non-stream chat turn %s",
                chat_message.turn_id,
                extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
            )
            await _release_non_stream_turn_claim()
            return ChatResponse(response=assistant_turn.content, session_id=session_id)

        logger.info(
            "Retrying incomplete non-stream chat turn %s after prior request ended",
            chat_message.turn_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
        )
        if effective_user_message != chat_message.message:
            logger.info(
                "Reusing stored user content for retried non-stream turn %s",
                chat_message.turn_id,
                extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
            )

    try:
        tool_agent_map = get_supervisor_tool_agent_map()
    except Exception as exc:
        await _release_non_stream_turn_claim()
        logger.error(
            "Supervisor tool-map resolution failed; aborting chat run to prevent silent extraction data loss",
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Internal configuration error: unable to process chat request",
        ) from exc

    try:
        context_messages = _build_context_messages_from_durable_messages(
            repository,
            user_id=user_id,
            session_id=session_id,
            user_message=effective_user_message,
        )
        if context_messages:
            logger.info(
                "Including %s durable context messages for session %s",
                len(context_messages),
                session_id,
                extra={"session_id": session_id, "user_id": user_id},
            )

        # Collect full response from streaming generator
        full_response = ""
        error_message = None
        trace_id = None
        run_finished = False
        extraction_candidates: List[ExtractionEnvelopeCandidate] = []

        async for event in run_agent_streamed(
            context_messages=context_messages,
            user_id=user_id,
            session_id=session_id,
            document_id=document_id,
            document_name=document_name,
            active_groups=active_groups,
            supervisor_model=chat_message.model,
            specialist_model=chat_message.specialist_model,
            supervisor_temperature=chat_message.supervisor_temperature,
            specialist_temperature=chat_message.specialist_temperature,
            supervisor_reasoning=chat_message.supervisor_reasoning,
            specialist_reasoning=chat_message.specialist_reasoning,
        ):
            event_type = event.get("type")
            event_data = event.get("data", {}) or {}

            if event_type == "RUN_STARTED" and "trace_id" in event_data:
                trace_id = event_data.get("trace_id")

            candidate = _build_extraction_candidate_from_tool_event(
                event,
                tool_agent_map=tool_agent_map,
                conversation_summary=effective_user_message,
                metadata={"document_name": document_name} if document_name else None,
            )
            if candidate:
                extraction_candidates.append(candidate)

            if event_type == "RUN_FINISHED":
                full_response = event_data.get("response", "")
                run_finished = True
                continue
            elif event_type == "RUN_ERROR":
                # Capture error and stop processing
                error_message = event_data.get("message", "Unknown error")
                logger.error(
                    "Agent error during non-streaming chat: %s",
                    error_message,
                    extra={"session_id": session_id, "user_id": user_id},
                )
                break

        # If we got an error, raise it
        if error_message:
            raise HTTPException(status_code=500, detail=error_message)

        if run_finished:
            try:
                _persist_extraction_candidates(
                    candidates=extraction_candidates,
                    document_id=document_id,
                    user_id=user_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    source_kind=CurationExtractionSourceKind.CHAT,
                    db=db,
                )
                assistant_turn = repository.append_message(
                    session_id=session_id,
                    user_auth_sub=user_id,
                    chat_kind=ASSISTANT_CHAT_KIND,
                    role="assistant",
                    content=full_response,
                    turn_id=chat_message.turn_id,
                    trace_id=trace_id,
                )
                if chat_message.turn_id and not assistant_turn.created:
                    db.rollback()
                    logger.info(
                        "Discarding duplicate non-stream completion for replayed turn %s",
                        chat_message.turn_id,
                        extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
                    )
                    _queue_chat_title_backfill(
                        background_tasks,
                        session_id=session_id,
                        user_id=user_id,
                        preferred_generated_title=_generate_title_from_turn(
                            user_message=effective_user_message,
                            assistant_message=assistant_turn.message.content,
                        ),
                    )
                    return ChatResponse(response=assistant_turn.message.content, session_id=session_id)
                db.commit()
                _queue_chat_title_backfill(
                    background_tasks,
                    session_id=session_id,
                    user_id=user_id,
                    preferred_generated_title=_generate_title_from_turn(
                        user_message=effective_user_message,
                        assistant_message=assistant_turn.message.content,
                    ),
                )
            except ValueError as exc:
                _rollback_and_raise(db, status_code=400, detail=str(exc), exc=exc)
            except Exception as exc:
                logger.error(
                    "Failed to persist durable non-stream assistant turn for session %s",
                    session_id,
                    extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
                    exc_info=True,
                )
                _rollback_and_raise(
                    db,
                    status_code=500,
                    detail="Failed to persist chat response",
                    exc=exc,
                )
        else:
            raise HTTPException(status_code=500, detail="Chat run did not complete")

        return ChatResponse(response=full_response, session_id=session_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Chat error: %s",
            e,
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await _release_non_stream_turn_claim()


@router.post("/chat/stream")
async def chat_stream_endpoint(
    chat_message: ChatMessage,
    user: Dict[str, Any] = get_auth_dependency(),
    db: Session = Depends(get_db),
):
    """Stream a chat response using Server-Sent Events."""
    session_id = chat_message.session_id or str(uuid.uuid4())
    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    # Set context variables for file output tools
    set_current_session_id(session_id)
    set_current_user_id(user_id)

    # Get active document (optional)
    active_doc = document_state.get_document(user_id)
    document_id = active_doc.get("id") if active_doc else None
    document_name = active_doc.get("filename") if active_doc else None

    doc_info = f"{document_id[:8]}..." if document_id else "none"
    logger.info(
        "Chat stream request received",
        extra={"session_id": session_id, "user_id": user_id, "document_id": doc_info},
    )

    # Extract active groups from user's Cognito groups for prompt injection
    # Note: Cognito uses "cognito:groups" as the claim key
    cognito_groups = user.get("cognito:groups", [])
    active_groups = get_groups_from_cognito(cognito_groups)
    if active_groups:
        logger.info(
            "User has active groups: %s (from Cognito groups: %s)",
            active_groups,
            cognito_groups,
            extra={"session_id": session_id, "user_id": user_id},
        )

    try:
        tool_agent_map = get_supervisor_tool_agent_map()
    except Exception as exc:
        logger.error(
            "Supervisor tool-map resolution failed; aborting chat stream to prevent silent extraction data loss",
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Internal configuration error: unable to process chat request",
        ) from exc

    stream_lifecycle = await _claim_active_stream_lifecycle(session_id=session_id, user_id=user_id)
    cancel_event = stream_lifecycle.cancel_event
    generated_title_candidate: str | None = None

    try:
        active_document_id, _ = _resolve_session_create_active_document(
            repository=repository,
            user_id=user_id,
        )
        prepared_turn = _prepare_chat_stream_turn(
            repository=repository,
            db=db,
            session_id=session_id,
            user_id=user_id,
            user_message=chat_message.message,
            requested_turn_id=chat_message.turn_id,
            active_document_id=active_document_id,
        )
        generated_title_candidate = _generate_title_from_turn(
            user_message=prepared_turn.effective_user_message,
        )
    except HTTPException:
        await stream_lifecycle.cleanup(session_id)
        raise
    except ValueError as exc:
        await stream_lifecycle.cleanup(session_id)
        _rollback_and_raise(db, status_code=400, detail=str(exc), exc=exc)
    except Exception as exc:
        await stream_lifecycle.cleanup(session_id)
        logger.error(
            "Failed to persist durable stream user turn for session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to persist chat request",
            exc=exc,
        )

    if prepared_turn.context_messages:
        logger.info(
            "Including %s durable context messages for session %s",
            len(prepared_turn.context_messages),
            session_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": prepared_turn.turn_id},
        )

    if prepared_turn.replay_assistant_turn is not None:
        generated_title_candidate = _generate_title_from_turn(
            user_message=prepared_turn.effective_user_message,
            assistant_message=prepared_turn.replay_assistant_turn.content,
        )

        async def replay_stream():
            try:
                yield _stream_event_sse(
                    _stream_event_payload(
                        "TEXT_MESSAGE_CONTENT",
                        session_id=session_id,
                        turn_id=prepared_turn.turn_id,
                        trace_id=prepared_turn.replay_assistant_turn.trace_id,
                        content=prepared_turn.replay_assistant_turn.content,
                    )
                )
                yield _stream_event_sse(
                    _build_terminal_turn_event(
                        "turn_completed",
                        session_id=session_id,
                        turn_id=prepared_turn.turn_id,
                        trace_id=prepared_turn.replay_assistant_turn.trace_id,
                        message="Chat turn completed.",
                    )
                )
            finally:
                await stream_lifecycle.cleanup(session_id)

        return StreamingResponse(
            replay_stream(),
            media_type="text/event-stream",
            background=stream_lifecycle.background_task(lambda: generated_title_candidate),
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def generate_stream():
        """Generate SSE events from the agent runner."""
        nonlocal generated_title_candidate
        current_session_id = session_id
        current_turn_id = prepared_turn.turn_id
        full_response = ""
        trace_id = None
        run_finished = False
        runner_error_message: Optional[str] = None
        runner_error_type: Optional[str] = None
        interrupted_message: Optional[str] = None
        extraction_candidates: List[ExtractionEnvelopeCandidate] = []
        evidence_records: List[Dict[str, Any]] = []
        evidence_summary_event_received = False

        try:
            async for event in run_agent_streamed(
                context_messages=prepared_turn.context_messages,
                user_id=user_id,
                session_id=current_session_id,
                document_id=document_id,
                document_name=document_name,
                active_groups=active_groups,
                supervisor_model=chat_message.model,
                specialist_model=chat_message.specialist_model,
                supervisor_temperature=chat_message.supervisor_temperature,
                specialist_temperature=chat_message.specialist_temperature,
                supervisor_reasoning=chat_message.supervisor_reasoning,
                specialist_reasoning=chat_message.specialist_reasoning,
            ):
                if cancel_event.is_set() or await check_cancel_signal(current_session_id):
                    interrupted_message = "Run cancelled by user"
                    logger.info(
                        "Chat stream cancelled for session %s",
                        current_session_id,
                        extra={
                            "session_id": current_session_id,
                            "user_id": user_id,
                            "trace_id": trace_id,
                            "turn_id": current_turn_id,
                        },
                    )
                    break

                event_type = event.get("type")
                event_data = event.get("data", {}) or {}

                if "trace_id" in event_data:
                    trace_id = event_data.get("trace_id")

                flat_event = _stream_event_payload(
                    str(event_type),
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                )
                flat_event.update(event_data)
                flat_event["session_id"] = current_session_id
                flat_event["turn_id"] = current_turn_id

                if "timestamp" in event:
                    flat_event["timestamp"] = event["timestamp"]
                if "details" in event:
                    flat_event["details"] = event["details"]

                if event_type == "CHUNK_PROVENANCE":
                    for key in ["chunk_id", "doc_items", "message_id", "source_tool"]:
                        if key in event and key not in flat_event:
                            flat_event[key] = event[key]

                if event_type == "evidence_summary":
                    event_evidence_records = _extract_evidence_records(event.get("evidence_records"))
                    if not event_evidence_records:
                        event_evidence_records = _extract_evidence_records(
                            (event.get("details") or {}).get("evidence_records", [])
                        )
                    evidence_curation_metadata = _build_evidence_curation_metadata(
                        event=event,
                        tool_agent_map=tool_agent_map,
                    )
                    if event_evidence_records:
                        evidence_records = event_evidence_records
                        evidence_summary_event_received = True
                    if "evidence_records" not in flat_event:
                        if event_evidence_records:
                            flat_event["evidence_records"] = event_evidence_records
                        elif "evidence_records" in event:
                            flat_event["evidence_records"] = event["evidence_records"]
                        elif "evidence_records" in (event.get("details") or {}):
                            flat_event["evidence_records"] = event["details"]["evidence_records"]
                    for key, value in evidence_curation_metadata.items():
                        flat_event[key] = value
                    yield _stream_event_sse(flat_event)
                    continue

                candidate = _build_extraction_candidate_from_tool_event(
                    event,
                    tool_agent_map=tool_agent_map,
                    conversation_summary=prepared_turn.effective_user_message,
                    metadata={"document_name": document_name} if document_name else None,
                )
                if candidate:
                    extraction_candidates.append(candidate)

                if not evidence_summary_event_received:
                    evidence_record = _build_evidence_record_from_tool_event(event)
                    if evidence_record:
                        evidence_records.append(evidence_record)

                if event_type == "RUN_FINISHED":
                    full_response = event_data.get("response", "")
                    run_finished = True
                    continue

                if event_type == "RUN_ERROR":
                    runner_error_message = event_data.get("message")
                    runner_error_type = event_data.get("error_type")
                    if not runner_error_message:
                        logger.error(
                            "Agent sent RUN_ERROR without message field",
                            extra={"session_id": current_session_id, "turn_id": current_turn_id},
                        )
                        runner_error_message = "Agent error (no details provided)"
                    if not runner_error_type:
                        logger.error(
                            "Agent sent RUN_ERROR without error_type field",
                            extra={"session_id": current_session_id, "turn_id": current_turn_id},
                        )
                    logger.error(
                        "Agent error during streaming chat: %s",
                        runner_error_message,
                        extra={
                            "session_id": current_session_id,
                            "user_id": user_id,
                            "trace_id": trace_id,
                            "turn_id": current_turn_id,
                        },
                    )
                    break

                yield _stream_event_sse(flat_event)

            if interrupted_message:
                yield _stream_event_sse(
                    _build_terminal_turn_event(
                        "turn_interrupted",
                        session_id=current_session_id,
                        turn_id=current_turn_id,
                        trace_id=trace_id,
                        message=interrupted_message,
                    )
                )
                return

            if runner_error_message:
                yield _stream_event_sse(
                    _build_terminal_turn_event(
                        "turn_failed",
                        session_id=current_session_id,
                        turn_id=current_turn_id,
                        trace_id=trace_id,
                        message=runner_error_message,
                        error_type=runner_error_type,
                    )
                )
                return

            if run_finished:
                if evidence_records and not evidence_summary_event_received:
                    evidence_curation_metadata = _build_candidate_evidence_curation_metadata(
                        extraction_candidates,
                    )
                    yield _stream_event_sse(
                        _stream_event_payload(
                            "evidence_summary",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            evidence_records=evidence_records,
                            **evidence_curation_metadata,
                        )
                    )

                try:
                    assistant_turn = _persist_completed_chat_stream_turn(
                        session_id=current_session_id,
                        user_id=user_id,
                        turn_id=current_turn_id,
                        user_message=prepared_turn.effective_user_message,
                        assistant_message=full_response,
                        trace_id=trace_id,
                        extraction_candidates=extraction_candidates,
                        document_id=document_id,
                    )
                except ChatHistorySessionNotFoundError:
                    yield _stream_event_sse(
                        _build_terminal_turn_event(
                            "session_gone",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            message="Chat session is no longer available.",
                        )
                    )
                    return
                except ChatStreamAssistantSaveFailedError as exc:
                    root_exc = exc.__cause__ or exc
                    logger.error(
                        "Failed to persist durable stream assistant turn for session %s",
                        current_session_id,
                        extra={
                            "session_id": current_session_id,
                            "user_id": user_id,
                            "trace_id": trace_id,
                            "turn_id": current_turn_id,
                        },
                        exc_info=True,
                    )
                    yield _stream_event_sse(
                        _stream_event_payload(
                            "SUPERVISOR_ERROR",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            details={
                                "error": str(root_exc),
                                "context": type(root_exc).__name__,
                                "message": (
                                    "The chat response completed, but saving the durable assistant turn failed."
                                ),
                            },
                        )
                    )
                    yield _stream_event_sse(
                        _build_terminal_turn_event(
                            "turn_save_failed",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            message="Chat completed, but the assistant response could not be saved.",
                            error_type=type(root_exc).__name__,
                        )
                    )
                    return
                except Exception as exc:
                    logger.error(
                        "Failed to persist durable stream completion side effects for session %s",
                        current_session_id,
                        extra={
                            "session_id": current_session_id,
                            "user_id": user_id,
                            "trace_id": trace_id,
                            "turn_id": current_turn_id,
                        },
                        exc_info=True,
                    )
                    yield _stream_event_sse(
                        _stream_event_payload(
                            "SUPERVISOR_ERROR",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            details={
                                "error": str(exc),
                                "context": type(exc).__name__,
                                "message": (
                                    "The chat response completed, but saving durable stream side effects failed."
                                ),
                            },
                        )
                    )
                    yield _stream_event_sse(
                        _build_terminal_turn_event(
                            "turn_failed",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            message="Chat completed, but durable side effects could not be saved.",
                            error_type=type(exc).__name__,
                        )
                    )
                    return

                generated_title_candidate = _generate_title_from_turn(
                    user_message=prepared_turn.effective_user_message,
                    assistant_message=assistant_turn.content,
                )
                yield _stream_event_sse(
                    _build_terminal_turn_event(
                        "turn_completed",
                        session_id=current_session_id,
                        turn_id=current_turn_id,
                        trace_id=assistant_turn.trace_id or trace_id,
                        message="Chat turn completed.",
                    )
                )
                return

            yield _stream_event_sse(
                _build_terminal_turn_event(
                    "turn_failed",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    message="Chat run did not complete.",
                    error_type="IncompleteRun",
                )
            )
        except asyncio.CancelledError:
            logger.warning(
                "Chat stream cancelled unexpectedly for session %s",
                current_session_id,
                extra={
                    "session_id": current_session_id,
                    "user_id": user_id,
                    "trace_id": trace_id,
                    "turn_id": current_turn_id,
                },
            )
            yield _stream_event_sse(
                _stream_event_payload(
                    "SUPERVISOR_ERROR",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    details={
                        "error": "Stream cancelled unexpectedly",
                        "context": "asyncio.CancelledError",
                        "message": (
                            "The request was interrupted. Please provide feedback using the ⋮ menu, then try your query again."
                        ),
                    },
                )
            )
            yield _stream_event_sse(
                _build_terminal_turn_event(
                    "turn_interrupted",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    message=(
                        "The request was interrupted unexpectedly. Please provide feedback using the ⋮ menu on this message, then try your query again."
                    ),
                    error_type="StreamCancelled",
                )
            )
        except Exception as exc:
            logger.error(
                "Stream error: %s",
                exc,
                extra={
                    "session_id": current_session_id,
                    "user_id": user_id,
                    "trace_id": trace_id,
                    "turn_id": current_turn_id,
                },
                exc_info=True,
            )
            yield _stream_event_sse(
                _stream_event_payload(
                    "SUPERVISOR_ERROR",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    details={
                        "error": str(exc),
                        "context": type(exc).__name__,
                        "message": "An error occurred. Please provide feedback using the ⋮ menu, then try your query again.",
                    },
                )
            )
            yield _stream_event_sse(
                _build_terminal_turn_event(
                    "turn_failed",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    message=(
                        "An error occurred. Please provide feedback using the ⋮ menu on this message, then try your query again."
                    ),
                    error_type=type(exc).__name__,
                )
            )
        finally:
            await stream_lifecycle.cleanup(current_session_id)

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        background=stream_lifecycle.background_task(lambda: generated_title_candidate),
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/chat/stop")
async def stop_chat(request: StopRequest, user: Dict[str, Any] = get_auth_dependency()):
    """Best-effort cancel of a running chat stream for the given session.

    Note: Stop is cooperative - it signals the stream to stop at the next event,
    but cannot interrupt long-running tool calls mid-execution.
    """
    session_id = request.session_id
    requester_id = user.get("sub")
    if not requester_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

    owner_id = _LOCAL_SESSION_OWNERS.get(session_id)
    if owner_id is None:
        owner_id = await get_stream_owner(session_id)
    if owner_id and owner_id != requester_id:
        raise HTTPException(status_code=403, detail="You do not have permission to cancel this session")

    # Check if stream is active (either locally or in Redis)
    local_event = _LOCAL_CANCEL_EVENTS.get(session_id)
    stream_active = await is_stream_active(session_id)

    if stream_active and owner_id is None:
        raise HTTPException(status_code=403, detail="Unable to verify stream ownership for cancellation")

    if not local_event and not stream_active:
        return {"status": "ok", "message": "No running chat for this session."}

    # Signal cancellation via Redis (cross-worker) and local event (same-worker)
    await set_cancel_signal(session_id)
    if local_event:
        local_event.set()

    return {"status": "ok", "message": "Cancellation requested (cooperative - may take a moment)."}


@router.post(
    "/chat/{session_id}/assistant-rescue",
    response_model=AssistantRescueResponse,
    responses={
        409: {
            "description": (
                "The referenced user turn is missing, or the retry payload conflicts "
                "with an existing assistant turn for the same turn_id."
            )
        }
    },
)
async def assistant_rescue(
    session_id: str,
    request: AssistantRescueRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
    background_tasks: BackgroundTasks = None,
):
    """Backfill one durable assistant turn; retries must reuse the stored payload."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    try:
        session = repository.get_session(
            session_id=session_id,
            user_auth_sub=user_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Chat session not found")

        user_turn = repository.get_message_by_turn_id(
            session_id=session_id,
            user_auth_sub=user_id,
            turn_id=request.turn_id,
            role="user",
        )
        if user_turn is None:
            raise HTTPException(status_code=409, detail="Chat user turn not found")

        assistant_turn = repository.append_message(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            role="assistant",
            content=request.content,
            turn_id=request.turn_id,
            trace_id=request.trace_id,
        )
        if not assistant_turn.created:
            conflicting_fields = _assistant_rescue_conflicting_fields(
                existing_turn=assistant_turn.message,
                content=request.content,
                trace_id=request.trace_id,
            )
            if conflicting_fields:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Assistant rescue payload conflicts with existing assistant turn "
                        f"for fields: {', '.join(conflicting_fields)}"
                    ),
                )
        db.commit()
        _queue_chat_title_backfill(
            background_tasks,
            session_id=session_id,
            user_id=user_id,
            preferred_generated_title=_generate_title_from_turn(
                user_message=user_turn.content,
                assistant_message=assistant_turn.message.content,
            ),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        _rollback_and_raise(db, status_code=400, detail=str(exc), exc=exc)
    except ChatHistorySessionNotFoundError as exc:
        _rollback_and_raise(db, status_code=404, detail="Chat session not found", exc=exc)
    except Exception as exc:
        logger.error(
            "Failed to rescue assistant turn for session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id, "turn_id": request.turn_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to rescue assistant turn",
            exc=exc,
        )

    return AssistantRescueResponse(
        session_id=session_id,
        turn_id=request.turn_id,
        created=assistant_turn.created,
        trace_id=assistant_turn.message.trace_id,
    )


@router.post("/chat/execute-flow")
async def execute_flow_endpoint(
    request: ExecuteFlowRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
):
    """Execute a curation flow with SSE streaming response.

    Executes a user-defined curation flow, streaming events back via SSE.
    Flow ownership is verified before execution.

    Returns:
        StreamingResponse with Server-Sent Events

    HTTP Status Codes:
        200: Success (streaming response)
        400: Validation error (Pydantic)
        401: Unauthorized
        403: User doesn't own this flow
        404: Flow not found or soft-deleted
    """
    db_user = set_global_user_from_cognito(db, user)
    repository = _get_chat_history_repository(db)

    flow = db.query(CurationFlow).filter(
        CurationFlow.id == request.flow_id,
        CurationFlow.is_active == True,  # noqa: E712 - SQLAlchemy requires == for SQL
    ).first()

    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    if flow.user_id != db_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    user_id = _require_user_sub(user)
    history_user_message = (request.user_query or "").strip() or f"Run flow '{flow.name}'"

    cognito_groups = user.get("cognito:groups", [])
    active_groups = get_groups_from_cognito(cognito_groups)
    if active_groups:
        logger.info(
            "User has active groups: %s",
            active_groups,
            extra={"session_id": request.session_id, "user_id": user_id},
        )

    set_current_session_id(request.session_id)
    set_current_user_id(user_id)

    active_doc = document_state.get_document(user_id)
    document_name = active_doc.get("filename") if active_doc else None

    logger.info(
        "Starting flow execution: flow_id=%s flow_name=%s document_id=%s document_name=%s turn_id=%s",
        request.flow_id,
        flow.name,
        request.document_id,
        document_name,
        request.turn_id,
        extra={"session_id": request.session_id, "user_id": user_id, "turn_id": request.turn_id},
    )

    stream_lifecycle = await _claim_active_stream_lifecycle(
        session_id=request.session_id,
        user_id=user_id,
    )
    cancel_event = stream_lifecycle.cancel_event
    generated_title_candidate: str | None = None

    try:
        active_document_id, _ = _resolve_session_create_active_document(
            repository=repository,
            user_id=user_id,
        )
        prepared_turn = _prepare_execute_flow_turn(
            repository=repository,
            db=db,
            flow=flow,
            session_id=request.session_id,
            user_id=user_id,
            user_message=history_user_message,
            requested_turn_id=request.turn_id,
            active_document_id=active_document_id,
        )
        generated_title_candidate = _generate_title_from_turn(
            user_message=prepared_turn.effective_user_message,
        )
    except HTTPException:
        await stream_lifecycle.cleanup(request.session_id)
        raise
    except ValueError as exc:
        await stream_lifecycle.cleanup(request.session_id)
        _rollback_and_raise(db, status_code=400, detail=str(exc), exc=exc)
    except Exception as exc:
        logger.error(
            "Failed to persist execute-flow request for session %s",
            request.session_id,
            extra={"session_id": request.session_id, "user_id": user_id, "turn_id": request.turn_id},
            exc_info=True,
        )
        db.rollback()
        await stream_lifecycle.cleanup(request.session_id)
        raise HTTPException(status_code=500, detail="Failed to start flow execution") from exc

    if prepared_turn.replay_events:
        if prepared_turn.replay_assistant_message is not None:
            generated_title_candidate = _generate_title_from_turn(
                user_message=prepared_turn.effective_user_message,
                assistant_message=prepared_turn.replay_assistant_message,
            )

        async def replay_stream():
            for event_payload in prepared_turn.replay_events:
                yield _stream_event_sse(event_payload)

        return StreamingResponse(
            replay_stream(),
            media_type="text/event-stream",
            background=stream_lifecycle.background_task(lambda: generated_title_candidate),
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def event_generator():
        """Generate SSE events from flow execution with cancellation support."""
        nonlocal generated_title_candidate
        current_session_id = request.session_id
        current_turn_id = prepared_turn.turn_id
        trace_id = None
        flow_status: Optional[str] = None
        flow_failure_reason: Optional[str] = None
        run_finished_response = ""
        chat_output_response = ""
        agents_used: List[str] = []
        specialist_outputs: List[Dict[str, Any]] = []
        specialist_summaries: List[Dict[str, Any]] = []
        domain_warnings: List[Dict[str, Any]] = []
        file_outputs: List[Dict[str, Any]] = []
        transcript_rows: List[ExecuteFlowTranscriptRow] = []
        run_started_event: Optional[Dict[str, Any]] = None
        chat_output_ready_event: Optional[Dict[str, Any]] = None
        run_error_event: Optional[Dict[str, Any]] = None
        buffered_flow_finished_event: Optional[Dict[str, Any]] = None

        try:
            async for event in execute_flow(
                flow=flow,
                user_id=user_id,
                session_id=current_session_id,
                db_user_id=db_user.id,
                document_id=str(request.document_id) if request.document_id else None,
                document_name=document_name,
                user_query=request.user_query,
                active_groups=active_groups,
                flow_run_id=prepared_turn.flow_run_id,
                trace_context=(
                    {"trace_id": prepared_turn.resume_trace_id}
                    if prepared_turn.resume_trace_id
                    else None
                ),
            ):
                if cancel_event.is_set() or await check_cancel_signal(current_session_id):
                    logger.info(
                        "Flow execution cancelled for session %s",
                        current_session_id,
                        extra={
                            "session_id": current_session_id,
                            "user_id": user_id,
                            "trace_id": trace_id,
                            "turn_id": current_turn_id,
                        },
                    )
                    yield _stream_event_sse(
                        _stream_event_payload(
                            "RUN_ERROR",
                            session_id=current_session_id,
                            turn_id=current_turn_id,
                            trace_id=trace_id,
                            message="Flow execution cancelled by user",
                            error_type="FlowCancelled",
                        )
                    )
                    break

                event_type = event.get("type")
                event_data = event.get("data", {}) or {}
                event_details = event.get("details", {}) or {}

                if event_type == "RUN_STARTED" and "trace_id" in event_data:
                    trace_id = event_data.get("trace_id")
                    _persist_execute_flow_runtime_state(
                        session_id=current_session_id,
                        user_id=user_id,
                        turn_id=current_turn_id,
                        flow_run_id=prepared_turn.flow_run_id,
                        trace_id=trace_id,
                    )

                if event_type == "RUN_FINISHED":
                    run_finished_response = str(event_data.get("response") or "")
                    agents_used.extend([
                        str(agent_name) for agent_name in (event_data.get("agents_used") or [])
                        if agent_name
                    ])
                elif event_type == "CHAT_OUTPUT_READY":
                    chat_output_response = str(event_details.get("output") or event_data.get("output") or "")
                elif event_type == "CREW_START":
                    crew_name = event_details.get("crewDisplayName") or event_details.get("crewName")
                    if crew_name:
                        agents_used.append(str(crew_name))
                elif event_type == "SPECIALIST_SUMMARY":
                    specialist_summaries.append(dict(event_details))
                elif event_type == "DOMAIN_WARNING":
                    domain_warnings.append(dict(event_details))
                elif event_type == "FILE_READY":
                    file_outputs.append(dict(event_details))
                elif event_type == "FLOW_FINISHED":
                    flow_status = event_data.get("status")
                    flow_failure_reason = event_data.get("failure_reason")
                elif event_type == "TOOL_COMPLETE":
                    tool_name = event_details.get("toolName")
                    internal_payload = event.get("internal")
                    if (
                        isinstance(internal_payload, dict)
                        and isinstance(tool_name, str)
                        and tool_name.startswith("ask_")
                        and tool_name.endswith("_specialist")
                        and "tool_output" in internal_payload
                    ):
                        raw_output = internal_payload.get("tool_output")
                        output_text = str(raw_output) if raw_output is not None else ""
                        specialist_outputs.append({
                            "tool": tool_name,
                            "output": output_text,
                            "output_length": internal_payload.get("output_length", len(output_text)),
                        })

                flat_event = {
                    "type": event_type,
                    "session_id": current_session_id,
                    "turn_id": current_turn_id,
                }
                flat_event.update(event_data)

                if "timestamp" in event:
                    flat_event["timestamp"] = event["timestamp"]
                if "details" in event:
                    flat_event["details"] = event["details"]

                if event_type == "FLOW_STEP_EVIDENCE":
                    for source in (event, event_details):
                        for key in (
                            "flow_id",
                            "flow_name",
                            "flow_run_id",
                            "step",
                            "tool_name",
                            "agent_id",
                            "agent_name",
                            "evidence_preview",
                            "evidence_records",
                            "evidence_count",
                            "total_evidence_records",
                        ):
                            if key in source and key not in flat_event:
                                flat_event[key] = source[key]

                if event_type == "RUN_STARTED":
                    run_started_event = dict(flat_event)
                elif event_type == "CHAT_OUTPUT_READY":
                    chat_output_ready_event = dict(flat_event)
                elif event_type == "RUN_ERROR":
                    run_error_event = dict(flat_event)
                elif event_type == "FLOW_FINISHED":
                    buffered_flow_finished_event = dict(flat_event)

                transcript_row = _build_execute_flow_transcript_row_from_event(flat_event)
                if transcript_row is not None:
                    transcript_rows.append(transcript_row)

                if event_type == "FLOW_FINISHED":
                    continue

                yield _stream_event_sse(flat_event)

            if flow_status:
                history_assistant_message = _build_flow_memory_assistant_message(
                    flow_name=flow.name,
                    flow_id=str(flow.id),
                    session_id=current_session_id,
                    status=flow_status,
                    trace_id=trace_id,
                    final_user_output=chat_output_response or run_finished_response,
                    agents_used=agents_used,
                    specialist_outputs=specialist_outputs,
                    specialist_summaries=specialist_summaries,
                    domain_warnings=domain_warnings,
                    file_outputs=file_outputs,
                    failure_reason=flow_failure_reason,
                )
                summary_row = _build_execute_flow_summary_row(
                    flow_id=str(flow.id),
                    flow_name=flow.name,
                    flow_run_id=str(
                        (buffered_flow_finished_event or {}).get("flow_run_id") or ""
                    ).strip() or None,
                    session_id=current_session_id,
                    document_id=str(request.document_id) if request.document_id else None,
                    status=flow_status,
                    trace_id=trace_id,
                    final_user_output=chat_output_response or run_finished_response,
                    failure_reason=flow_failure_reason,
                    assistant_message=history_assistant_message,
                    run_started_event=run_started_event,
                    terminal_events=[
                        event_payload
                        for event_payload in [
                            chat_output_ready_event,
                            run_error_event,
                            buffered_flow_finished_event,
                        ]
                        if event_payload is not None
                    ],
                )
                _persist_completed_execute_flow_turn(
                    session_id=current_session_id,
                    user_id=user_id,
                    turn_id=current_turn_id,
                    user_message=prepared_turn.effective_user_message,
                    transcript_rows=[*transcript_rows, summary_row],
                )
                generated_title_candidate = _generate_title_from_turn(
                    user_message=prepared_turn.effective_user_message,
                    assistant_message=chat_output_response or run_finished_response or history_assistant_message,
                )

            if buffered_flow_finished_event is not None:
                yield _stream_event_sse(buffered_flow_finished_event)

        except asyncio.CancelledError:
            logger.warning(
                "Flow execution cancelled unexpectedly for session %s",
                current_session_id,
                extra={
                    "session_id": current_session_id,
                    "user_id": user_id,
                    "trace_id": trace_id,
                    "turn_id": current_turn_id,
                },
            )
            yield _stream_event_sse(
                _stream_event_payload(
                    "SUPERVISOR_ERROR",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    details={
                        "error": "Flow cancelled unexpectedly",
                        "context": "asyncio.CancelledError",
                    },
                )
            )
            yield _stream_event_sse(
                _stream_event_payload(
                    "RUN_ERROR",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    message="Flow execution was interrupted unexpectedly.",
                    error_type="StreamCancelled",
                )
            )
        except Exception as exc:
            logger.error(
                "Flow execution error: %s",
                exc,
                extra={
                    "session_id": current_session_id,
                    "user_id": user_id,
                    "trace_id": trace_id,
                    "turn_id": current_turn_id,
                },
                exc_info=True,
            )
            yield _stream_event_sse(
                _stream_event_payload(
                    "SUPERVISOR_ERROR",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    details={
                        "error": str(exc),
                        "context": type(exc).__name__,
                    },
                )
            )
            yield _stream_event_sse(
                _stream_event_payload(
                    "RUN_ERROR",
                    session_id=current_session_id,
                    turn_id=current_turn_id,
                    trace_id=trace_id,
                    message=f"Flow execution error: {str(exc)}",
                    error_type=type(exc).__name__,
                )
            )
        finally:
            await stream_lifecycle.cleanup(current_session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        background=stream_lifecycle.background_task(lambda: generated_title_candidate),
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@router.get("/chat/status")
async def chat_status(user: Dict[str, Any] = get_auth_dependency()):
    """Check the status of the chat service."""
    import os
    return {
        "service": "chat",
        "status": "ready",
        "engine": "openai-agents-sdk",
        "openai_key_configured": bool(os.getenv("OPENAI_API_KEY"))
    }


# Conversation History Endpoints

@router.get("/chat/conversation", response_model=ConversationStatusResponse)
async def get_conversation_status(
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
) -> ConversationStatusResponse:
    """Get the current conversation status and memory statistics for the authenticated user."""
    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    try:
        latest_session = _latest_visible_chat_session(repository, user_id=user_id)
        stats = _build_durable_conversation_stats(
            repository,
            user_id=user_id,
            current_session=latest_session,
        )
        return ConversationStatusResponse(
            is_active=latest_session is not None,
            conversation_id=latest_session.session_id if latest_session is not None else None,
            memory_stats=stats,
            message="Conversation status retrieved successfully",
        )
    except Exception as e:
        logger.error("Failed to get conversation status: %s", e, extra={"user_id": user_id})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/conversation/reset", response_model=ConversationResetResponse)
async def reset_conversation(
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
) -> ConversationResetResponse:
    """Reset the conversation memory for the authenticated user and start a new conversation."""
    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    try:
        active_document_id, _active_document = _resolve_session_create_active_document(
            repository=repository,
            user_id=user_id,
        )
        new_session_id = str(uuid.uuid4())
        new_session = repository.create_session(
            session_id=new_session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            active_document_id=active_document_id,
        )
        db.commit()
        stats = _build_durable_conversation_stats(
            repository,
            user_id=user_id,
            current_session=new_session,
        )
        return ConversationResetResponse(
            success=True,
            message="Conversation reset successfully. Use the provided session_id for the next message.",
            memory_stats=stats,
            session_id=new_session_id,
        )
    except Exception as e:
        db.rollback()
        logger.error("Failed to reset conversation: %s", e, extra={"user_id": user_id})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/history/{session_id}", response_model=ChatSessionDetailResponse)
async def get_session_history(
    session_id: str,
    message_limit: int = Query(100, ge=1, le=200),
    message_cursor: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
    background_tasks: BackgroundTasks = None,
):
    """Return one durable chat session plus one page of persisted transcript rows."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    try:
        detail = repository.get_session_detail(
            session_id=session_id,
            user_auth_sub=user_id,
            message_limit=message_limit,
            message_cursor=_decode_message_cursor(message_cursor),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="Chat session not found")

    active_document = await _load_session_active_document(
        user_id=user_id,
        active_document_id=detail.session.active_document_id,
    )
    generated_title = None
    if detail.session.effective_title is None:
        if message_cursor is None:
            generated_title = _generate_title_from_messages(detail.messages)
        _queue_chat_title_backfill(
            background_tasks,
            session_id=session_id,
            user_id=user_id,
            preferred_generated_title=generated_title,
        )
    return ChatSessionDetailResponse(
        session=_serialize_session(detail.session, title_override=generated_title),
        active_document=active_document,
        messages=[_serialize_message(message) for message in detail.messages],
        message_limit=message_limit,
        next_message_cursor=_encode_message_cursor(detail.next_message_cursor),
    )


@router.get("/chat/history", response_model=ChatSessionListResponse)
async def get_all_sessions_stats(
    chat_kind: Literal["assistant_chat", "agent_studio", "all"] = Query(...),
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    document_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
    background_tasks: BackgroundTasks = None,
):
    """Browse or search durable chat sessions visible to the authenticated user."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)
    normalized_query = query.strip() if query is not None else None
    if query is not None and not normalized_query:
        raise HTTPException(status_code=400, detail="query cannot be blank")

    active_document_id = _parse_document_filter(document_id)
    decoded_cursor = _decode_session_cursor(cursor)

    try:
        if normalized_query:
            page = repository.search_sessions(
                user_auth_sub=user_id,
                chat_kind=chat_kind,
                query=normalized_query,
                limit=limit,
                cursor=decoded_cursor,
                active_document_id=active_document_id,
            )
            total_sessions = repository.count_sessions(
                user_auth_sub=user_id,
                chat_kind=chat_kind,
                query=normalized_query,
                active_document_id=active_document_id,
            )
        else:
            page = repository.list_sessions(
                user_auth_sub=user_id,
                chat_kind=chat_kind,
                limit=limit,
                cursor=decoded_cursor,
                active_document_id=active_document_id,
            )
            total_sessions = repository.count_sessions(
                user_auth_sub=user_id,
                chat_kind=chat_kind,
                active_document_id=active_document_id,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for session in page.items:
        if session.effective_title is None:
            _queue_chat_title_backfill(
                background_tasks,
                session_id=session.session_id,
                user_id=user_id,
            )

    return ChatSessionListResponse(
        chat_kind=chat_kind,
        total_sessions=total_sessions,
        limit=limit,
        query=normalized_query,
        document_id=str(active_document_id) if active_document_id else None,
        next_cursor=_encode_session_cursor(page.next_cursor),
        sessions=[_serialize_session(session) for session in page.items],
    )


@router.patch("/chat/session/{session_id}", response_model=RenameSessionResponse)
async def rename_session(
    session_id: str,
    request: RenameSessionRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
):
    """Rename one durable chat session visible to the authenticated user."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    try:
        session = repository.rename_session(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            title=request.title,
        )
        if session is None:
            db.rollback()
            raise HTTPException(status_code=404, detail="Chat session not found")
        db.commit()
    except HTTPException:
        raise
    except ValueError as exc:
        _rollback_and_raise(db, status_code=400, detail=str(exc), exc=exc)
    except Exception as exc:
        logger.error(
            "Failed to rename chat session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to rename chat session",
            exc=exc,
        )

    return RenameSessionResponse(session=_serialize_session(session))


@router.delete("/chat/session/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
) -> Response:
    """Soft-delete one durable chat session visible to the authenticated user."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)

    try:
        deleted = repository.soft_delete_session(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
        )
        if not deleted:
            db.rollback()
            raise HTTPException(status_code=404, detail="Chat session not found")
        db.commit()
    except HTTPException:
        raise
    except ValueError as exc:
        _rollback_and_raise(db, status_code=400, detail=str(exc), exc=exc)
    except Exception as exc:
        logger.error(
            "Failed to delete chat session %s",
            session_id,
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to delete chat session",
            exc=exc,
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chat/session/bulk-delete", response_model=BulkDeleteSessionsResponse)
async def bulk_delete_sessions(
    request: BulkDeleteSessionsRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
):
    """Soft-delete multiple durable chat sessions visible to the authenticated user."""

    user_id = _require_user_sub(user)
    repository = _get_chat_history_repository(db)
    seen_session_ids: set[str] = set()
    normalized_session_ids: List[str] = []

    for raw_session_id in request.session_ids:
        normalized_session_id = raw_session_id.strip()
        if not normalized_session_id:
            raise HTTPException(status_code=400, detail="session_ids cannot include blank values")
        if normalized_session_id in seen_session_ids:
            continue
        seen_session_ids.add(normalized_session_id)
        normalized_session_ids.append(normalized_session_id)

    deleted_session_ids: List[str] = []
    try:
        for target_session_id in normalized_session_ids:
            if repository.soft_delete_session(
                session_id=target_session_id,
                user_auth_sub=user_id,
                chat_kind=ASSISTANT_CHAT_KIND,
            ):
                deleted_session_ids.append(target_session_id)
        db.commit()
    except Exception as exc:
        logger.error(
            "Failed to bulk delete chat sessions",
            extra={"user_id": user_id, "requested_count": len(normalized_session_ids)},
            exc_info=True,
        )
        _rollback_and_raise(
            db,
            status_code=500,
            detail="Failed to delete chat sessions",
            exc=exc,
        )

    return BulkDeleteSessionsResponse(
        requested_count=len(normalized_session_ids),
        deleted_count=len(deleted_session_ids),
        deleted_session_ids=deleted_session_ids,
    )
