"""Bounded main-chat lookup tools for the supervisor agent."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.lib.agent_studio.tools import (
    get_extraction_diagnostic_report,
    get_tool_calls_summary,
    get_trace_conversation,
    get_trace_costs,
    get_trace_duplicates,
    get_trace_model_live_context,
    get_trace_payloads,
    get_trace_summary,
    set_trusted_trace_caller,
)
from src.lib.chat_history_repository import (
    ASSISTANT_CHAT_KIND,
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
    ChatMessageRecord,
)
from src.lib.chat_transcript import extract_flow_assistant_message
from src.lib.context import (
    get_current_session_id,
    get_current_trace_id,
    get_current_user_id,
)
from src.lib.openai_agents.bounded_list import (
    normalize_page_limit,
    parse_offset_cursor,
    recent_page,
)
from src.lib.openai_agents.config import (
    get_agent_studio_trace_review_aggregate_page_size,
    get_agent_studio_trace_review_page_size,
    get_supervisor_field_text_limit,
    get_supervisor_inspect_chat_traces_default_limit,
    get_supervisor_max_list_limit,
    get_supervisor_recall_chat_history_default_limit,
    get_supervisor_text_preview_limit,
)
from src.lib.openai_agents.chat_compaction_session import (
    CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE,
)
from src.lib.openai_agents.tool_result_bounds import (
    ToolResultBudgetError,
    budget_failure,
    content_sha256,
    fit_page,
    fit_text_window,
    report_budget_failure_result,
    tool_result_budget,
)
from src.models.sql.database import SessionLocal


# Env-configurable (defaults unchanged); see config.py getters and .env.example:
#   SUPERVISOR_TEXT_PREVIEW_LIMIT, SUPERVISOR_FIELD_TEXT_LIMIT, SUPERVISOR_MAX_LIST_LIMIT,
#   SUPERVISOR_RECALL_CHAT_HISTORY_DEFAULT_LIMIT, SUPERVISOR_INSPECT_CHAT_TRACES_DEFAULT_LIMIT.
_TEXT_PREVIEW_LIMIT = get_supervisor_text_preview_limit()
_FIELD_TEXT_LIMIT = get_supervisor_field_text_limit()
_MAX_LIST_LIMIT = get_supervisor_max_list_limit()
_MAX_TRACE_ARRAY_ITEMS = 20
_MAX_TRACE_TEXT = 1200
_MAX_TRACE_INVENTORY_MESSAGES = 5000


def _tool_response(status: str, message: str, **extra: Any) -> str:
    payload = {"status": status, "message": message}
    payload.update(extra)
    return json.dumps(_bounded_json(payload), ensure_ascii=True, default=str)


def _recall_response(status: str, message: str, **extra: Any) -> str:
    payload = {"status": status, "message": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=True, default=str)


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _preview_text(value: Any, *, limit: int = _TEXT_PREVIEW_LIMIT) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _bounded_json(
    value: Any,
    *,
    text_limit: int = _MAX_TRACE_TEXT,
    list_limit: int = _MAX_TRACE_ARRAY_ITEMS,
    depth: int = 0,
) -> Any:
    if isinstance(value, str):
        return _preview_text(value, limit=text_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if depth >= 8:
        return "<truncated>"
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump(mode="json")
        except TypeError:
            value = value.model_dump()
    if isinstance(value, Mapping):
        return {
            str(key): _bounded_json(
                item,
                text_limit=text_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = [
            _bounded_json(
                item,
                text_limit=text_limit,
                list_limit=list_limit,
                depth=depth + 1,
            )
            for item in list(value)[:list_limit]
        ]
        if len(value) > list_limit:
            items.append({"truncated_count": len(value) - list_limit})
        return items
    return _preview_text(value, limit=text_limit)


def _list_session_messages(
    *,
    session_id: str,
    user_id: str,
    max_messages: int = _MAX_TRACE_INVENTORY_MESSAGES,
) -> list[ChatMessageRecord]:
    db = SessionLocal()
    try:
        repository = ChatHistoryRepository(db)
        return repository.list_recent_messages(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            limit=max_messages,
        )
    except ChatHistorySessionNotFoundError:
        return []
    finally:
        db.close()


def _recall_message_payload(message: ChatMessageRecord, *, ordinal: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ordinal": ordinal,
        "message_id": str(message.message_id),
        "turn_id": message.turn_id,
        "role": message.role,
        "message_type": message.message_type,
        "created_at": message.created_at.isoformat(),
        "content": message.content,
    }
    if message.role == "flow":
        flow_assistant_message = extract_flow_assistant_message(message)
        if flow_assistant_message is not None:
            payload["flow_assistant_message"] = flow_assistant_message
    return payload


def _recall_visible_messages(*, session_id: str, user_id: str) -> list[ChatMessageRecord]:
    return [
        message
        for message in _list_session_messages(session_id=session_id, user_id=user_id)
        if message.message_type != CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE
    ]


def _resolve_recall_turn_messages(
    messages: Sequence[ChatMessageRecord],
    *,
    turn_ref: str | None,
) -> list[ChatMessageRecord]:
    if not messages:
        return []
    ref = str(turn_ref or "latest").strip()
    if not ref or ref.lower() == "latest":
        for message in reversed(messages):
            if message.turn_id:
                return [item for item in messages if item.turn_id == message.turn_id]
        return [messages[-1]]

    turn_ids: list[str] = []
    for message in messages:
        if message.turn_id and message.turn_id not in turn_ids:
            turn_ids.append(message.turn_id)
    if ref.isdigit():
        index = int(ref) - 1
        if 0 <= index < len(turn_ids):
            turn_id = turn_ids[index]
            return [item for item in messages if item.turn_id == turn_id]

    for message in messages:
        if ref in {
            str(message.turn_id or ""),
            str(message.message_id),
        }:
            return [item for item in messages if item.turn_id == message.turn_id] if message.turn_id else [message]
    return []


def _exclude_compaction_messages(
    messages: Sequence[ChatMessageRecord],
) -> list[ChatMessageRecord]:
    return [
        message
        for message in messages
        if message.message_type != CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE
    ]


def _uuid_ref(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _direct_recall_turn_messages(
    *,
    session_id: str,
    user_id: str,
    turn_ref: str,
) -> list[ChatMessageRecord]:
    db = SessionLocal()
    try:
        repository = ChatHistoryRepository(db)
        message_id = _uuid_ref(turn_ref)
        if message_id is not None:
            message = repository.get_message_by_id(
                session_id=session_id,
                user_auth_sub=user_id,
                chat_kind=ASSISTANT_CHAT_KIND,
                message_id=message_id,
            )
            if message is not None and message.message_type != CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE:
                if message.turn_id:
                    return _exclude_compaction_messages(
                        repository.list_messages_for_turn(
                            session_id=session_id,
                            user_auth_sub=user_id,
                            chat_kind=ASSISTANT_CHAT_KIND,
                            turn_id=message.turn_id,
                        )
                    )
                return [message]

        return _exclude_compaction_messages(
            repository.list_messages_for_turn(
                session_id=session_id,
                user_auth_sub=user_id,
                chat_kind=ASSISTANT_CHAT_KIND,
                turn_id=turn_ref,
            )
        )
    except ChatHistorySessionNotFoundError:
        return []
    finally:
        db.close()


_RECALL_TOOL_NAME = "recall_chat_history"
_RECALL_MESSAGE_FIELDS = ("content", "flow_assistant_message")


def _recall_budget_failure(exc: ToolResultBudgetError, *, detail: str) -> str:
    failure = budget_failure(
        tool_name=_RECALL_TOOL_NAME,
        measured=exc.measured,
        limit=exc.limit,
        field=detail,
    )
    report_budget_failure_result(
        failure,
        tool_name=_RECALL_TOOL_NAME,
        component="supervisor_recall_chat_history",
    )
    return json.dumps(failure, ensure_ascii=True, default=str)


def _recall_cursor(cursor: str | int | None, *, total: int) -> int:
    """Parse a recall page cursor strictly.

    Malformed cursors and cursors past the current end fail explicitly instead
    of restarting at zero. Only the past-the-end case is detected as stale: a
    transcript that gained messages between pages is not detected, so a recent
    page requested after new messages arrive can repeat a boundary message.
    """

    if cursor is None or cursor == "":
        return 0
    text = str(cursor).strip()
    if isinstance(cursor, bool) or not text.isdigit():
        raise ValueError("cursor must be a non-negative integer from a previous next_cursor")
    value = int(text)
    if value > total:
        raise ValueError(
            f"cursor {_preview_text(text)} is past the end of the {total} available "
            "messages; the transcript may have changed. Restart without a cursor."
        )
    return value


def _echo(value: Any) -> str:
    """Short preview of model-supplied input echoed back in a response."""

    return _preview_text(value, limit=_TEXT_PREVIEW_LIMIT)


def _withheld_message(payload: dict[str, Any]) -> dict[str, Any]:
    """Identity and exact detail pointers for a message too large for one page."""

    withheld = {
        key: payload[key]
        for key in ("ordinal", "message_id", "turn_id", "role", "message_type", "created_at")
    }
    withheld["withheld"] = True
    present = []
    for field in _RECALL_MESSAGE_FIELDS:
        value = payload.get(field)
        if isinstance(value, str):
            withheld[f"{field}_chars"] = len(value)
            withheld[f"{field}_sha256"] = content_sha256(value)
            present.append((len(value), field))
    # One exact read per text field, longest first: a flow row keeps its long
    # text in flow_assistant_message, not in content.
    withheld["detail_calls"] = [
        {
            "detail": "message",
            "message_id": payload["message_id"],
            "message_field": field,
            "content_cursor": 0,
        }
        for _length, field in sorted(present, reverse=True)
    ]
    return withheld


def _withheld_note(page: list[dict[str, Any]]) -> str:
    count = sum(1 for item in page if item.get("withheld"))
    if not count:
        return ""
    return (
        f" {count} message(s) on this page were too long to include and are withheld;"
        " read each exactly with its detail_calls."
    )


def _fit_recall_messages(
    payloads: list[dict[str, Any]],
    *,
    start: int,
    limit: int,
    render: Any,
) -> dict[str, Any]:
    response, _ = fit_page(
        payloads,
        start=start,
        limit=limit,
        render=render,
        budget=tool_result_budget(),
        oversized=lambda payload, _index: _withheld_message(payload),
    )
    return response


def _page_note(ended_by: str) -> str:
    if ended_by == "size_budget":
        return (
            " The page ended at the tool result size budget; continue with next_cursor."
        )
    return ""


async def _recall_message_detail(
    *,
    session_id: str,
    user_id: str,
    message_id: str | None,
    message_field: str,
    content_cursor: int | None,
) -> str:
    """Exact bounded chunks of one message in the active chat session."""

    parsed_id = _uuid_ref(str(message_id or "").strip())
    if parsed_id is None:
        return _recall_response(
            "invalid_request",
            "detail=\"message\" requires the message_id of a message in this chat.",
            detail="message",
        )
    if message_field not in _RECALL_MESSAGE_FIELDS:
        return _recall_response(
            "invalid_request",
            f"message_field must be one of: {', '.join(_RECALL_MESSAGE_FIELDS)}.",
            detail="message",
        )
    db = SessionLocal()
    try:
        # Scoped to the curator's own active session: other sessions' ids miss.
        message = ChatHistoryRepository(db).get_message_by_id(
            session_id=session_id,
            user_auth_sub=user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            message_id=parsed_id,
            excluded_message_types={CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE},
        )
    except ChatHistorySessionNotFoundError:
        message = None
    finally:
        db.close()
    if message is None:
        return _recall_response(
            "not_found",
            "No message with that id exists in this chat.",
            detail="message",
            message_id=str(parsed_id),
        )
    text = (
        message.content
        if message_field == "content"
        else extract_flow_assistant_message(message)
    )
    if not isinstance(text, str):
        return _recall_response(
            "not_found",
            f"This message has no {message_field}.",
            detail="message",
            message_id=str(parsed_id),
        )
    start = 0 if content_cursor is None else content_cursor
    digest = content_sha256(text)

    def render(chunk: str, end: int) -> dict[str, Any]:
        complete = end >= len(text)
        return {
            "status": "ok",
            "message": "Returned an exact chunk of one transcript message.",
            "detail": "message",
            "session_id": session_id,
            "message_id": str(message.message_id),
            "turn_id": message.turn_id,
            "role": message.role,
            "message_type": message.message_type,
            "created_at": message.created_at.isoformat(),
            "message_field": message_field,
            "content": chunk,
            "content_range": {"start": start, "end": end, "total_chars": len(text)},
            "content_sha256": digest,
            "next_content_cursor": None if complete else end,
            "complete": complete,
        }

    try:
        return json.dumps(
            fit_text_window(text, cursor=start, render=render, budget=tool_result_budget()),
            ensure_ascii=True,
            default=str,
        )
    except ValueError as exc:
        return _recall_response(
            "invalid_cursor",
            str(exc),
            detail="message",
            message_id=str(parsed_id),
        )
    except ToolResultBudgetError as exc:
        return _recall_budget_failure(exc, detail="message")


async def recall_chat_history(
    *,
    detail: str = "recent",
    turn_ref: str | None = None,
    query: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    message_id: str | None = None,
    message_field: str = "content",
    content_cursor: int | None = None,
) -> str:
    """Recall exact transcript text for the active standard chat session.

    Every response fits TOOL_RESULT_MAX_BYTES. Pages end at the message limit
    or the size budget and carry ``next_cursor``; a message too large for any
    page is withheld with ``detail_calls`` that read it exactly in chunks
    (``detail="message"``). All reads are scoped to the curator's own session.
    """

    session_id = get_current_session_id()
    user_id = get_current_user_id()
    if not session_id or not user_id:
        return _recall_response(
            "unavailable",
            "Transcript recall is only available inside an active chat session.",
        )

    normalized_detail = str(detail or "recent").strip() or "recent"
    if normalized_detail == "message":
        return await _recall_message_detail(
            session_id=session_id,
            user_id=user_id,
            message_id=message_id,
            message_field=str(message_field or "content"),
            content_cursor=content_cursor,
        )
    bounded_limit = normalize_page_limit(
        limit,
        default=get_supervisor_recall_chat_history_default_limit(),
        maximum=_MAX_LIST_LIMIT,
    )
    if normalized_detail == "recent":
        messages = _recall_visible_messages(session_id=session_id, user_id=user_id)
        payloads = [
            _recall_message_payload(message, ordinal=index + 1)
            for index, message in enumerate(messages)
        ]
        total_count = len(messages)
        try:
            served = _recall_cursor(cursor, total=total_count)
        except ValueError as exc:
            return _recall_response("invalid_cursor", str(exc), detail="recent")
        end = total_count - served
        # Newest first while fitting, so a size-ended page keeps the latest turns.
        newest_first = list(reversed(payloads[max(0, end - bounded_limit):end]))

        def render_recent(page: list[dict[str, Any]], returned: int) -> dict[str, Any]:
            remaining = end - returned
            truncated = remaining > 0
            ended_by = "end" if not truncated else (
                "limit" if returned >= bounded_limit else "size_budget"
            )
            return {
                "status": "ok",
                "message": (
                    f"Returned {returned} exact transcript message(s) from this chat."
                    + _page_note(ended_by)
                    + _withheld_note(page)
                ),
                "detail": "recent",
                "session_id": session_id,
                "messages": list(reversed(page)),
                "total_count": total_count,
                "truncated": truncated,
                "next_cursor": str(served + returned) if truncated else None,
                "page_ended_by": ended_by,
            }

        try:
            response = _fit_recall_messages(
                newest_first,
                start=0,
                limit=len(newest_first),
                render=render_recent,
            )
        except ToolResultBudgetError as exc:
            return _recall_budget_failure(exc, detail="recent")
        return json.dumps(response, ensure_ascii=True, default=str)

    if normalized_detail == "turn":
        normalized_turn_ref = str(turn_ref or "latest").strip() or "latest"
        if normalized_turn_ref.lower() == "latest" or normalized_turn_ref.isdigit():
            messages = _recall_visible_messages(session_id=session_id, user_id=user_id)
            selected = _resolve_recall_turn_messages(
                messages,
                turn_ref=normalized_turn_ref,
            )
        else:
            selected = _direct_recall_turn_messages(
                session_id=session_id,
                user_id=user_id,
                turn_ref=normalized_turn_ref,
            )
        if not selected:
            return _recall_response(
                "not_found",
                "No transcript turn matched that reference in this chat.",
                detail="turn",
                session_id=session_id,
                turn_ref=_echo(normalized_turn_ref),
                messages=[],
            )
        payloads = [
            _recall_message_payload(message, ordinal=index + 1)
            for index, message in enumerate(selected)
        ]
        return _forward_recall_page(
            payloads,
            cursor=cursor,
            detail="turn",
            message="Returned exact transcript rows for the requested turn.",
            extra={"session_id": session_id, "turn_ref": _echo(normalized_turn_ref)},
        )

    if normalized_detail == "search":
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return _recall_response(
                "invalid_query",
                "Search detail requires a non-empty query.",
                detail="search",
            )
        if len(normalized_query) > _FIELD_TEXT_LIMIT:
            # A caller input error, not a result-budget escape.
            return _recall_response(
                "invalid_request",
                f"Search query is longer than {_FIELD_TEXT_LIMIT} characters; "
                "search for a shorter distinctive phrase.",
                detail="search",
            )
        db = SessionLocal()
        try:
            repository = ChatHistoryRepository(db)
            results = repository.search_session_messages_ranked(
                session_id=session_id,
                user_auth_sub=user_id,
                chat_kind=ASSISTANT_CHAT_KIND,
                query=normalized_query,
                limit=bounded_limit,
                excluded_message_types={CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE},
            )
        except ChatHistorySessionNotFoundError:
            results = []
        finally:
            db.close()
        payloads = [
            _recall_message_payload(message, ordinal=index + 1)
            for index, message in enumerate(results)
        ]
        return _forward_recall_page(
            payloads,
            cursor=cursor,
            detail="search",
            message=f"Found {len(results)} exact transcript message(s) in this chat.",
            extra={"session_id": session_id, "query": _echo(normalized_query)},
        )

    return _recall_response(
        "invalid_detail",
        "Unsupported recall detail. Use recent, turn, search, or message.",
        detail=_echo(normalized_detail),
    )


def _forward_recall_page(
    payloads: list[dict[str, Any]],
    *,
    cursor: str | int | None,
    detail: str,
    message: str,
    extra: Mapping[str, Any],
) -> str:
    """One size-fitted forward page of turn or search results."""

    total = len(payloads)
    try:
        start = _recall_cursor(cursor, total=total)
    except ValueError as exc:
        return _recall_response("invalid_cursor", str(exc), detail=detail)

    def render(page: list[dict[str, Any]], returned: int) -> dict[str, Any]:
        next_offset = start + returned
        truncated = next_offset < total
        ended_by = "size_budget" if truncated else "end"
        response = {
            "status": "ok",
            "message": message + _page_note(ended_by) + _withheld_note(page),
            "detail": detail,
            **extra,
            "messages": page,
        }
        if truncated or start:
            response.update(
                total_count=total,
                truncated=truncated,
                next_cursor=str(next_offset) if truncated else None,
                page_ended_by=ended_by,
            )
        return response

    try:
        response = _fit_recall_messages(payloads, start=start, limit=total - start, render=render)
    except ToolResultBudgetError as exc:
        return _recall_budget_failure(exc, detail=detail)
    return json.dumps(response, ensure_ascii=True, default=str)


def _trace_inventory_records(
    *,
    session_id: str,
    user_id: str,
    query: str | None = None,
) -> list[dict[str, Any]]:
    messages = _list_session_messages(session_id=session_id, user_id=user_id)
    query_text = str(query or "").strip().lower()
    traces: list[dict[str, Any]] = []
    seen_trace_ids: set[str] = set()
    pending_user: ChatMessageRecord | None = None

    def add_trace(message: ChatMessageRecord, *, source: str, answer_preview: str | None = None) -> None:
        trace_id = _optional_text(message.trace_id)
        if not trace_id or trace_id in seen_trace_ids:
            return
        user_preview = _preview_text(pending_user.content if pending_user else "")
        assistant_preview = _preview_text(answer_preview if answer_preview is not None else message.content)
        searchable = f"{trace_id} {user_preview} {assistant_preview}".lower()
        if query_text and query_text not in searchable:
            return
        seen_trace_ids.add(trace_id)
        traces.append(
            {
                "ordinal": len(traces) + 1,
                "trace_id": trace_id,
                "turn_id": message.turn_id,
                "message_id": str(message.message_id),
                "created_at": message.created_at.isoformat(),
                "role": message.role,
                "message_type": message.message_type,
                "source": source,
                "user_question_preview": user_preview,
                "assistant_answer_preview": assistant_preview,
            }
        )

    for message in messages:
        if message.role == "user":
            pending_user = message
            if message.trace_id:
                add_trace(message, source="execute_flow_user_runtime", answer_preview="")
            continue
        if message.role == "assistant" and message.trace_id:
            add_trace(message, source="assistant_message")
            continue
        if message.role == "flow" and message.trace_id:
            add_trace(
                message,
                source="execute_flow_transcript",
                answer_preview=extract_flow_assistant_message(message) or message.content,
            )

    current_trace_id = get_current_trace_id()
    if current_trace_id and current_trace_id not in seen_trace_ids:
        traces.append(
            {
                "ordinal": len(traces) + 1,
                "trace_id": current_trace_id,
                "turn_id": None,
                "message_id": None,
                "created_at": None,
                "role": "assistant",
                "message_type": "current_turn",
                "source": "current_turn_context",
                "user_question_preview": "",
                "assistant_answer_preview": "Current in-flight supervisor run.",
            }
        )

    return traces


def _trace_inventory(
    *,
    session_id: str,
    user_id: str,
    limit: int,
    query: str | None = None,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], bool, str | None, int]:
    traces = _trace_inventory_records(
        session_id=session_id,
        user_id=user_id,
        query=query,
    )
    page, truncated, next_cursor = recent_page(traces, limit=limit, cursor=cursor)
    return page, truncated, next_cursor, len(traces)


def _authorized_trace(
    trace_id: str | None,
    *,
    session_id: str,
    user_id: str,
    turn_ref: str | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    inventory = _trace_inventory_records(
        session_id=session_id,
        user_id=user_id,
    )
    normalized = _optional_text(trace_id) or _resolve_trace_id_from_ref(
        inventory,
        turn_ref=turn_ref,
    )
    if not normalized:
        return None, inventory
    for item in inventory:
        if item.get("trace_id") == normalized:
            return normalized, inventory
    return None, inventory


def _resolve_trace_id_from_ref(
    inventory: Sequence[Mapping[str, Any]],
    *,
    turn_ref: str | None,
) -> str | None:
    ref = _optional_text(turn_ref)
    if not ref or not inventory:
        return None
    item = _resolve_trace_item_from_ref(inventory, turn_ref=ref)
    return _optional_text(item.get("trace_id")) if item else None


def _resolve_trace_item_from_ref(
    inventory: Sequence[Mapping[str, Any]],
    *,
    turn_ref: str | None,
) -> Mapping[str, Any] | None:
    ref = _optional_text(turn_ref)
    if not ref or not inventory:
        return None
    if ref.lower() in {"previous", "latest", "last"}:
        completed = [
            item
            for item in inventory
            if item.get("source") != "current_turn_context"
        ]
        return completed[-1] if completed else inventory[-1]
    if ref.lower() in {"current", "current_turn"}:
        return inventory[-1]
    for item in inventory:
        if ref in {
            str(item.get("trace_id") or ""),
            str(item.get("turn_id") or ""),
            str(item.get("message_id") or ""),
            str(item.get("ordinal") or ""),
        }:
            return item
    return None


async def inspect_chat_traces(
    *,
    detail: str = "inventory",
    trace_id: str | None = None,
    turn_ref: str | None = None,
    query: str | None = None,
    tool_name: str | None = None,
    event_type: str | None = None,
    candidate_id: str | None = None,
    field: str | None = None,
    start: int = 0,
    max_chars: int | None = None,
    include_sibling_traces: bool = False,
    limit: int | None = None,
    cursor: str | None = None,
    section: str | None = None,
    item_start: int = 0,
) -> str:
    """Inspect current main-chat trace metadata through a bounded allowlist."""

    session_id = get_current_session_id()
    user_id = get_current_user_id()
    if not session_id or not user_id:
        return _tool_response(
            "unavailable",
            "Trace inspection is only available inside an active chat session.",
        )

    set_trusted_trace_caller(caller_sub=str(user_id), caller_email=None)

    normalized_detail = str(detail or "inventory").strip() or "inventory"
    bounded_limit = normalize_page_limit(
        limit,
        default=get_supervisor_inspect_chat_traces_default_limit(),
        maximum=_MAX_LIST_LIMIT,
    )
    if normalized_detail == "inventory":
        if turn_ref and not query:
            inventory = _trace_inventory_records(
                session_id=session_id,
                user_id=user_id,
            )
            selected = _resolve_trace_item_from_ref(inventory, turn_ref=turn_ref)
            traces = [dict(selected)] if selected else []
            truncated = False
            next_cursor = None
            total_count = len(inventory)
        else:
            traces, truncated, next_cursor, total_count = _trace_inventory(
                session_id=session_id,
                user_id=user_id,
                limit=bounded_limit,
                query=query,
                cursor=cursor,
            )
        return _tool_response(
            "ok",
            f"{len(traces)} authorized trace candidate(s) are available for this chat.",
            detail="inventory",
            session_id=session_id,
            traces=traces,
            total_count=total_count,
            truncated=truncated,
            next_cursor=next_cursor,
        )

    authorized_trace_id, inventory = _authorized_trace(
        trace_id,
        session_id=session_id,
        user_id=user_id,
        turn_ref=turn_ref,
    )
    if authorized_trace_id is None:
        return _tool_response(
            "unauthorized_trace",
            "Trace IDs must come from this chat session's authorized trace inventory before TraceReview can be queried.",
            detail=normalized_detail,
            requested_trace_id=trace_id,
            authorized_trace_ids=[item.get("trace_id") for item in inventory[-10:]],
        )

    if normalized_detail == "summary":
        result = await get_trace_summary(authorized_trace_id)
    elif normalized_detail == "conversation":
        if field not in {"user_query", "assistant_response"}:
            return _tool_response(
                "invalid_field",
                "Conversation detail requires field=user_query or field=assistant_response; fetch each field independently and follow its next_call cursor until complete=true.",
                detail=normalized_detail,
                field=field,
            )
        result = await get_trace_conversation(
            authorized_trace_id,
            field=field,
            start=start,
            max_chars=max_chars,
        )
    elif normalized_detail == "diagnostic_report":
        result = await get_extraction_diagnostic_report(
            authorized_trace_id,
            session_id=session_id,
            include_sibling_traces=bool(include_sibling_traces),
            include_raw_args=False,
            include_raw_outputs=False,
            tool_name=tool_name,
            event_type=event_type,
            candidate_id=candidate_id,
            section=section,
            offset=parse_offset_cursor(cursor),
            limit=normalize_page_limit(
                limit,
                default=get_agent_studio_trace_review_aggregate_page_size(),
                maximum=get_agent_studio_trace_review_aggregate_page_size(),
            ),
            item_start=item_start,
        )
    elif normalized_detail == "tool_calls":
        configured_page_size = get_agent_studio_trace_review_page_size()
        page_size = normalize_page_limit(
            limit,
            default=configured_page_size,
            maximum=configured_page_size,
        )
        offset = parse_offset_cursor(cursor)
        aligned_offset = offset - (offset % page_size)
        result = await get_tool_calls_summary(
            authorized_trace_id,
            page=(aligned_offset // page_size) + 1,
            page_size=page_size,
        )
    elif normalized_detail == "costs":
        result = await get_trace_costs(
            authorized_trace_id,
            section=section,
            offset=parse_offset_cursor(cursor),
            limit=normalize_page_limit(
                limit,
                default=get_agent_studio_trace_review_aggregate_page_size(),
                maximum=get_agent_studio_trace_review_aggregate_page_size(),
            ),
            item_start=item_start,
        )
    elif normalized_detail == "duplicates":
        result = await get_trace_duplicates(
            authorized_trace_id,
            section=section,
            offset=parse_offset_cursor(cursor),
            limit=normalize_page_limit(
                limit,
                default=get_agent_studio_trace_review_aggregate_page_size(),
                maximum=get_agent_studio_trace_review_aggregate_page_size(),
            ),
            item_start=item_start,
        )
    elif normalized_detail == "model_live_context":
        result = await get_trace_model_live_context(
            authorized_trace_id,
            section=section,
            offset=parse_offset_cursor(cursor),
            limit=normalize_page_limit(
                limit,
                default=get_agent_studio_trace_review_aggregate_page_size(),
                maximum=get_agent_studio_trace_review_aggregate_page_size(),
            ),
            item_start=item_start,
        )
    elif normalized_detail == "payload_inventory":
        offset = parse_offset_cursor(cursor)
        configured_page_size = get_agent_studio_trace_review_aggregate_page_size()
        result = await get_trace_payloads(
            authorized_trace_id,
            section="payloads",
            limit=normalize_page_limit(
                limit,
                default=configured_page_size,
                maximum=configured_page_size,
            ),
            offset=offset,
        )
    else:
        return _tool_response(
            "invalid_detail",
            "Unsupported trace detail. Use inventory, conversation, summary, diagnostic_report, tool_calls, costs, duplicates, model_live_context, or payload_inventory.",
            detail=normalized_detail,
        )

    return _tool_response(
        "ok" if result.get("status") == "success" else "trace_review_error",
        "TraceReview detail returned through the main-chat allowlist.",
        detail=normalized_detail,
        trace_id=authorized_trace_id,
        trace_review_status=result.get("status"),
        data=result.get("data") if "data" in result else result,
        token_info=result.get("token_info"),
        error=result.get("error"),
        truncated=True,
    )


__all__ = ["inspect_chat_traces", "recall_chat_history"]
