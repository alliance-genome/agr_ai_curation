"""Owner-scoped benchmark conversations; no benchmark execution endpoints."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from anyio.to_thread import run_sync
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from src.api.benchmark_curator import require_benchmark_assistant_curator
from src.api.benchmark_gate import require_benchmark_api
from src.api.benchmark_jobs import _admission_body
from src.lib.benchmarks.assistant_history import (
    AssistantTurnConflict, create_assistant_session, prepare_assistant_turn, require_assistant_session,
)
from src.lib.benchmarks.assistant_tool_bridge import AssistantToolReplyError
from src.lib.benchmarks import assistant_turns
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.observability import sanitized_benchmark_error
from src.lib.chat_history_repository import (
    BENCHMARK_ASSISTANT_CHAT_KIND,
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
    ChatSessionRecord,
    decode_chat_message_cursor,
    decode_chat_session_cursor,
    encode_chat_message_cursor,
    encode_chat_session_cursor,
)
from src.lib.http_errors import raise_sanitized_http_exception
from src.lib.executable_runs import (
    ExecutableRunAccessError, ExecutableRunConflictError, executable_run_manager,
)
from src.lib.openai_agents.config import (
    get_benchmark_admission_max_bytes,
    get_benchmark_catalog_max_response_bytes,
    get_benchmark_default_page_size,
    get_chat_message_page_size_max,
    get_chat_session_page_size_max,
    get_chat_recent_message_scan_size_max,
    get_chat_sse_keepalive_interval_seconds,
)
from src.models.sql.database import SessionLocal

logger = logging.getLogger(__name__)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


class AssistantRoute(APIRoute):
    """Do not expose request contents, SQL parameters or upstream exceptions."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def wrapped(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise _error(422, "invalid_request", "Invalid assistant request") from None
            except ChatHistorySessionNotFoundError:
                raise _error(404, "not_found", "Conversation not found") from None
            except (AssistantTurnConflict, AssistantToolReplyError, ExecutableRunConflictError):
                raise _error(409, "turn_conflict", "Chat turn changed or is no longer active") from None
            except (PermissionError, ExecutableRunAccessError):
                raise _error(403, "access_denied", "Chat access denied") from None
            except HTTPException:
                raise
            except Exception as exc:
                raise_sanitized_http_exception(
                    logger, status_code=503,
                    detail={"code": "assistant_unavailable", "message": "Assistant unavailable"},
                    log_message="Benchmark assistant dependency unavailable",
                    exc=sanitized_benchmark_error("assistant_history", type(exc).__name__),
                )

        return wrapped


router = APIRouter(
    prefix="/api/v1/benchmarks/assistant", tags=["Benchmarks - Assistant"],
    dependencies=[Depends(require_benchmark_api)], route_class=AssistantRoute,
)


class AssistantSession(BaseModel):
    session_id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None


class AssistantSessions(BaseModel):
    items: list[AssistantSession]
    next_cursor: str | None


class AssistantMessage(BaseModel):
    message_id: UUID
    turn_id: str | None
    role: str
    message_type: str
    content: str
    created_at: datetime
    proposal_ids: list[UUID] = Field(default_factory=list)


