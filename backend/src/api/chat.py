"""Chat API endpoints using OpenAI Agents SDK.

This module provides chat endpoints for the AI Curation Prototype,
using the OpenAI Agents SDK for multi-agent orchestration.

Architecture:
- Supervisor agent routes queries to domain specialists
- Bidirectional handoffs enable multi-step query handling
- Specialists: PDF, Disease Ontology, Gene Curation, Chemical Ontology
"""

import json
import logging
import uuid
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .auth import get_auth_dependency
from ..lib.chat_state import document_state
from ..lib.curation_workspace import (
    ExtractionEnvelopeCandidate,
    build_extraction_envelope_candidate,
    persist_extraction_results,
)
from ..lib.curation_workspace.extraction_results import get_agent_curation_metadata
from ..lib.weaviate_client.documents import get_document
from ..lib.conversation_manager import conversation_manager, SessionAccessError
from ..lib.openai_agents import run_agent_streamed
from ..lib.openai_agents.agents.supervisor_agent import get_supervisor_tool_agent_map
from ..lib.openai_agents.evidence_summary import (
    build_record_evidence_summary_record,
    normalize_evidence_records,
)
from ..lib.flows.executor import execute_flow
from ..models.sql import get_db, CurationFlow
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
    model: Optional[str] = "gpt-4o"
    specialist_model: Optional[str] = "gpt-4o-mini"

    # Temperature settings (0.0=deterministic, 1.0=creative)
    supervisor_temperature: Optional[float] = 0.1  # Low for deterministic routing
    specialist_temperature: Optional[float] = 0.3  # Slightly warmer for responses

    # Reasoning effort for GPT-5 models ("minimal", "low", "medium", "high")
    # Only applies when using gpt-5 family models
    supervisor_reasoning: Optional[str] = "medium"  # Thinking time for routing decisions
    specialist_reasoning: Optional[str] = "low"  # Less thinking for direct answers


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
        ]
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
    """Response model for session creation."""
    session_id: str
    created_at: str


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


class SessionHistoryResponse(BaseModel):
    """Response model for session history."""
    session_id: str
    exchange_count: int
    max_exchanges: int
    history: List[Dict[str, Any]]


class AllSessionsStatsResponse(BaseModel):
    """Response model for all sessions statistics."""
    total_sessions: int
    max_sessions: int
    history_enabled: bool
    max_exchanges_per_session: int
    sessions: List[str]


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


def _build_active_document(document_payload: Dict[str, Any]) -> ActiveDocument:
    """Convert stored document payload to response model."""
    return ActiveDocument(
        id=str(document_payload.get("id") or ""),
        filename=document_payload.get("filename"),
        chunk_count=document_payload.get("chunk_count") or document_payload.get("chunkCount"),
        vector_count=document_payload.get("vector_count") or document_payload.get("vectorCount"),
        metadata=document_payload.get("metadata") if isinstance(document_payload.get("metadata"), dict) else None,
    )


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
async def create_session(user: Dict[str, Any] = get_auth_dependency()):
    """Create a new chat session with a unique UUID."""
    from datetime import datetime

    session_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat()

    logger.info(
        "Created new session: %s",
        session_id,
        extra={"session_id": session_id, "user_id": user.get("sub")},
    )
    return SessionResponse(session_id=session_id, created_at=created_at)


# Chat Endpoints (using OpenAI Agents SDK)

@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(chat_message: ChatMessage, user: Dict[str, Any] = get_auth_dependency()):
    """Process a chat message and return a response (non-streaming)."""
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
            "Supervisor tool-map resolution failed; aborting chat run to prevent silent extraction data loss",
            extra={"session_id": session_id, "user_id": user_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Internal configuration error: unable to process chat request",
        ) from exc

    try:
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
            user_message=chat_message.message,
            user_id=user_id,
            session_id=session_id,
            document_id=document_id,
            document_name=document_name,
            conversation_history=conversation_history,
            active_groups=active_groups,
        ):
            event_type = event.get("type")
            event_data = event.get("data", {}) or {}

            if event_type == "RUN_STARTED" and "trace_id" in event_data:
                trace_id = event_data.get("trace_id")

            candidate = _build_extraction_candidate_from_tool_event(
                event,
                tool_agent_map=tool_agent_map,
                conversation_summary=chat_message.message,
                metadata={"document_name": document_name} if document_name else None,
            )
            if candidate:
                extraction_candidates.append(candidate)

            if event_type == "RUN_FINISHED":
                full_response = event_data.get("response", "")
                run_finished = True
                break
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
            _persist_extraction_candidates(
                candidates=extraction_candidates,
                document_id=document_id,
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
                source_kind=CurationExtractionSourceKind.CHAT,
            )

        # Save to conversation history
        conversation_manager.add_exchange(user_id, session_id, chat_message.message, full_response)

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


@router.get("/chat/history/{session_id}", response_model=SessionHistoryResponse)
async def get_session_history(session_id: str, user: Dict[str, Any] = get_auth_dependency()):
    """Get conversation history for a session owned by the authenticated user."""
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

    try:
        stats = conversation_manager.get_session_stats(user_id, session_id)
        return SessionHistoryResponse(**stats)
    except SessionAccessError as e:
        logger.warning(
            "Session access denied: %s",
            e,
            extra={"session_id": session_id, "user_id": user_id},
        )
        raise HTTPException(status_code=403, detail="Access denied: session belongs to another user")


@router.delete("/chat/history/{session_id}")
async def clear_session_history(session_id: str, user: Dict[str, Any] = get_auth_dependency()):
    """Clear conversation history for a session owned by the authenticated user."""
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

    try:
        conversation_manager.clear_session_history(user_id, session_id)
        return {"message": f"History cleared for session {session_id}"}
    except SessionAccessError as e:
        logger.warning(
            "Session access denied: %s",
            e,
            extra={"session_id": session_id, "user_id": user_id},
        )
        raise HTTPException(status_code=403, detail="Access denied: session belongs to another user")


@router.get("/chat/history", response_model=AllSessionsStatsResponse)
async def get_all_sessions_stats(user: Dict[str, Any] = get_auth_dependency()):
    """Get statistics for all chat sessions belonging to the authenticated user."""
    user_id = user.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="User identifier not found in token")

    stats = conversation_manager.get_all_sessions_stats(user_id)
    return AllSessionsStatsResponse(**stats)


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
