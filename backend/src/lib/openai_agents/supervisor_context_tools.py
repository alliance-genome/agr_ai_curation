"""Bounded main-chat lookup tools for the supervisor agent."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

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
from src.lib.openai_agents.curation_context_registry import (
    list_current_turn_curation_context,
)
from src.models.sql.database import SessionLocal
from src.schemas.curation_workspace import CurationExtractionSourceKind


_TEXT_PREVIEW_LIMIT = 220
_FIELD_TEXT_LIMIT = 500
_MAX_LIST_LIMIT = 20
_MAX_TRACE_ARRAY_ITEMS = 20
_MAX_TRACE_TEXT = 1200
_MAX_TRACE_INVENTORY_MESSAGES = 5000
_MAX_EVIDENCE_SCAN_RECORDS = 200


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
        return list_extraction_results(
            origin_session_id=session_id,
            user_id=user_id,
            source_kind=CurationExtractionSourceKind.CHAT,
            document_id=document_id,
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
        ]
        if flow_run_id:
            records.extend(
                list_extraction_results(
                    user_id=user_id,
                    source_kind=CurationExtractionSourceKind.FLOW,
                    flow_run_id=flow_run_id,
                )
            )
        return records
    return list_extraction_results(
        origin_session_id=session_id,
        user_id=user_id,
        source_kind=CurationExtractionSourceKind.CHAT,
    )


def _filter_extraction_records(
    records: Iterable[Any],
    *,
    extraction_result_id: str | None,
    trace_id: str | None,
    adapter_keys: Sequence[str] | None,
) -> list[Any]:
    allowed_adapters = {str(key).strip() for key in adapter_keys or [] if str(key).strip()}
    result_id = _optional_text(extraction_result_id)
    trace = _optional_text(trace_id)
    filtered: list[Any] = []
    for record in records:
        if result_id and str(_record_attr(record, "extraction_result_id") or "") != result_id:
            continue
        if trace and str(_record_attr(record, "trace_id") or "") != trace:
            continue
        if allowed_adapters and str(_record_attr(record, "adapter_key") or "") not in allowed_adapters:
            continue
        filtered.append(record)
    return filtered


async def inspect_curation_context(
    *,
    scope: str = "current_chat",
    detail: str = "inventory",
    extraction_result_id: str | None = None,
    trace_id: str | None = None,
    flow_run_id: str | None = None,
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
        adapter_keys=adapter_keys,
    )
    bounded_limit = _normalize_limit(limit, default=5)
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


__all__ = ["inspect_chat_traces", "inspect_curation_context"]