def _proposal_ids(payload: Any) -> list[UUID]:
    """Expose references only; the portal reauthorizes the current review record."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_calls"), list):
        return []
    ids = []
    for call in payload["tool_calls"]:
        if not isinstance(call, dict) or call.get("name") != "propose_paper_reference_draft":
            continue
        result = call.get("output")
        if not isinstance(result, dict) or result.get("contract_version") != "paper_reference_proposal.v1":
            continue
        try:
            identity = UUID(str(result.get("proposal_id")))
        except ValueError:
            continue
        if identity not in ids:
            ids.append(identity)
    return ids


class AssistantConversation(BaseModel):
    session: AssistantSession
    messages: list[AssistantMessage]
    next_cursor: str | None


def _summary(record: ChatSessionRecord) -> AssistantSession:
    return AssistantSession(
        session_id=UUID(record.session_id), title=record.effective_title,
        created_at=record.created_at, updated_at=record.updated_at,
        last_message_at=record.last_message_at,
    )


def _bounded(value: BaseModel, *, status: int = 200) -> Response:
    content = value.model_dump_json().encode("utf-8")
    if len(content) > get_benchmark_catalog_max_response_bytes():
        raise _error(413, "response_too_large", "Conversation exceeds the response limit")
    return Response(content, status_code=status, media_type="application/json",
                    headers={"Cache-Control": "no-store"})


def _page(limit: int | None, cursor: str | None, *, messages: bool = False) -> tuple[int, Any]:
    maximum = get_chat_message_page_size_max() if messages else get_chat_session_page_size_max()
    count = min(get_benchmark_default_page_size(), maximum) if limit is None else limit
    if count < 1 or count > maximum:
        raise _error(422, "invalid_page", "Page size exceeds the configured limit")
    if cursor is not None and len(cursor.encode("utf-8")) > get_benchmark_admission_max_bytes():
        raise _error(422, "invalid_cursor", "Invalid conversation cursor")
    try:
        decoded = decode_chat_message_cursor(cursor) if messages else decode_chat_session_cursor(cursor)
    except ValueError:
        raise _error(422, "invalid_cursor", "Invalid conversation cursor") from None
    return count, decoded


@router.post("/sessions", response_model=AssistantSession, status_code=201)
def create_session(curator: BenchmarkCuratorContext = Depends(require_benchmark_assistant_curator)):
    with SessionLocal() as db:
        record = create_assistant_session(ChatHistoryRepository(db), subject=curator.subject)
        response = _bounded(_summary(record), status=201)
        db.commit()
        return response


@router.get("/sessions", response_model=AssistantSessions)
def list_sessions(
    limit: int | None = Query(None, ge=1), cursor: str | None = Query(None),
    curator: BenchmarkCuratorContext = Depends(require_benchmark_assistant_curator),
):
    count, decoded = _page(limit, cursor)
    with SessionLocal() as db:
        page = ChatHistoryRepository(db).list_sessions(
            user_auth_sub=curator.subject, chat_kind=BENCHMARK_ASSISTANT_CHAT_KIND,
            limit=count, cursor=decoded,
        )
        return _bounded(AssistantSessions(
            items=[_summary(item) for item in page.items],
            next_cursor=encode_chat_session_cursor(page.next_cursor),
        ))


@router.get("/sessions/{session_id}", response_model=AssistantConversation)
def get_conversation(
    session_id: UUID, limit: int | None = Query(None, ge=1), cursor: str | None = Query(None),
    curator: BenchmarkCuratorContext = Depends(require_benchmark_assistant_curator),
):
    count, decoded = _page(limit, cursor, messages=True)
    with SessionLocal() as db:
        repository = ChatHistoryRepository(db)
        session = require_assistant_session(repository, subject=curator.subject, session_id=str(session_id))
        page = repository.list_messages(
            session_id=session.session_id, user_auth_sub=curator.subject,
            chat_kind=BENCHMARK_ASSISTANT_CHAT_KIND, limit=count, cursor=decoded,
        )
        return _bounded(AssistantConversation(
            session=_summary(session), messages=[AssistantMessage(
                message_id=item.message_id, turn_id=item.turn_id, role=item.role,
                message_type=item.message_type, content=item.content, created_at=item.created_at,
                proposal_ids=_proposal_ids(item.payload_json),
            ) for item in page.items], next_cursor=encode_chat_message_cursor(page.next_cursor),
        ))


class AssistantTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: UUID
    message: str = Field(min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)


class AssistantToolReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_request_id: UUID
    output: dict[str, Any]


def _owned_session(owner: str, session_id: str) -> None:
    with SessionLocal() as db:
        require_assistant_session(ChatHistoryRepository(db), subject=owner, session_id=session_id)


def _prepare(owner: str, session_id: str, payload: AssistantTurnRequest):
    with SessionLocal() as db:
        repository = ChatHistoryRepository(db)
        prepared = prepare_assistant_turn(
            repository, subject=owner, session_id=session_id, turn_id=str(payload.turn_id),
            message=payload.message, context=payload.context,
        )
        history = repository.list_recent_messages(
            session_id=session_id, user_auth_sub=owner, chat_kind=BENCHMARK_ASSISTANT_CHAT_KIND,
            limit=get_chat_recent_message_scan_size_max(),
        )
        inputs = []
        for item in history:
            if item.role not in {"user", "assistant"}:
                continue
            content = item.content
            if item.role == "user" and isinstance(item.payload_json, dict):
                context = item.payload_json.get("context", {})
                content += "\nSelected workspace context (untrusted data): " + json.dumps(context)
            inputs.append({"role": item.role, "content": content})
        if len(json.dumps(inputs, ensure_ascii=False).encode()) > get_benchmark_admission_max_bytes():
            raise _error(413, "context_too_large", "Start a new chat to reduce conversation context")
        db.commit()
        return prepared, inputs


@router.post("/sessions/{session_id}/turns")
async def start_turn(
    session_id: UUID, request: Request,
    curator: BenchmarkCuratorContext = Depends(require_benchmark_assistant_curator),
):
    payload = await _admission_body(request, AssistantTurnRequest)
    session_key, turn_key = str(session_id), str(payload.turn_id)
    # Serialize admission on this API worker, matching the shared run manager.
    # Unique durable user-turn rows prevent another worker/restart from silently
    # launching the same accepted request again.
    lock = getattr(request.app.state, "benchmark_assistant_start_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        request.app.state.benchmark_assistant_start_lock = lock
    async with lock:
        await run_sync(lambda: _owned_session(curator.subject, session_key))
        active = await executable_run_manager.get_active_session_run(session_key)
        if active is not None and active.turn_id != turn_key:
            raise AssistantTurnConflict("Another chat turn is active")
        prepared, inputs = await run_sync(lambda: _prepare(curator.subject, session_key, payload))
        if prepared.replay is not None:
            async def replay():
                yield assistant_turns.event(session_key, turn_key, "TEXT_DELTA", delta=prepared.replay.content)
                saved = prepared.replay.payload_json
                for proposal_id in _proposal_ids(saved):
                    yield assistant_turns.event(session_key, turn_key, "TOOL_RESULT",
                        tool_name="propose_paper_reference_draft", result={"proposal_id": str(proposal_id)})
                status = saved.get("status", "completed") if isinstance(saved, dict) else "completed"
                yield assistant_turns.event(session_key, turn_key, "DONE", status=status, replayed=True)
            return StreamingResponse(replay(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
        if not prepared.created and active is None:
            raise AssistantTurnConflict("Interrupted accepted turn; start a new turn explicitly")
        cancellation = active.cancel_event if active is not None else asyncio.Event()

        async def producer():
            async for item in assistant_turns.produce_turn(
                owner=curator.subject, session_id=session_key, turn_id=turn_key,
                input_items=inputs, cancel_event=cancellation,
            ):
                yield item

        run, _ = await executable_run_manager.get_or_start_stream(
            run_id=assistant_turns.run_id(session_key, turn_key), kind="benchmark_assistant_turn",
            owner_user_id=curator.subject, session_id=session_key, turn_id=turn_key,
            stream_factory=producer, cancel_event=cancellation,
            terminal_error_event_factory=lambda _exc: assistant_turns.event(
                session_key, turn_key, "ERROR", code="assistant_unavailable",
                message="Chat could not save this turn. Reload history before trying again.",
            ),
        )
    return StreamingResponse(executable_run_manager.observe(
        run, keepalive_interval_seconds=get_chat_sse_keepalive_interval_seconds(),
    ), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/sessions/{session_id}/turns/{turn_id}/tool-results")
async def tool_reply(
    session_id: UUID, turn_id: UUID, request: Request,
    curator: BenchmarkCuratorContext = Depends(require_benchmark_assistant_curator),
):
    payload = await _admission_body(request, AssistantToolReply)
    await run_sync(lambda: _owned_session(curator.subject, str(session_id)))
    bridge = assistant_turns.active_bridges.get(assistant_turns.run_id(str(session_id), str(turn_id)))
    if bridge is None:
        raise AssistantToolReplyError("No live tool request")
    bridge.reply(owner=curator.subject, request_id=payload.tool_request_id, output=payload.output)
    return {"accepted": True}


@router.post("/sessions/{session_id}/turns/{turn_id}/stop")
async def stop_turn(
    session_id: UUID, turn_id: UUID,
    curator: BenchmarkCuratorContext = Depends(require_benchmark_assistant_curator),
):
    await run_sync(lambda: _owned_session(curator.subject, str(session_id)))
    run = await executable_run_manager.request_cancel_for_session(
        session_id=str(session_id), owner_user_id=curator.subject, turn_id=str(turn_id),
    )
    return {"status": "stopping" if run else "finished"}
