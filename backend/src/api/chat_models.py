"""Request, response, and durable turn models for chat endpoints."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from ..lib.chat_history_repository import ChatMessageRecord


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



__all__ = [name for name in globals() if not name.startswith("__")]
