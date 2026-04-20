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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .auth import get_auth_dependency
from ..lib.chat_history_repository import (
    ChatHistoryRepository,
    ChatMessageCursor,
    ChatMessageRecord,
    ChatSessionCursor,
    ChatSessionRecord,
)
from ..lib.chat_state import document_state
from ..lib.curation_workspace import (
    ExtractionEnvelopeCandidate,
    build_extraction_envelope_candidate,
    persist_extraction_results,
)
from ..lib.curation_workspace.extraction_results import get_agent_curation_metadata
from ..lib.conversation_manager import conversation_manager
from ..lib.openai_agents import run_agent_streamed
from ..lib.openai_agents.agents.supervisor_agent import get_supervisor_tool_agent_map
from ..lib.openai_agents.evidence_summary import (
    build_record_evidence_summary_record,
    normalize_evidence_records,
)
from ..lib.flows.executor import execute_flow
from ..lib.weaviate_client.documents import get_document
from ..models.sql import CurationFlow, get_db
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


def _get_conversation_history_for_session(user_id: str, session_id: str) -> List[Dict[str, str]]:
    """
    Retrieve conversation history from conversation_manager and format for OpenAI.

    Converts from exchange format {'user': ..., 'assistant': ...}
    to OpenAI message format [{'role': 'user', 'content': ...}, ...]

    Args:
        user_id: User identifier (Cognito sub claim)
        session_id: Session identifier
    """
    if not conversation_manager.history_enabled:
        return []

    history = conversation_manager.get_session_history(user_id, session_id)
    if not history:
        return []

    messages = []
    for exchange in history:
        # Each exchange has 'user' and 'assistant' keys
        if exchange.get('user'):
            messages.append({'role': 'user', 'content': exchange['user']})
        if exchange.get('assistant'):
            messages.append({'role': 'assistant', 'content': exchange['assistant']})

    return messages


def _collect_durable_text_exchanges(
    messages: List[ChatMessageRecord],
    *,
    pending_user_message: Optional[str] = None,
) -> tuple[List[tuple[str, str]], Optional[str]]:
    """Pair durable text transcript rows into user/assistant exchanges."""

    exchanges: List[tuple[str, str]] = []

    for message in messages:
        if message.message_type != "text" or not message.content.strip():
            continue

        if message.role == "user":
            pending_user_message = message.content
            continue

        if message.role != "assistant" or pending_user_message is None:
            continue

        exchanges.append((pending_user_message, message.content))
        pending_user_message = None

    return exchanges, pending_user_message


def _hydrate_conversation_history_from_durable_messages(
    repository: ChatHistoryRepository,
    *,
    user_id: str,
    session_id: str,
) -> None:
    """Synchronize in-memory chat history with the durable text transcript."""

    if not conversation_manager.history_enabled:
        return

    add_exchange = conversation_manager.add_exchange

    durable_exchanges: List[tuple[str, str]] = []
    pending_user_message: Optional[str] = None
    message_cursor: Optional[ChatMessageCursor] = None

    while True:
        message_page = repository.list_messages(
            session_id=session_id,
            user_auth_sub=user_id,
            limit=200,
            cursor=message_cursor,
        )
        if not message_page.items:
            break

        page_exchanges, pending_user_message = _collect_durable_text_exchanges(
            messages=message_page.items,
            pending_user_message=pending_user_message,
        )
        durable_exchanges.extend(page_exchanges)

        if message_page.next_cursor is None:
            break
        message_cursor = message_page.next_cursor

    conversation_manager.clear_session_history(user_id, session_id)

    for durable_user_message, durable_assistant_message in durable_exchanges:
        add_exchange(user_id, session_id, durable_user_message, durable_assistant_message)


def _ensure_conversation_history_contains_exchange(
    repository: ChatHistoryRepository,
    *,
    user_id: str,
    session_id: str,
    user_message: str,
    assistant_message: str,
) -> None:
    """Ensure replayed durable exchanges become visible to subsequent prompt history on this worker."""

    if not conversation_manager.history_enabled:
        return

    get_session_history = conversation_manager.get_session_history
    add_exchange = conversation_manager.add_exchange

    session_history = get_session_history(user_id, session_id)
    if not session_history:
        _hydrate_conversation_history_from_durable_messages(
            repository,
            user_id=user_id,
            session_id=session_id,
        )
        return

    if any(
        exchange.get("user") == user_message and exchange.get("assistant") == assistant_message
        for exchange in session_history
    ):
        return

    add_exchange(user_id, session_id, user_message, assistant_message)


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
        final_output_block = f"Flow failed before producing a final output. Reason: {failure_reason or 'Unknown'}"
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


