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

from .auth import get_auth_dependency
from ..lib.chat_state import document_state
from ..lib.weaviate_client.documents import get_document
from ..lib.conversation_manager import conversation_manager, SessionAccessError
from ..lib.openai_agents import run_agent_streamed
from ..lib.flows.executor import execute_flow
from ..models.sql import get_db, CurationFlow
from ..schemas.flows import ExecuteFlowRequest
from ..services.user_service import set_global_user_from_cognito
from config.mod_rules import get_mods_from_cognito_groups
from ..lib.redis_client import (
    set_cancel_signal,
    check_cancel_signal,
    clear_cancel_signal,
    register_active_stream,
    unregister_active_stream,
    is_stream_active,
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
    logger.info(f"[LOAD] Loading document for chat: {payload.document_id}")

    try:
        document_detail = await get_document(user['sub'], payload.document_id)
        logger.info(f"[LOAD] Successfully retrieved document: {payload.document_id}")
    except ValueError as exc:
        logger.warning(f"[LOAD] Document not found: {payload.document_id}")
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"[LOAD] Error loading document {payload.document_id}: {exc}")
        raise HTTPException(status_code=500, detail="Failed to load document for chat") from exc

    document_summary = document_detail.get("document")
    if not document_summary:
        logger.error(f"Document payload missing summary for {payload.document_id}")
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

    logger.info(f"Created new session: {session_id}")
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

    # Extract active MODs from user's Cognito groups for prompt injection
    # Note: Cognito uses "cognito:groups" as the claim key
    cognito_groups = user.get("cognito:groups", [])
    active_mods = get_mods_from_cognito_groups(cognito_groups)
    if active_mods:
        logger.info(f"[chat] User has active MODs: {active_mods} (from groups: {cognito_groups})")

    try:
        # Retrieve conversation history for multi-turn context
        conversation_history = _get_conversation_history_for_session(user_id, session_id)
        if conversation_history:
            logger.info(f"[chat] Including {len(conversation_history)} history messages for session {session_id}")

        # Collect full response from streaming generator
        full_response = ""
        error_message = None

        async for event in run_agent_streamed(
            user_message=chat_message.message,
            user_id=user_id,
            session_id=session_id,
            document_id=document_id,
            document_name=document_name,
            conversation_history=conversation_history,
            active_mods=active_mods,
        ):
            event_type = event.get("type")

            if event_type == "RUN_FINISHED":
                full_response = event.get("data", {}).get("response", "")
                break
            elif event_type == "RUN_ERROR":
                # Capture error and stop processing
                error_message = event.get("data", {}).get("message", "Unknown error")
                logger.error(f"Agent error during non-streaming chat: {error_message}")
                break

        # If we got an error, raise it
        if error_message:
            raise HTTPException(status_code=500, detail=error_message)

        # Save to conversation history
        conversation_manager.add_exchange(user_id, session_id, chat_message.message, full_response)

        return ChatResponse(response=full_response, session_id=session_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat error: {e}", exc_info=True)
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

    doc_info = f"document={document_id[:8]}..." if document_id else "no document"
    logger.info(f"[chat/stream] Request from user {user_id[:8]}..., {doc_info}")

    # Extract active MODs from user's Cognito groups for prompt injection
    # Note: Cognito uses "cognito:groups" as the claim key
    cognito_groups = user.get("cognito:groups", [])
    active_mods = get_mods_from_cognito_groups(cognito_groups)
    if active_mods:
        logger.info(f"[chat/stream] User has active MODs: {active_mods} (from groups: {cognito_groups})")

    # Retrieve conversation history for multi-turn context
    conversation_history = _get_conversation_history_for_session(user_id, session_id)
    if conversation_history:
        logger.info(f"[chat/stream] Including {len(conversation_history)} history messages for session {session_id}")

    # Create local cancellation event (for immediate same-worker cancellation)
    cancel_event = asyncio.Event()
    _LOCAL_CANCEL_EVENTS[session_id] = cancel_event

    async def generate_stream():
        """Generate SSE events from the agent runner."""
        current_session_id = session_id
        full_response = ""
        trace_id = None  # Capture trace_id for error reporting

        try:
            # Register stream as active in Redis (for cross-worker visibility)
            await register_active_stream(current_session_id)

            async for event in run_agent_streamed(
                user_message=chat_message.message,
                user_id=user_id,
                session_id=current_session_id,
                document_id=document_id,
                document_name=document_name,
                conversation_history=conversation_history,
                active_mods=active_mods,
            ):
                # Check for cancellation (local event OR Redis signal)
                if cancel_event.is_set() or await check_cancel_signal(current_session_id):
                    logger.info(f"Chat stream cancelled for session {current_session_id}")
                    yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': 'Run cancelled by user', 'session_id': current_session_id})}\n\n"
                    break

                # Flatten event: merge data fields to top level for frontend compatibility
                # Frontend expects: {type, delta, content, trace_id, session_id, ...}
                # Runner sends: {type, data: {delta, trace_id, ...}}
                # Audit events send: {type, timestamp, details}
                event_type = event.get("type")
                event_data = event.get("data", {})

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

                # Capture response for history
                if event_type == "RUN_FINISHED":
                    full_response = event_data.get("response", "")

                yield f"data: {json.dumps(flat_event, default=str)}\n\n"

            # Save to conversation history
            if full_response:
                conversation_manager.add_exchange(user_id, current_session_id, chat_message.message, full_response)

        except asyncio.CancelledError:
            logger.warning(f"Chat stream cancelled unexpectedly for session {current_session_id}, trace_id={trace_id}")
            # Emit audit event so it's visible in the audit panel
            yield f"data: {json.dumps({'type': 'SUPERVISOR_ERROR', 'timestamp': datetime.now(timezone.utc).isoformat(), 'details': {'error': 'Stream cancelled unexpectedly', 'context': 'asyncio.CancelledError', 'message': 'The request was interrupted. Please provide feedback using the ⋮ menu, then try your query again.'}, 'session_id': current_session_id})}\n\n"
            # Emit RUN_ERROR with trace_id for feedback reporting
            yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': 'The request was interrupted unexpectedly. Please provide feedback using the ⋮ menu on this message, then try your query again.', 'error_type': 'StreamCancelled', 'trace_id': trace_id, 'session_id': current_session_id})}\n\n"
        except Exception as e:
            logger.error(f"Stream error: {e}, trace_id={trace_id}", exc_info=True)
            # Emit audit event so it's visible in the audit panel
            yield f"data: {json.dumps({'type': 'SUPERVISOR_ERROR', 'timestamp': datetime.now(timezone.utc).isoformat(), 'details': {'error': str(e), 'context': type(e).__name__, 'message': f'An error occurred. Please provide feedback using the ⋮ menu, then try your query again.'}, 'session_id': current_session_id})}\n\n"
            # Emit RUN_ERROR with trace_id for feedback reporting
            yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': f'An error occurred. Please provide feedback using the ⋮ menu on this message, then try your query again.', 'error_type': type(e).__name__, 'trace_id': trace_id, 'session_id': current_session_id})}\n\n"
        finally:
            # Cleanup: remove from local dict, unregister from Redis, clear any cancel signal
            _LOCAL_CANCEL_EVENTS.pop(session_id, None)
            await unregister_active_stream(current_session_id)
            await clear_cancel_signal(current_session_id)

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
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

    # Check if stream is active (either locally or in Redis)
    local_event = _LOCAL_CANCEL_EVENTS.get(session_id)
    stream_active = await is_stream_active(session_id)

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

    # Update execution stats
    flow.execution_count += 1
    flow.last_executed_at = datetime.now(timezone.utc)
    db.commit()

    # Extract active MODs from user's Cognito groups for prompt injection
    cognito_groups = user.get("cognito:groups", [])
    active_mods = get_mods_from_cognito_groups(cognito_groups)
    if active_mods:
        logger.info(f"[execute-flow] User has active MODs: {active_mods}")

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
        f"[execute-flow] Starting flow execution: flow_id={request.flow_id}, "
        f"flow_name='{flow.name}', session_id={request.session_id}, "
        f"user_id={user_id[:8]}..., document_id={request.document_id}, "
        f"document_name={document_name}"
    )

    # Create local cancellation event (for immediate same-worker cancellation)
    cancel_event = asyncio.Event()
    _LOCAL_CANCEL_EVENTS[request.session_id] = cancel_event

    # Stream events via SSE with cancellation support
    async def event_generator():
        """Generate SSE events from flow execution with cancellation support."""
        current_session_id = request.session_id
        trace_id = None

        try:
            # Register stream as active in Redis (for cross-worker visibility)
            await register_active_stream(current_session_id)

            async for event in execute_flow(
                flow=flow,
                user_id=user_id,
                session_id=current_session_id,
                document_id=str(request.document_id) if request.document_id else None,
                document_name=document_name,
                user_query=request.user_query,
                active_mods=active_mods,
            ):
                # Check for cancellation (local event OR Redis signal)
                if cancel_event.is_set() or await check_cancel_signal(current_session_id):
                    logger.info(f"Flow execution cancelled for session {current_session_id}")
                    yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': 'Flow execution cancelled by user', 'session_id': current_session_id})}\n\n"
                    break

                # Flatten event: merge data fields to top level for frontend compatibility
                # Frontend expects: {type, delta, content, trace_id, session_id, ...}
                # Executor sends: {type, data: {...}, timestamp?, details?}
                # Audit panel expects: {type, timestamp, sessionId, details}
                event_type = event.get("type")
                event_data = event.get("data", {})

                flat_event = {"type": event_type, "session_id": current_session_id, "sessionId": current_session_id}
                flat_event.update(event_data)  # Merge all data fields to top level

                # Preserve audit event fields (timestamp, details) if present at top level
                if "timestamp" in event:
                    flat_event["timestamp"] = event["timestamp"]
                if "details" in event:
                    flat_event["details"] = event["details"]

                # Capture trace_id from RUN_STARTED event for error reporting
                if event_type == "RUN_STARTED" and "trace_id" in event_data:
                    trace_id = event_data.get("trace_id")

                yield f"data: {json.dumps(flat_event, default=str)}\n\n"

        except asyncio.CancelledError:
            logger.warning(f"Flow execution cancelled unexpectedly for session {current_session_id}, trace_id={trace_id}")
            yield f"data: {json.dumps({'type': 'SUPERVISOR_ERROR', 'timestamp': datetime.now(timezone.utc).isoformat(), 'details': {'error': 'Flow cancelled unexpectedly', 'context': 'asyncio.CancelledError'}, 'session_id': current_session_id})}\n\n"
            yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': 'Flow execution was interrupted unexpectedly.', 'error_type': 'StreamCancelled', 'trace_id': trace_id, 'session_id': current_session_id})}\n\n"
        except Exception as e:
            logger.error(f"Flow execution error: {e}, trace_id={trace_id}", exc_info=True)
            yield f"data: {json.dumps({'type': 'SUPERVISOR_ERROR', 'timestamp': datetime.now(timezone.utc).isoformat(), 'details': {'error': str(e), 'context': type(e).__name__}, 'session_id': current_session_id})}\n\n"
            yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': f'Flow execution error: {str(e)}', 'error_type': type(e).__name__, 'trace_id': trace_id, 'session_id': current_session_id})}\n\n"
        finally:
            # Cleanup: remove from local dict, unregister from Redis, clear any cancel signal
            _LOCAL_CANCEL_EVENTS.pop(request.session_id, None)
            await unregister_active_stream(current_session_id)
            await clear_cancel_signal(current_session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
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
        logger.error(f"Failed to get conversation status: {e}")
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
        logger.error(f"Failed to reset conversation: {e}")
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
        logger.warning(f"Session access denied: {e}")
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
        logger.warning(f"Session access denied: {e}")
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
