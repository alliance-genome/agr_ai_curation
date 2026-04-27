# ruff: noqa: F401,F403,F405
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
from typing import Any, Callable, Dict, List, Literal, NoReturn, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask
from sqlalchemy.exc import SQLAlchemyError

from .auth import get_auth_dependency
from .chat_models import *
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
from ..lib.http_errors import log_exception, raise_sanitized_http_exception
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

logger = logging.getLogger("src.api.chat")

# Create router with prefix
router = APIRouter(prefix="/api")


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


# Local cancel events for immediate in-process cancellation.
# Redis handles cross-worker cancellation coordination.
_LOCAL_CANCEL_EVENTS: Dict[str, asyncio.Event] = {}
_LOCAL_SESSION_OWNERS: Dict[str, str] = {}
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


def _rollback_and_raise(
    db: Session,
    *,
    status_code: int,
    detail: str,
    exc: Exception,
    log_message: str | None = None,
    level: int = logging.ERROR,
) -> NoReturn:
    """Rollback the current transaction and raise an HTTP exception."""

    db.rollback()
    if log_message is not None:
        log_exception(logger, message=log_message, exc=exc, level=level)
    raise HTTPException(status_code=status_code, detail=detail) from exc


def _stream_error_details(
    *,
    error: str,
    exc: Exception,
    message: str | None = None,
) -> Dict[str, str]:
    details = {
        "error": error,
        "context": type(exc).__name__,
    }
    if message is not None:
        details["message"] = message
    return details


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


__all__ = [name for name in globals() if not name.startswith("__")]