class ChatSessionSummaryResponse(BaseModel):
    """Compact session payload for history browsing and mutations."""

    session_id: str
    title: Optional[str] = None
    active_document_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_message_at: Optional[datetime] = None
    recent_activity_at: datetime


class ChatSessionListResponse(BaseModel):
    """Paginated durable history response."""

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


class ChatConfigResponse(BaseModel):
    """Response model for chat configuration."""
    history: Dict[str, Any]


class StopRequest(BaseModel):
    """Request model for stopping a chat stream."""
    session_id: str


# Local fallback for cancel events (used alongside Redis for immediate in-process cancellation)
# Redis provides cross-worker cancellation; this provides immediate same-worker cancellation
_LOCAL_CANCEL_EVENTS: Dict[str, asyncio.Event] = {}
_LOCAL_SESSION_OWNERS: Dict[str, str] = {}
_LOCAL_NON_STREAM_TURN_OWNERS: Dict[str, str] = {}


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


def _serialize_session(record: ChatSessionRecord) -> ChatSessionSummaryResponse:
    """Convert a repository session record into the API summary payload."""

    return ChatSessionSummaryResponse(
        session_id=record.session_id,
        title=record.title,
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
        payload_json = dict(record.payload_json)
    elif isinstance(record.payload_json, list):
        payload_json = list(record.payload_json)

    return ChatSessionMessageResponse(
        message_id=str(record.message_id),
        session_id=record.session_id,
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
            active_document_id=active_document_id,
        )
        user_turn = repository.append_message(
            session_id=session_id,
            user_auth_sub=user_id,
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
            _ensure_conversation_history_contains_exchange(
                repository,
                user_id=user_id,
                session_id=session_id,
                user_message=effective_user_message,
                assistant_message=assistant_turn.content,
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
        _hydrate_conversation_history_from_durable_messages(
            repository,
            user_id=user_id,
            session_id=session_id,
        )
        # Retrieve conversation history for multi-turn context
        conversation_history = _get_conversation_history_for_session(user_id, session_id)
        if conversation_history:
            logger.info(
                "Including %s history messages for session %s",
                len(conversation_history),
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
            user_message=effective_user_message,
            user_id=user_id,
            session_id=session_id,
            document_id=document_id,
            document_name=document_name,
            conversation_history=conversation_history,
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
                    role="assistant",
                    content=full_response,
                    turn_id=chat_message.turn_id,
                    trace_id=trace_id,
                )
                if chat_message.turn_id and not assistant_turn.created:
                    db.rollback()
                    _ensure_conversation_history_contains_exchange(
                        repository,
                        user_id=user_id,
                        session_id=session_id,
                        user_message=effective_user_message,
                        assistant_message=assistant_turn.message.content,
                    )
                    logger.info(
                        "Discarding duplicate non-stream completion for replayed turn %s",
                        chat_message.turn_id,
                        extra={"session_id": session_id, "user_id": user_id, "turn_id": chat_message.turn_id},
                    )
                    return ChatResponse(response=assistant_turn.message.content, session_id=session_id)
                db.commit()
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

        # Save to conversation history
        conversation_manager.add_exchange(user_id, session_id, effective_user_message, full_response)

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
async def chat_stream_endpoint(chat_message: ChatMessage, user: Dict[str, Any] = get_auth_dependency()):
    """Stream a chat response using Server-Sent Events."""
    session_id = chat_message.session_id or str(uuid.uuid4())
    user_id = user.get("sub")

    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

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

    # Retrieve conversation history for multi-turn context
    conversation_history = _get_conversation_history_for_session(user_id, session_id)
    if conversation_history:
        logger.info(
            "Including %s history messages for session %s",
            len(conversation_history),
            session_id,
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

    # Create local cancellation event (for immediate same-worker cancellation)
    stream_claim_token = str(uuid.uuid4())
    existing_owner = _LOCAL_SESSION_OWNERS.get(session_id)
    if existing_owner:
        if existing_owner != user_id:
            raise HTTPException(status_code=403, detail="Session is active for a different user")
        raise HTTPException(status_code=409, detail="Session is already active")
    _LOCAL_SESSION_OWNERS[session_id] = user_id

    cancel_event = asyncio.Event()
    _LOCAL_CANCEL_EVENTS[session_id] = cancel_event

    if not await register_active_stream(session_id, user_id=user_id, stream_token=stream_claim_token):
        _LOCAL_CANCEL_EVENTS.pop(session_id, None)
        _LOCAL_SESSION_OWNERS.pop(session_id, None)
        raise HTTPException(status_code=403, detail="Session is active for a different user")

    cleanup_done = False

    async def _cleanup_stream_state(target_session_id: str) -> None:
        """Best-effort idempotent cleanup for active stream bookkeeping."""
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        _LOCAL_CANCEL_EVENTS.pop(target_session_id, None)
        if _LOCAL_SESSION_OWNERS.get(target_session_id) == user_id:
            _LOCAL_SESSION_OWNERS.pop(target_session_id, None)
        await unregister_active_stream(target_session_id, user_id=user_id, stream_token=stream_claim_token)
        await clear_cancel_signal(target_session_id)

    async def generate_stream():
        """Generate SSE events from the agent runner."""
        current_session_id = session_id
        full_response = ""
        trace_id = None  # Capture trace_id for error reporting
        run_finished = False
        pending_run_finished_event: Optional[Dict[str, Any]] = None
        extraction_candidates: List[ExtractionEnvelopeCandidate] = []
        evidence_records: List[Dict[str, Any]] = []
        evidence_summary_event_received = False

        try:
            async for event in run_agent_streamed(
                user_message=chat_message.message,
                user_id=user_id,
                session_id=current_session_id,
                document_id=document_id,
                document_name=document_name,
                conversation_history=conversation_history,
                active_groups=active_groups,
                supervisor_model=chat_message.model,
                specialist_model=chat_message.specialist_model,
                supervisor_temperature=chat_message.supervisor_temperature,
                specialist_temperature=chat_message.specialist_temperature,
                supervisor_reasoning=chat_message.supervisor_reasoning,
                specialist_reasoning=chat_message.specialist_reasoning,
            ):
                # Check for cancellation (local event OR Redis signal)
                if cancel_event.is_set() or await check_cancel_signal(current_session_id):
                    logger.info(
                        "Chat stream cancelled for session %s",
                        current_session_id,
                        extra={"session_id": current_session_id, "user_id": user_id, "trace_id": trace_id},
                    )
                    yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': 'Run cancelled by user', 'session_id': current_session_id})}\n\n"
                    break

                # Flatten event: merge data fields to top level for frontend compatibility
                # Frontend expects: {type, delta, content, trace_id, session_id, ...}
                # Runner sends: {type, data: {delta, trace_id, ...}}
                # Audit events send: {type, timestamp, details}
                event_type = event.get("type")
                event_data = event.get("data", {}) or {}

                flat_event = {"type": event_type, "session_id": current_session_id, "sessionId": current_session_id}
                flat_event.update(event_data)  # Merge all data fields to top level

                # Preserve audit event fields (timestamp, details) if present at top level
                if "timestamp" in event:
                    flat_event["timestamp"] = event["timestamp"]
                if "details" in event:
                    flat_event["details"] = event["details"]

                # CRITICAL: For CHUNK_PROVENANCE, copy top-level fields that aren't in event_data
                if event_type == "CHUNK_PROVENANCE":
                    for key in ["chunk_id", "doc_items", "message_id", "source_tool"]:
                        if key in event and key not in flat_event:
                            flat_event[key] = event[key]

                # Capture trace_id for error reporting (from RUN_STARTED event)
                if event_type == "RUN_STARTED" and "trace_id" in event_data:
                    trace_id = event_data.get("trace_id")

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
                    yield f"data: {json.dumps(flat_event, default=str)}\n\n"
                    continue

                candidate = _build_extraction_candidate_from_tool_event(
                    event,
                    tool_agent_map=tool_agent_map,
                    conversation_summary=chat_message.message,
                    metadata={"document_name": document_name} if document_name else None,
                )
                if candidate:
                    extraction_candidates.append(candidate)

                if not evidence_summary_event_received:
                    evidence_record = _build_evidence_record_from_tool_event(event)
                    if evidence_record:
                        evidence_records.append(evidence_record)

                # Capture response for history
                if event_type == "RUN_FINISHED":
                    full_response = event_data.get("response", "")
                    run_finished = True
                    pending_run_finished_event = flat_event
                    continue

                yield f"data: {json.dumps(flat_event, default=str)}\n\n"

            # Save to conversation history
            if run_finished:
                if evidence_records and not evidence_summary_event_received:
                    evidence_curation_metadata = _build_candidate_evidence_curation_metadata(
                        extraction_candidates,
                    )
                    yield (
                        "data: "
                        f"{json.dumps({'type': 'evidence_summary', 'timestamp': datetime.now(timezone.utc).isoformat(), 'session_id': current_session_id, 'sessionId': current_session_id, 'evidence_records': evidence_records, **evidence_curation_metadata}, default=str)}\n\n"
                    )
                _persist_extraction_candidates(
                    candidates=extraction_candidates,
                    document_id=document_id,
                    user_id=user_id,
                    session_id=current_session_id,
                    trace_id=trace_id,
                    source_kind=CurationExtractionSourceKind.CHAT,
                )
                if pending_run_finished_event:
                    yield (
                        f"data: {json.dumps(pending_run_finished_event, default=str)}\n\n"
                    )
            if full_response:
                conversation_manager.add_exchange(user_id, current_session_id, chat_message.message, full_response)

        except asyncio.CancelledError:
            logger.warning(
                "Chat stream cancelled unexpectedly for session %s",
                current_session_id,
                extra={"session_id": current_session_id, "user_id": user_id, "trace_id": trace_id},
            )
            # Emit audit event so it's visible in the audit panel
            yield f"data: {json.dumps({'type': 'SUPERVISOR_ERROR', 'timestamp': datetime.now(timezone.utc).isoformat(), 'details': {'error': 'Stream cancelled unexpectedly', 'context': 'asyncio.CancelledError', 'message': 'The request was interrupted. Please provide feedback using the ⋮ menu, then try your query again.'}, 'session_id': current_session_id})}\n\n"
            # Emit RUN_ERROR with trace_id for feedback reporting
            yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': 'The request was interrupted unexpectedly. Please provide feedback using the ⋮ menu on this message, then try your query again.', 'error_type': 'StreamCancelled', 'trace_id': trace_id, 'session_id': current_session_id})}\n\n"
        except Exception as e:
            logger.error(
                "Stream error: %s",
                e,
                extra={"session_id": current_session_id, "user_id": user_id, "trace_id": trace_id},
                exc_info=True,
            )
            # Emit audit event so it's visible in the audit panel
            yield f"data: {json.dumps({'type': 'SUPERVISOR_ERROR', 'timestamp': datetime.now(timezone.utc).isoformat(), 'details': {'error': str(e), 'context': type(e).__name__, 'message': 'An error occurred. Please provide feedback using the ⋮ menu, then try your query again.'}, 'session_id': current_session_id})}\n\n"
            # Emit RUN_ERROR with trace_id for feedback reporting
            yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': 'An error occurred. Please provide feedback using the ⋮ menu on this message, then try your query again.', 'error_type': type(e).__name__, 'trace_id': trace_id, 'session_id': current_session_id})}\n\n"
        finally:
            # Cleanup: remove from local dict, unregister from Redis, clear any cancel signal
            await _cleanup_stream_state(current_session_id)

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        background=BackgroundTask(_cleanup_stream_state, session_id),
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
    # Get database user from Cognito token
    db_user = set_global_user_from_cognito(db, user)

    # Fetch flow and verify ownership
    flow = db.query(CurationFlow).filter(
        CurationFlow.id == request.flow_id,
        CurationFlow.is_active == True,  # noqa: E712 - SQLAlchemy requires == for SQL
    ).first()

    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    if flow.user_id != db_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Extract active groups from user's Cognito groups for prompt injection
    cognito_groups = user.get("cognito:groups", [])
    active_groups = get_groups_from_cognito(cognito_groups)
    if active_groups:
        logger.info(
            "User has active groups: %s",
            active_groups,
            extra={"session_id": request.session_id, "user_id": user.get('sub')},
        )

    # Use Cognito sub (not db_user.id) for Weaviate tenant isolation
    # This matches how chat endpoints work - Weaviate tenants use the Cognito subject ID
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

    # Set context variables for file output tools
    set_current_session_id(request.session_id)
    set_current_user_id(user_id)

    # Get document name from active document state (matches chat behavior)
    active_doc = document_state.get_document(user_id)
    document_name = active_doc.get("filename") if active_doc else None

    logger.info(
        "Starting flow execution: flow_id=%s flow_name=%s document_id=%s document_name=%s",
        request.flow_id,
        flow.name,
        request.document_id,
        document_name,
        extra={"session_id": request.session_id, "user_id": user_id},
    )

    # Create local cancellation event (for immediate same-worker cancellation)
    stream_claim_token = str(uuid.uuid4())
    existing_owner = _LOCAL_SESSION_OWNERS.get(request.session_id)
    if existing_owner:
        if existing_owner != user_id:
            raise HTTPException(status_code=403, detail="Session is active for a different user")
        raise HTTPException(status_code=409, detail="Session is already active")
    _LOCAL_SESSION_OWNERS[request.session_id] = user_id

    cancel_event = asyncio.Event()
    _LOCAL_CANCEL_EVENTS[request.session_id] = cancel_event

    if not await register_active_stream(
        request.session_id,
        user_id=user_id,
        stream_token=stream_claim_token,
    ):
        _LOCAL_CANCEL_EVENTS.pop(request.session_id, None)
        _LOCAL_SESSION_OWNERS.pop(request.session_id, None)
        raise HTTPException(status_code=403, detail="Session is active for a different user")

    cleanup_done = False

    async def _cleanup_stream_state(target_session_id: str) -> None:
        """Best-effort idempotent cleanup for active flow stream bookkeeping."""
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        _LOCAL_CANCEL_EVENTS.pop(target_session_id, None)
        if _LOCAL_SESSION_OWNERS.get(target_session_id) == user_id:
            _LOCAL_SESSION_OWNERS.pop(target_session_id, None)
        await unregister_active_stream(
            target_session_id,
            user_id=user_id,
            stream_token=stream_claim_token,
        )
        await clear_cancel_signal(target_session_id)

    # Update execution stats only after ownership/session checks succeed.
    try:
        flow.execution_count += 1
        flow.last_executed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        logger.error(
            "Failed to persist flow execution start for session %s: %s",
            request.session_id,
            exc,
            extra={"session_id": request.session_id, "user_id": user_id},
            exc_info=True,
        )
        db.rollback()
        await _cleanup_stream_state(request.session_id)
        raise HTTPException(status_code=500, detail="Failed to start flow execution") from exc

    # Stream events via SSE with cancellation support
    async def event_generator():
        """Generate SSE events from flow execution with cancellation support."""
        current_session_id = request.session_id
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
            ):
                # Check for cancellation (local event OR Redis signal)
                if cancel_event.is_set() or await check_cancel_signal(current_session_id):
                    logger.info(
                        "Flow execution cancelled for session %s",
                        current_session_id,
                        extra={"session_id": current_session_id, "user_id": user_id, "trace_id": trace_id},
                    )
                    yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': 'Flow execution cancelled by user', 'session_id': current_session_id})}\n\n"
                    break

                # Flatten event: merge data fields to top level for frontend compatibility
                # Frontend expects: {type, delta, content, trace_id, session_id, ...}
                # Executor sends: {type, data: {...}, timestamp?, details?}
                # Audit panel expects: {type, timestamp, sessionId, details}
                event_type = event.get("type")
                event_data = event.get("data", {}) or {}
                event_details = event.get("details", {}) or {}

                if event_type == "RUN_STARTED" and "trace_id" in event_data:
                    trace_id = event_data.get("trace_id")

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

                flat_event = {"type": event_type, "session_id": current_session_id, "sessionId": current_session_id}
                flat_event.update(event_data)  # Merge all data fields to top level

                # Preserve audit event fields (timestamp, details) if present at top level
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

                yield f"data: {json.dumps(flat_event, default=str)}\n\n"

            if flow_status:
                history_user_message = (request.user_query or "").strip() or f"Run flow '{flow.name}'"
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
                try:
                    conversation_manager.add_exchange(
                        user_id,
                        current_session_id,
                        history_user_message,
                        history_assistant_message,
                    )
                except Exception:
                    logger.warning(
                        "Flow context injection failed for session %s",
                        current_session_id,
                        extra={"session_id": current_session_id, "user_id": user_id},
                        exc_info=True,
                    )

        except asyncio.CancelledError:
            logger.warning(
                "Flow execution cancelled unexpectedly for session %s",
                current_session_id,
                extra={"session_id": current_session_id, "user_id": user_id, "trace_id": trace_id},
            )
            yield f"data: {json.dumps({'type': 'SUPERVISOR_ERROR', 'timestamp': datetime.now(timezone.utc).isoformat(), 'details': {'error': 'Flow cancelled unexpectedly', 'context': 'asyncio.CancelledError'}, 'session_id': current_session_id})}\n\n"
            yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': 'Flow execution was interrupted unexpectedly.', 'error_type': 'StreamCancelled', 'trace_id': trace_id, 'session_id': current_session_id})}\n\n"
        except Exception as e:
            logger.error(
                "Flow execution error: %s",
                e,
                extra={"session_id": current_session_id, "user_id": user_id, "trace_id": trace_id},
                exc_info=True,
            )
            yield f"data: {json.dumps({'type': 'SUPERVISOR_ERROR', 'timestamp': datetime.now(timezone.utc).isoformat(), 'details': {'error': str(e), 'context': type(e).__name__}, 'session_id': current_session_id})}\n\n"
            yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': f'Flow execution error: {str(e)}', 'error_type': type(e).__name__, 'trace_id': trace_id, 'session_id': current_session_id})}\n\n"
        finally:
            # Cleanup: remove from local dict, unregister from Redis, clear any cancel signal
            await _cleanup_stream_state(current_session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        background=BackgroundTask(_cleanup_stream_state, request.session_id),
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
async def get_conversation_status(user: Dict[str, Any] = get_auth_dependency()) -> ConversationStatusResponse:
    """Get the current conversation status and memory statistics for the authenticated user."""
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

    try:
        stats = conversation_manager.get_memory_stats(user_id)
        return ConversationStatusResponse(
            is_active=stats.get("is_active", True),
            conversation_id=stats.get("conversation_id"),
            memory_stats=stats,
            message="Conversation status retrieved successfully"
        )
    except Exception as e:
        logger.error("Failed to get conversation status: %s", e, extra={"user_id": user_id})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/conversation/reset", response_model=ConversationResetResponse)
async def reset_conversation(user: Dict[str, Any] = get_auth_dependency()) -> ConversationResetResponse:
    """Reset the conversation memory for the authenticated user and start a new conversation."""
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

    try:
        success = conversation_manager.reset_conversation(user_id)

        if success:
            stats = conversation_manager.get_memory_stats(user_id)
            new_session_id = str(uuid.uuid4())
            return ConversationResetResponse(
                success=True,
                message="Conversation reset successfully. Use the provided session_id for the next message.",
                memory_stats=stats,
                session_id=new_session_id
            )
        else:
            return ConversationResetResponse(
                success=False,
                message="Failed to reset conversation memory",
                memory_stats=None,
                session_id=None
            )
    except Exception as e:
        logger.error("Failed to reset conversation: %s", e, extra={"user_id": user_id})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/history/{session_id}", response_model=ChatSessionDetailResponse)
async def get_session_history(
    session_id: str,
    message_limit: int = Query(100, ge=1, le=200),
    message_cursor: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
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
    return ChatSessionDetailResponse(
        session=_serialize_session(detail.session),
        active_document=active_document,
        messages=[_serialize_message(message) for message in detail.messages],
        message_limit=message_limit,
        next_message_cursor=_encode_message_cursor(detail.next_message_cursor),
    )


@router.get("/chat/history", response_model=ChatSessionListResponse)
async def get_all_sessions_stats(
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    document_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: Dict[str, Any] = get_auth_dependency(),
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
                query=normalized_query,
                limit=limit,
                cursor=decoded_cursor,
                active_document_id=active_document_id,
            )
            total_sessions = repository.count_sessions(
                user_auth_sub=user_id,
                query=normalized_query,
                active_document_id=active_document_id,
            )
        else:
            page = repository.list_sessions(
                user_auth_sub=user_id,
                limit=limit,
                cursor=decoded_cursor,
                active_document_id=active_document_id,
            )
            total_sessions = repository.count_sessions(
                user_auth_sub=user_id,
                active_document_id=active_document_id,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ChatSessionListResponse(
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


@router.get("/chat/config", response_model=ChatConfigResponse)
async def get_chat_configuration(user: Dict[str, Any] = get_auth_dependency()):
    """Get current chat configuration including history settings."""
    return ChatConfigResponse(
        history={
            "enabled": conversation_manager.history_enabled,
            "max_exchanges": conversation_manager.max_exchanges,
            "include_in_routing": conversation_manager.include_in_routing,
            "include_in_response": conversation_manager.include_in_response,
            "max_sessions_per_user": conversation_manager.max_sessions_per_user
        }
    )
