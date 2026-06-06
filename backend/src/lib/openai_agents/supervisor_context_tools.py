"""Bounded main-chat lookup tools for the supervisor agent."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from src.lib.agent_studio.tools import (
    get_extraction_diagnostic_report,
    get_tool_calls_summary,
    get_trace_conversation,
    get_trace_costs,
    get_trace_duplicates,
    get_trace_payloads,
    get_trace_summary,
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
from src.lib.curation_workspace.extraction_results import list_extraction_results
from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationExtractionResultRecord,
    CurationReviewSession,
)
from src.lib.curation_workspace.session_queries import (
    get_session_detail,
    get_session_workspace,
)
from src.lib.file_outputs.storage import FileOutputStorageService, PathSecurityError
from src.lib.openai_agents.curation_context_registry import (
    list_current_turn_curation_context,
)
from src.models.sql.database import SessionLocal
from src.models.sql.file_output import FileOutput
from src.schemas.curation_workspace import CurationExtractionSourceKind


_TEXT_PREVIEW_LIMIT = 220
_FIELD_TEXT_LIMIT = 500
_MAX_LIST_LIMIT = 20
_MAX_TRACE_ARRAY_ITEMS = 20
_MAX_TRACE_TEXT = 1200
_MAX_TRACE_INVENTORY_MESSAGES = 5000
_MAX_EVIDENCE_SCAN_RECORDS = 200
_MAX_FILE_PREVIEW_BYTES = 64 * 1024
_MAX_FILE_SCHEMA_ROWS = 20


def _tool_response(status: str, message: str, **extra: Any) -> str:
    payload = {"status": status, "message": message}
    payload.update(extra)
    return json.dumps(_bounded_json(payload), ensure_ascii=True, default=str)


def _normalize_limit(limit: int | None, *, default: int = 5, maximum: int = _MAX_LIST_LIMIT) -> int:
    try:
        value = int(limit if limit is not None else default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _parse_offset_cursor(cursor: str | None) -> int:
    try:
        return max(0, int(cursor or 0))
    except (TypeError, ValueError):
        return 0


def _offset_page(
    items: Sequence[Any],
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[Any], bool, str | None]:
    offset = _parse_offset_cursor(cursor)
    page = list(items[offset : offset + limit])
    next_offset = offset + len(page)
    has_more = next_offset < len(items)
    return page, has_more, str(next_offset) if has_more else None


def _recent_page(
    items: Sequence[Any],
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[Any], bool, str | None]:
    offset = _parse_offset_cursor(cursor)
    end = max(0, len(items) - offset)
    start = max(0, end - limit)
    page = list(items[start:end])
    has_more = start > 0
    return page, has_more, str(offset + len(page)) if has_more else None


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
    page, truncated, next_cursor = _recent_page(traces, limit=limit, cursor=cursor)
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
    include_sibling_traces: bool = False,
    limit: int | None = None,
    cursor: str | None = None,
) -> str:
    """Inspect current main-chat trace metadata through a bounded allowlist."""

    session_id = get_current_session_id()
    user_id = get_current_user_id()
    if not session_id or not user_id:
        return _tool_response(
            "unavailable",
            "Trace inspection is only available inside an active chat session.",
        )

    normalized_detail = str(detail or "inventory").strip() or "inventory"
    bounded_limit = _normalize_limit(limit, default=5)
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
        result = await get_trace_conversation(authorized_trace_id)
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
        )
    elif normalized_detail == "tool_calls":
        result = await get_tool_calls_summary(authorized_trace_id)
    elif normalized_detail == "costs":
        result = await get_trace_costs(authorized_trace_id)
    elif normalized_detail == "duplicates":
        result = await get_trace_duplicates(authorized_trace_id)
    elif normalized_detail == "payload_inventory":
        try:
            offset = max(0, int(cursor or 0))
        except (TypeError, ValueError):
            offset = 0
        result = await get_trace_payloads(
            authorized_trace_id,
            limit=_normalize_limit(limit, default=10, maximum=20),
            offset=offset,
            include_values=False,
        )
    else:
        return _tool_response(
            "invalid_detail",
            "Unsupported trace detail. Use inventory, conversation, summary, diagnostic_report, tool_calls, costs, duplicates, or payload_inventory.",
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


def _record_payload(record: Any) -> Any:
    if isinstance(record, Mapping):
        return record.get("payload_json")
    return getattr(record, "payload_json", None)


def _record_attr(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        if name == "extraction_result_id":
            return record.get("extraction_result_id") or record.get("id")
        return record.get(name)
    if name == "extraction_result_id":
        return getattr(record, "extraction_result_id", None) or getattr(record, "id", None)
    return getattr(record, name, None)


def _current_turn_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    trace_id = get_current_trace_id()
    for index, event_record in enumerate(list_current_turn_curation_context(), start=1):
        refs = event_record.get("refs") if isinstance(event_record, Mapping) else {}
        builder = refs.get("builder_finalization") if isinstance(refs, Mapping) else None
        records.append(
            {
                "extraction_result_id": f"current-turn:{index}",
                "source_kind": "current_turn",
                "origin_session_id": get_current_session_id(),
                "trace_id": (
                    refs.get("trace_id") or trace_id
                    if isinstance(refs, Mapping)
                    else trace_id
                ),
                "agent_key": None,
                "adapter_key": _domain_pack_from_payload(event_record.get("payload_json")),
                "flow_run_id": None,
                "candidate_count": _candidate_count(event_record.get("payload_json")),
                "conversation_summary": "Current-turn internal extraction result.",
                "payload_json": event_record.get("payload_json"),
                "metadata": {
                    "tool_name": refs.get("tool_name") if isinstance(refs, Mapping) else None,
                    "builder_run_id": builder.get("builder_run_id") if isinstance(builder, Mapping) else None,
                    "builder_candidate_ids": builder.get("candidate_ids") if isinstance(builder, Mapping) else None,
                },
            }
        )
    return records


def _dedupe_extraction_records(records: Iterable[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[str] = set()
    for record in records:
        record_id = str(_record_attr(record, "extraction_result_id") or "").strip()
        key = record_id or str(id(record))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _authorized_extraction_results(
    *,
    scope: str,
    session_id: str,
    user_id: str,
    document_id: str | None,
    flow_run_id: str | None,
) -> list[Any]:
    if scope == "current_turn":
        records = _current_turn_records()
        if records:
            return records
        current_trace_id = get_current_trace_id()
        if current_trace_id:
            return [
                record
                for record in list_extraction_results(
                    origin_session_id=session_id,
                    user_id=user_id,
                    source_kind=CurationExtractionSourceKind.CHAT,
                    document_id=document_id,
                )
                if str(getattr(record, "trace_id", "") or "") == current_trace_id
            ]
        return []
    if scope == "current_document":
        if not document_id:
            return []
        return _dedupe_extraction_records(
            [
                *list_extraction_results(
                    origin_session_id=session_id,
                    user_id=user_id,
                    source_kind=CurationExtractionSourceKind.CHAT,
                    document_id=document_id,
                ),
                *list_extraction_results(
                    origin_session_id=session_id,
                    user_id=user_id,
                    source_kind=CurationExtractionSourceKind.FLOW,
                    document_id=document_id,
                ),
            ]
        )
    if scope == "flow_run":
        if not flow_run_id:
            return []
        return list_extraction_results(
            user_id=user_id,
            source_kind=CurationExtractionSourceKind.FLOW,
            flow_run_id=flow_run_id,
        )
    if scope == "extraction_result":
        records: list[Any] = [
            *_current_turn_records(),
            *list_extraction_results(
                origin_session_id=session_id,
                user_id=user_id,
                source_kind=CurationExtractionSourceKind.CHAT,
            ),
            *list_extraction_results(
                origin_session_id=session_id,
                user_id=user_id,
                source_kind=CurationExtractionSourceKind.FLOW,
            ),
        ]
        if flow_run_id:
            records.extend(
                list_extraction_results(
                    user_id=user_id,
                    source_kind=CurationExtractionSourceKind.FLOW,
                    flow_run_id=flow_run_id,
                )
            )
        return _dedupe_extraction_records(records)
    return _dedupe_extraction_records(
        [
            *list_extraction_results(
                origin_session_id=session_id,
                user_id=user_id,
                source_kind=CurationExtractionSourceKind.CHAT,
            ),
            *list_extraction_results(
                origin_session_id=session_id,
                user_id=user_id,
                source_kind=CurationExtractionSourceKind.FLOW,
            ),
        ]
    )


def _filter_extraction_records(
    records: Iterable[Any],
    *,
    extraction_result_id: str | None,
    trace_id: str | None,
    flow_run_id: str | None,
    adapter_keys: Sequence[str] | None,
) -> list[Any]:
    allowed_adapters = {str(key).strip() for key in adapter_keys or [] if str(key).strip()}
    result_id = _optional_text(extraction_result_id)
    trace = _optional_text(trace_id)
    flow = _optional_text(flow_run_id)
    filtered: list[Any] = []
    for record in records:
        if result_id and str(_record_attr(record, "extraction_result_id") or "") != result_id:
            continue
        if trace and str(_record_attr(record, "trace_id") or "") != trace:
            continue
        if flow and str(_record_attr(record, "flow_run_id") or "") != flow:
            continue
        if allowed_adapters and str(_record_attr(record, "adapter_key") or "") not in allowed_adapters:
            continue
        filtered.append(record)
    return filtered


def _normalize_uuid_or_none(value: str | UUID | None) -> UUID | None:
    if isinstance(value, UUID):
        return value
    text = _optional_text(value)
    if not text:
        return None
    try:
        return UUID(text)
    except (TypeError, ValueError):
        return None


def authorize_review_session_for_context(
    db: Session,
    *,
    review_session_id: str | UUID,
    user_id: str,
    current_chat_session_id: str | None,
    flow_run_id: str | None = None,
) -> CurationReviewSession | None:
    """Return a review session only when this chat/user is allowed to inspect it."""

    normalized_session_id = _normalize_uuid_or_none(review_session_id)
    if normalized_session_id is None:
        return None

    session = db.get(CurationReviewSession, normalized_session_id)
    if session is None:
        return None

    if session.created_by_id == user_id or session.assigned_curator_id == user_id:
        return session

    scope_conditions = []
    if current_chat_session_id:
        scope_conditions.append(
            CurationExtractionResultRecord.origin_session_id == current_chat_session_id
        )
    if flow_run_id:
        scope_conditions.append(CurationExtractionResultRecord.flow_run_id == flow_run_id)
    if not scope_conditions:
        return None
    scope_condition = scope_conditions[0] if len(scope_conditions) == 1 else or_(*scope_conditions)

    linked_statement = (
        select(
            exists().where(
                CurationCandidate.session_id == normalized_session_id,
                CurationCandidate.extraction_result_id == CurationExtractionResultRecord.id,
                CurationExtractionResultRecord.user_id == user_id,
                scope_condition,
            )
        )
    )
    has_link = bool(db.scalar(linked_statement))
    return session if has_link else None


def _model_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _review_session_ref(session_payload: Mapping[str, Any]) -> dict[str, Any]:
    adapter = _as_mapping(session_payload.get("adapter"))
    document = _as_mapping(session_payload.get("document"))
    return {
        "review_session_id": session_payload.get("session_id"),
        "flow_run_id": session_payload.get("flow_run_id"),
        "adapter_key": adapter.get("adapter_key"),
        "document_id": document.get("document_id"),
    }


def _review_session_inventory_payload(session_payload: Mapping[str, Any]) -> dict[str, Any]:
    document = _as_mapping(session_payload.get("document"))
    adapter = _as_mapping(session_payload.get("adapter"))
    progress = _as_mapping(session_payload.get("progress"))
    validation = _as_mapping(session_payload.get("validation")) or None
    return {
        **_review_session_ref(session_payload),
        "status": session_payload.get("status"),
        "adapter": adapter,
        "document": {
            "document_id": document.get("document_id"),
            "title": document.get("title"),
            "page_count": document.get("page_count"),
        },
        "progress": progress,
        "validation": _bounded_json(validation, text_limit=_FIELD_TEXT_LIMIT, list_limit=5),
        "current_candidate_id": session_payload.get("current_candidate_id"),
        "prepared_at": session_payload.get("prepared_at"),
        "last_worked_at": session_payload.get("last_worked_at"),
        "available_details": [
            "inventory",
            "summary",
            "candidates",
            "objects",
            "evidence",
            "validation_findings",
            "field",
        ],
    }


def _review_session_summary_payload(
    session_payload: Mapping[str, Any],
    *,
    db: Session,
    user_id: str,
) -> dict[str, Any]:
    extraction_results = [
        _bounded_json(item, text_limit=_FIELD_TEXT_LIMIT, list_limit=5)
        for item in session_payload.get("extraction_results", [])
        if isinstance(item, Mapping)
    ]
    file_refs = _session_file_refs(
        db,
        session_id=str(session_payload.get("session_id") or ""),
        user_id=user_id,
        limit=5,
        cursor=None,
    )[0]
    return {
        **_review_session_inventory_payload(session_payload),
        "extraction_results": extraction_results,
        "tags": list(session_payload.get("tags") or []),
        "notes_preview": _preview_text(session_payload.get("notes")),
        "warning_count": len(session_payload.get("warnings") or []),
        "submitted_at": session_payload.get("submitted_at"),
        "latest_submission": _bounded_json(
            session_payload.get("latest_submission"),
            text_limit=_FIELD_TEXT_LIMIT,
            list_limit=5,
        ),
        "file_refs": file_refs,
    }


def _candidate_matches_ref(candidate: Mapping[str, Any], object_ref: str | None) -> bool:
    ref = _optional_text(object_ref)
    if not ref:
        return True
    projection_ref = _as_mapping(candidate.get("projection_ref"))
    values = {
        str(candidate.get("candidate_id") or ""),
        str(candidate.get("display_label") or ""),
        str(candidate.get("secondary_label") or ""),
        str(candidate.get("adapter_key") or ""),
        str(projection_ref.get("envelope_id") or ""),
        str(projection_ref.get("object_id") or ""),
    }
    normalized_payload = candidate.get("normalized_payload")
    if isinstance(normalized_payload, Mapping):
        values.update(
            str(normalized_payload.get(key) or "")
            for key in ("object_type", "id", "object_id", "pending_ref_id")
        )
    return ref in values


def _compact_review_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    projection_ref = _as_mapping(candidate.get("projection_ref"))
    normalized_payload = _as_mapping(candidate.get("normalized_payload"))
    return {
        "candidate_id": candidate.get("candidate_id"),
        "session_id": candidate.get("session_id"),
        "envelope_id": projection_ref.get("envelope_id"),
        "object_id": projection_ref.get("object_id"),
        "object_type": normalized_payload.get("object_type"),
        "status": candidate.get("status"),
        "adapter_key": candidate.get("adapter_key"),
        "display_label": candidate.get("display_label"),
        "secondary_label": candidate.get("secondary_label"),
        "fields": _selected_scalar_fields(normalized_payload),
        "evidence_count": len(candidate.get("evidence_anchors") or [])
        + len(candidate.get("evidence_anchor_projections") or []),
        "validation_finding_count": len(candidate.get("validation_summary_projections") or []),
    }


def _review_candidates_detail(
    workspace_payload: Mapping[str, Any],
    *,
    object_ref: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], int, bool, str | None]:
    candidates = [
        candidate
        for candidate in workspace_payload.get("candidates", [])
        if isinstance(candidate, Mapping) and _candidate_matches_ref(candidate, object_ref)
    ]
    page, truncated, next_cursor = _offset_page(candidates, limit=limit, cursor=cursor)
    return [_compact_review_candidate(candidate) for candidate in page], len(candidates), truncated, next_cursor


def _review_evidence_detail(
    workspace_payload: Mapping[str, Any],
    *,
    object_ref: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], int, bool, str | None]:
    records: list[dict[str, Any]] = []
    for candidate in workspace_payload.get("candidates", []):
        if not isinstance(candidate, Mapping) or not _candidate_matches_ref(candidate, object_ref):
            continue
        for item in [
            *(candidate.get("evidence_anchors") or []),
            *(candidate.get("evidence_anchor_projections") or []),
        ]:
            if not isinstance(item, Mapping):
                continue
            compact = _compact_evidence_record(item)
            compact.setdefault("candidate_id", candidate.get("candidate_id"))
            records.append(compact)
    page, truncated, next_cursor = _offset_page(records, limit=limit, cursor=cursor)
    return page, len(records), truncated, next_cursor


def _review_validation_detail(
    workspace_payload: Mapping[str, Any],
    *,
    object_ref: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], int, bool, str | None]:
    records: list[dict[str, Any]] = []
    for candidate in workspace_payload.get("candidates", []):
        if not isinstance(candidate, Mapping) or not _candidate_matches_ref(candidate, object_ref):
            continue
        for item in [
            candidate.get("validation"),
            *(candidate.get("validation_summary_projections") or []),
        ]:
            if not isinstance(item, Mapping):
                continue
            compact = _bounded_json(item, text_limit=_FIELD_TEXT_LIMIT, list_limit=5)
            if isinstance(compact, Mapping):
                record = dict(compact)
                record.setdefault("candidate_id", candidate.get("candidate_id"))
                records.append(record)
    page, truncated, next_cursor = _offset_page(records, limit=limit, cursor=cursor)
    return page, len(records), truncated, next_cursor


def _review_field_detail(
    session_payload: Mapping[str, Any],
    workspace_payload: Mapping[str, Any],
    *,
    object_ref: str | None,
    field_path: str,
) -> dict[str, Any]:
    ref = _optional_text(object_ref)
    if ref:
        for candidate in workspace_payload.get("candidates", []):
            if isinstance(candidate, Mapping) and _candidate_matches_ref(candidate, ref):
                return {
                    "review_session_id": session_payload.get("session_id"),
                    "candidate_id": candidate.get("candidate_id"),
                    "field_path": field_path,
                    "value": _bounded_json(
                        _field_path_value(candidate, field_path),
                        text_limit=_FIELD_TEXT_LIMIT,
                        list_limit=10,
                    ),
                }
    return {
        "review_session_id": session_payload.get("session_id"),
        "field_path": field_path,
        "value": _bounded_json(
            _field_path_value(session_payload, field_path),
            text_limit=_FIELD_TEXT_LIMIT,
            list_limit=10,
        ),
    }


def _inspect_review_session_context(
    *,
    db: Session,
    review_session_id: str | None,
    user_id: str,
    current_chat_session_id: str,
    flow_run_id: str | None,
    detail: str,
    object_ref: str | None,
    field_path: str | None,
    limit: int,
    cursor: str | None,
) -> str:
    if not review_session_id:
        return _tool_response(
            "invalid_request",
            "review_session_id is required when scope=review_session.",
            scope="review_session",
            detail=detail,
        )

    session = authorize_review_session_for_context(
        db,
        review_session_id=review_session_id,
        user_id=user_id,
        current_chat_session_id=current_chat_session_id,
        flow_run_id=flow_run_id,
    )
    if session is None:
        return _tool_response(
            "unauthorized_context",
            "No authorized review session matched that curation context lookup.",
            scope="review_session",
            detail=detail,
        )

    session_detail = get_session_detail(db, session.id)
    session_payload = _model_payload(session_detail)
    refs = [_review_session_ref(session_payload)]

    if detail == "inventory":
        results = [_review_session_inventory_payload(session_payload)]
        total_count = 1
        truncated = False
        next_cursor = None
    elif detail == "summary":
        results = [
            _review_session_summary_payload(
                session_payload,
                db=db,
                user_id=user_id,
            )
        ]
        total_count = 1
        truncated = False
        next_cursor = None
    elif detail in {"candidates", "objects", "evidence", "validation_findings", "field"}:
        workspace = get_session_workspace(db, session.id)
        workspace_payload = _model_payload(workspace).get("workspace", {})
        if detail in {"candidates", "objects"}:
            results, total_count, truncated, next_cursor = _review_candidates_detail(
                workspace_payload,
                object_ref=object_ref,
                limit=limit,
                cursor=cursor,
            )
        elif detail == "evidence":
            results, total_count, truncated, next_cursor = _review_evidence_detail(
                workspace_payload,
                object_ref=object_ref,
                limit=limit,
                cursor=cursor,
            )
        elif detail == "validation_findings":
            results, total_count, truncated, next_cursor = _review_validation_detail(
                workspace_payload,
                object_ref=object_ref,
                limit=limit,
                cursor=cursor,
            )
        else:
            if not field_path:
                return _tool_response(
                    "invalid_request",
                    "field_path is required when detail=field.",
                    scope="review_session",
                )
            results = [
                _review_field_detail(
                    session_payload,
                    workspace_payload,
                    object_ref=object_ref,
                    field_path=field_path,
                )
            ]
            total_count = 1
            truncated = False
            next_cursor = None
    else:
        return _tool_response(
            "invalid_detail",
            "Unsupported review session detail. Use inventory, summary, candidates, objects, evidence, validation_findings, or field.",
            scope="review_session",
            detail=detail,
        )

    return _tool_response(
        "ok",
        "Authorized review session context matched.",
        scope="review_session",
        detail=detail,
        refs=refs,
        results=results,
        total_count=total_count,
        truncated=truncated,
        next_cursor=next_cursor,
    )


def _file_output_response(file: FileOutput) -> dict[str, Any]:
    return {
        "file_id": str(file.id),
        "filename": file.filename,
        "file_type": file.file_type,
        "file_size": file.file_size,
        "curator_id": file.curator_id,
        "session_id": file.session_id,
        "trace_id": file.trace_id,
        "agent_name": file.agent_name,
        "generation_model": file.generation_model,
        "download_count": file.download_count,
        "last_download_at": file.last_download_at,
        "created_at": file.created_at,
        "download_url": f"/api/files/{file.id}/download",
        "metadata": _bounded_json(file.file_metadata or {}, text_limit=_FIELD_TEXT_LIMIT, list_limit=10),
    }


def _load_authorized_file_output(
    db: Session,
    *,
    file_id: str | None,
    user_id: str,
) -> FileOutput | None:
    normalized_file_id = _normalize_uuid_or_none(file_id)
    if normalized_file_id is None:
        return None
    file = db.get(FileOutput, normalized_file_id)
    if file is None or file.curator_id != user_id:
        return None
    return file


def _safe_file_output_path(file: FileOutput) -> Path | None:
    storage = FileOutputStorageService()
    try:
        base_path = storage.base_path.resolve()
        file_path = Path(file.file_path).resolve()
        if not file_path.is_relative_to(base_path):
            return None
    except (OSError, ValueError, PathSecurityError):
        return None
    if not file_path.exists() or not file_path.is_file():
        return None
    return file_path


def _read_bounded_text(path: Path, *, max_bytes: int = _MAX_FILE_PREVIEW_BYTES) -> tuple[str, bool]:
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    return text, truncated


def _csv_rows_preview(
    text: str,
    *,
    delimiter: str,
    limit: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = list(reader.fieldnames or [])
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(reader):
        if index >= limit:
            break
        preview_row: dict[str, Any] = {}
        for key, value in row.items():
            preview_key = "_extra_values" if key is None else str(key)
            preview_row[preview_key] = _bounded_json(
                value,
                text_limit=_FIELD_TEXT_LIMIT,
                list_limit=10,
            )
        rows.append(preview_row)
    return headers, rows


def _json_file_data(path: Path) -> tuple[Any | None, str | None]:
    text, truncated = _read_bounded_text(path)
    if truncated:
        return None, "JSON file is too large for bounded field/preview parsing; use metadata or download."
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"Malformed JSON: {exc.msg}"


def _file_schema(file: FileOutput, path: Path, *, limit: int) -> dict[str, Any]:
    text, truncated = _read_bounded_text(path)
    if file.file_type in {"csv", "tsv"}:
        delimiter = "," if file.file_type == "csv" else "\t"
        headers, rows = _csv_rows_preview(text, delimiter=delimiter, limit=min(limit, _MAX_FILE_SCHEMA_ROWS))
        return {
            "file_id": str(file.id),
            "file_type": file.file_type,
            "delimiter": delimiter,
            "headers": headers,
            "preview_rows": rows,
            "row_count_estimate": max(0, text.count("\n") - 1),
            "truncated": truncated,
        }
    if file.file_type == "json":
        data, error = _json_file_data(path)
        if error:
            return {"file_id": str(file.id), "file_type": "json", "error": error, "truncated": truncated}
        if isinstance(data, Mapping):
            return {
                "file_id": str(file.id),
                "file_type": "json",
                "top_level_type": "object",
                "object_keys": list(data.keys())[:50],
            }
        if isinstance(data, list):
            key_union: list[str] = []
            seen: set[str] = set()
            for item in data[:limit]:
                if isinstance(item, Mapping):
                    for key in item:
                        if key not in seen:
                            seen.add(str(key))
                            key_union.append(str(key))
            return {
                "file_id": str(file.id),
                "file_type": "json",
                "top_level_type": "array",
                "item_count": len(data),
                "item_key_union": key_union,
            }
        return {"file_id": str(file.id), "file_type": "json", "top_level_type": type(data).__name__}
    return {"file_id": str(file.id), "file_type": file.file_type, "schema": "unsupported"}


def _file_preview(file: FileOutput, path: Path, *, limit: int) -> dict[str, Any]:
    text, truncated = _read_bounded_text(path)
    if file.file_type in {"csv", "tsv"}:
        delimiter = "," if file.file_type == "csv" else "\t"
        headers, rows = _csv_rows_preview(text, delimiter=delimiter, limit=limit)
        return {
            "file_id": str(file.id),
            "headers": headers,
            "rows": rows,
            "truncated": truncated,
        }
    if file.file_type == "json":
        data, error = _json_file_data(path)
        if error:
            return {"file_id": str(file.id), "error": error, "truncated": truncated}
        return {
            "file_id": str(file.id),
            "preview": _bounded_json(data, text_limit=_FIELD_TEXT_LIMIT, list_limit=limit),
            "truncated": truncated,
        }
    return {
        "file_id": str(file.id),
        "text_preview": _preview_text(text, limit=_FIELD_TEXT_LIMIT),
        "truncated": truncated,
    }


def _file_field(file: FileOutput, path: Path, *, field_path: str) -> dict[str, Any]:
    if file.file_type != "json":
        return {
            "file_id": str(file.id),
            "field_path": field_path,
            "error": "detail=field is supported for JSON files only.",
        }
    data, error = _json_file_data(path)
    if error:
        return {"file_id": str(file.id), "field_path": field_path, "error": error}
    return {
        "file_id": str(file.id),
        "field_path": field_path,
        "value": _bounded_json(
            _field_path_value(data, field_path),
            text_limit=_FIELD_TEXT_LIMIT,
            list_limit=10,
        ),
    }


def _inspect_file_context(
    *,
    db: Session,
    file_id: str | None,
    user_id: str,
    detail: str,
    field_path: str | None,
    limit: int,
) -> str:
    file = _load_authorized_file_output(db, file_id=file_id, user_id=user_id)
    if file is None:
        return _tool_response(
            "unauthorized_context",
            "No authorized file matched that curation context lookup.",
            scope="file",
            detail=detail,
        )

    if detail in {"metadata", "inventory"}:
        results = [_file_output_response(file)]
    else:
        path = _safe_file_output_path(file)
        if path is None:
            return _tool_response(
                "unavailable",
                "The authorized file is not available in bounded storage.",
                scope="file",
                detail=detail,
                refs=[{"file_id": str(file.id)}],
            )
        if detail == "schema":
            results = [_file_schema(file, path, limit=limit)]
        elif detail == "preview":
            results = [_file_preview(file, path, limit=limit)]
        elif detail == "field":
            if not field_path:
                return _tool_response(
                    "invalid_request",
                    "field_path is required when detail=field.",
                    scope="file",
                )
            results = [_file_field(file, path, field_path=field_path)]
        else:
            return _tool_response(
                "invalid_detail",
                "Unsupported file detail. Use metadata, inventory, schema, preview, or field.",
                scope="file",
                detail=detail,
            )

    return _tool_response(
        "ok",
        "Authorized file context matched.",
        scope="file",
        detail=detail,
        refs=[{"file_id": str(file.id), "session_id": file.session_id}],
        results=results,
        total_count=1,
        truncated=False,
        next_cursor=None,
    )


def _session_file_refs(
    db: Session,
    *,
    session_id: str,
    user_id: str,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], int, bool, str | None]:
    if not session_id:
        return [], 0, False, None
    other_user_file = (
        db.query(FileOutput)
        .filter(FileOutput.session_id == session_id)
        .filter(FileOutput.curator_id != user_id)
        .first()
    )
    if other_user_file:
        return [], -1, False, None
    query = (
        db.query(FileOutput)
        .filter(FileOutput.session_id == session_id)
        .filter(FileOutput.curator_id == user_id)
        .order_by(FileOutput.created_at.desc())
    )
    files = query.all()
    page, truncated, next_cursor = _offset_page(files, limit=limit, cursor=cursor)
    refs = [_file_output_response(file) for file in page]
    return refs, len(files), truncated, next_cursor


def _inspect_session_files_context(
    *,
    db: Session,
    review_session_id: str | None,
    current_chat_session_id: str,
    user_id: str,
    flow_run_id: str | None,
    limit: int,
    cursor: str | None,
) -> str:
    session_id = current_chat_session_id
    refs: list[dict[str, Any]] = []
    if review_session_id:
        session = authorize_review_session_for_context(
            db,
            review_session_id=review_session_id,
            user_id=user_id,
            current_chat_session_id=current_chat_session_id,
            flow_run_id=flow_run_id,
        )
        if session is None:
            return _tool_response(
                "unauthorized_context",
                "No authorized review session matched that file lookup.",
                scope="session_files",
            )
        session_id = str(session.id)
        refs.append({"review_session_id": session_id, "flow_run_id": session.flow_run_id})

    files, total_count, truncated, next_cursor = _session_file_refs(
        db,
        session_id=session_id,
        user_id=user_id,
        limit=limit,
        cursor=cursor,
    )
    if total_count < 0:
        return _tool_response(
            "unauthorized_context",
            "Not authorized to access files from this mixed-curator session.",
            scope="session_files",
        )
    return _tool_response(
        "ok",
        "Authorized session file refs matched.",
        scope="session_files",
        detail="inventory",
        refs=refs or [{"session_id": session_id}],
        results=files,
        total_count=total_count,
        truncated=truncated,
        next_cursor=next_cursor,
    )


async def inspect_curation_context(
    *,
    scope: str = "current_chat",
    detail: str = "inventory",
    extraction_result_id: str | None = None,
    trace_id: str | None = None,
    flow_run_id: str | None = None,
    review_session_id: str | None = None,
    file_id: str | None = None,
    adapter_keys: list[str] | None = None,
    object_ref: str | None = None,
    field_path: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> str:
    """Inspect bounded canonical curation context for the main-chat supervisor."""

    session_id = get_current_session_id()
    user_id = get_current_user_id()
    if not session_id or not user_id:
        return _tool_response(
            "unavailable",
            "Curation context lookup is only available inside an active chat session.",
        )

    normalized_scope = str(scope or "current_chat").strip() or "current_chat"
    normalized_detail = str(detail or "inventory").strip() or "inventory"
    bounded_limit = _normalize_limit(limit, default=5)
    if normalized_scope == "review_session":
        db = SessionLocal()
        try:
            return _inspect_review_session_context(
                db=db,
                review_session_id=review_session_id,
                user_id=user_id,
                current_chat_session_id=session_id,
                flow_run_id=flow_run_id,
                detail=normalized_detail,
                object_ref=object_ref,
                field_path=field_path,
                limit=bounded_limit,
                cursor=cursor,
            )
        finally:
            db.close()
    if normalized_scope == "file":
        db = SessionLocal()
        try:
            return _inspect_file_context(
                db=db,
                file_id=file_id,
                user_id=user_id,
                detail=normalized_detail,
                field_path=field_path,
                limit=bounded_limit,
            )
        finally:
            db.close()
    if normalized_scope == "session_files":
        db = SessionLocal()
        try:
            return _inspect_session_files_context(
                db=db,
                review_session_id=review_session_id,
                current_chat_session_id=session_id,
                user_id=user_id,
                flow_run_id=flow_run_id,
                limit=bounded_limit,
                cursor=cursor,
            )
        finally:
            db.close()
    if normalized_scope == "extraction_result" and not _optional_text(extraction_result_id):
        return _tool_response(
            "invalid_request",
            "extraction_result_id is required when scope=extraction_result.",
            scope=normalized_scope,
            detail=normalized_detail,
        )
    document_id = _active_document_id(user_id)
    records = _authorized_extraction_results(
        scope=normalized_scope,
        session_id=session_id,
        user_id=user_id,
        document_id=document_id,
        flow_run_id=flow_run_id,
    )
    records = _filter_extraction_records(
        records,
        extraction_result_id=extraction_result_id,
        trace_id=trace_id,
        flow_run_id=flow_run_id,
        adapter_keys=adapter_keys,
    )
    total_count = len(records)
    use_nested_cursor = (
        normalized_detail in {"objects", "evidence", "validation_findings"}
        and total_count == 1
    )
    detail_cursor = cursor if use_nested_cursor else None
    if use_nested_cursor:
        records = records[:1]
        truncated = False
        next_cursor = None
    else:
        records, truncated, next_cursor = _offset_page(
            records,
            limit=bounded_limit,
            cursor=cursor,
        )

    if not records:
        return _tool_response(
            "no_context",
            "No authorized extraction results matched that curation context lookup.",
            scope=normalized_scope,
            detail=normalized_detail,
            document_id=document_id,
        )

    if normalized_detail == "inventory":
        results = [_extraction_record_ref(record) for record in records]
    elif normalized_detail == "summary":
        results = [_extraction_record_summary(record) for record in records]
    elif normalized_detail == "objects":
        results = [
            _objects_detail(
                record,
                object_ref=object_ref,
                limit=bounded_limit,
                cursor=detail_cursor,
            )
            for record in records
        ]
    elif normalized_detail == "evidence":
        results = [
            _evidence_detail(record, limit=bounded_limit, cursor=detail_cursor)
            for record in records
        ]
    elif normalized_detail == "validation_findings":
        results = [
            _validation_findings_detail(record, limit=bounded_limit, cursor=detail_cursor)
            for record in records
        ]
    elif normalized_detail == "field":
        if not field_path:
            return _tool_response(
                "invalid_request",
                "field_path is required when detail=field.",
                scope=normalized_scope,
            )
        results = [_field_detail(record, field_path=field_path) for record in records]
    else:
        return _tool_response(
            "invalid_detail",
            "Unsupported curation detail. Use inventory, summary, objects, evidence, validation_findings, or field.",
            detail=normalized_detail,
        )

    return _tool_response(
        "ok",
        f"{len(records)} authorized extraction result(s) matched.",
        scope=normalized_scope,
        detail=normalized_detail,
        refs=[_extraction_record_ref(record) for record in records],
        results=results,
        total_count=total_count,
        truncated=truncated,
        next_cursor=next_cursor,
    )


def _active_document_id(user_id: str) -> str | None:
    from src.lib.chat_state import document_state

    active_document = document_state.get_document(user_id)
    if not isinstance(active_document, Mapping):
        return None
    return _optional_text(active_document.get("id"))


def _domain_pack_from_payload(payload: Any) -> str | None:
    if isinstance(payload, Mapping):
        return _optional_text(payload.get("domain_pack_id") or payload.get("adapter_key"))
    return None


def _candidate_count(payload: Any) -> int:
    if isinstance(payload, Mapping):
        objects = payload.get("objects")
        if isinstance(objects, list):
            return len(objects)
        candidates = payload.get("candidates")
        if isinstance(candidates, list):
            return len(candidates)
        items = payload.get("items")
        if isinstance(items, list):
            return len(items)
        run_summary = payload.get("run_summary")
        if isinstance(run_summary, Mapping):
            count = run_summary.get("candidate_count")
            if isinstance(count, int):
                return count
    return 0


def _extraction_record_ref(record: Any) -> dict[str, Any]:
    metadata = _record_attr(record, "metadata") or _record_attr(record, "extraction_metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    return {
        "extraction_result_id": str(_record_attr(record, "extraction_result_id") or ""),
        "trace_id": _record_attr(record, "trace_id"),
        "adapter_key": _record_attr(record, "adapter_key"),
        "agent_key": _record_attr(record, "agent_key"),
        "source_kind": str(_record_attr(record, "source_kind") or ""),
        "flow_run_id": _record_attr(record, "flow_run_id"),
        "builder_run_id": metadata.get("builder_run_id"),
        "tool_name": metadata.get("tool_name"),
    }


def _extraction_record_summary(record: Any) -> dict[str, Any]:
    payload = _record_payload(record)
    return {
        **_extraction_record_ref(record),
        "candidate_count": _record_attr(record, "candidate_count") or _candidate_count(payload),
        "object_count": len(payload.get("objects", [])) if isinstance(payload, Mapping) and isinstance(payload.get("objects"), list) else 0,
        "validation_finding_count": len(payload.get("validation_findings", [])) if isinstance(payload, Mapping) and isinstance(payload.get("validation_findings"), list) else 0,
        "conversation_summary": _preview_text(_record_attr(record, "conversation_summary")),
    }


def _objects_detail(
    record: Any,
    *,
    object_ref: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    payload = _record_payload(record)
    objects = []
    if isinstance(payload, Mapping):
        raw_objects = payload.get("objects")
        if isinstance(raw_objects, list):
            objects = [item for item in raw_objects if isinstance(item, Mapping)]
        elif isinstance(payload.get("items"), list):
            objects = [item for item in payload["items"] if isinstance(item, Mapping)]
    ref = _optional_text(object_ref)
    if ref:
        objects = [
            item
            for item in objects
            if ref in {
                str(item.get("pending_ref_id") or ""),
                str(item.get("object_type") or ""),
                str(item.get("id") or ""),
            }
        ]
    visible, truncated, next_cursor = _offset_page(objects, limit=limit, cursor=cursor)
    return {
        **_extraction_record_ref(record),
        "objects": [_compact_object(item) for item in visible],
        "object_count": len(objects),
        "truncated": truncated,
        "next_cursor": next_cursor,
    }


def _compact_object(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else item
    return {
        "object_type": item.get("object_type"),
        "pending_ref_id": item.get("pending_ref_id"),
        "status": item.get("status"),
        "fields": _selected_scalar_fields(payload),
        "evidence_record_ids": _bounded_json(item.get("evidence_record_ids") or []),
    }


def _selected_scalar_fields(payload: Any, *, limit: int = 12) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    fields: dict[str, Any] = {}
    for key in sorted(payload):
        value = payload.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            fields[str(key)] = _bounded_json(value, text_limit=_FIELD_TEXT_LIMIT)
        if len(fields) >= limit:
            break
    return fields


def _evidence_detail(record: Any, *, limit: int, cursor: str | None) -> dict[str, Any]:
    evidence = _collect_evidence_records(
        _record_payload(record),
        limit=_MAX_EVIDENCE_SCAN_RECORDS + 1,
    )
    scan_truncated = len(evidence) > _MAX_EVIDENCE_SCAN_RECORDS
    evidence = evidence[:_MAX_EVIDENCE_SCAN_RECORDS]
    visible, truncated, next_cursor = _offset_page(
        evidence,
        limit=limit,
        cursor=cursor,
    )
    return {
        **_extraction_record_ref(record),
        "evidence": [_compact_evidence_record(item) for item in visible],
        "evidence_count": len(evidence),
        "evidence_count_is_lower_bound": scan_truncated,
        "truncated": scan_truncated or truncated,
        "next_cursor": next_cursor,
    }


def _validation_findings_detail(
    record: Any,
    *,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    payload = _record_payload(record)
    findings = []
    if isinstance(payload, Mapping) and isinstance(payload.get("validation_findings"), list):
        findings = [item for item in payload["validation_findings"] if isinstance(item, Mapping)]
    visible, truncated, next_cursor = _offset_page(
        findings,
        limit=limit,
        cursor=cursor,
    )
    return {
        **_extraction_record_ref(record),
        "validation_findings": [
            _bounded_json(item, text_limit=_FIELD_TEXT_LIMIT, list_limit=5)
            for item in visible
        ],
        "finding_count": len(findings),
        "truncated": truncated,
        "next_cursor": next_cursor,
    }


def _field_detail(record: Any, *, field_path: str) -> dict[str, Any]:
    value = _field_path_value(_record_payload(record), field_path)
    return {
        **_extraction_record_ref(record),
        "field_path": field_path,
        "value": _bounded_json(value, text_limit=_FIELD_TEXT_LIMIT, list_limit=10),
    }


def _field_path_value(payload: Any, field_path: str) -> Any:
    current = payload
    for part in field_path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
            continue
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (TypeError, ValueError, IndexError):
                return None
            continue
        return None
    return current


_EVIDENCE_CONTAINER_KEYS = {
    "evidence",
    "evidence_records",
    "evidence_anchors",
    "evidence_items",
}
_EVIDENCE_RECORD_KEYS = {
    "evidence_record_id",
    "verified_quote",
    "quote",
    "evidence_quote",
    "source_quote",
    "source_chunk_id",
    "chunk_id",
    "source_section",
    "section",
    "page",
    "page_number",
}


def _collect_evidence_records(
    value: Any,
    *,
    limit: int,
) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []

    def visit(item: Any, depth: int = 0) -> None:
        if len(found) >= limit or depth > 8:
            return
        if isinstance(item, Mapping):
            if any(key in item for key in _EVIDENCE_RECORD_KEYS):
                found.append(item)
                if len(found) >= limit:
                    return
            for key, nested in item.items():
                if key in _EVIDENCE_CONTAINER_KEYS or isinstance(nested, (list, Mapping)):
                    visit(nested, depth + 1)
        elif isinstance(item, list):
            for nested in item:
                visit(nested, depth + 1)

    visit(value)
    return found


def _compact_evidence_record(item: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "evidence_record_id",
        "id",
        "verified_quote",
        "quote",
        "evidence_quote",
        "source_quote",
        "page",
        "page_number",
        "section",
        "source_section",
        "chunk_id",
        "source_chunk_id",
        "field_path",
        "pending_ref_id",
        "object_ref",
        "status",
        "confidence",
    ):
        value = item.get(key)
        if value is not None:
            compact[key] = _bounded_json(value, text_limit=_FIELD_TEXT_LIMIT, list_limit=5)
    if not compact:
        compact = _selected_scalar_fields(item, limit=8)
    return compact


__all__ = [
    "authorize_review_session_for_context",
    "inspect_chat_traces",
    "inspect_curation_context",
]
