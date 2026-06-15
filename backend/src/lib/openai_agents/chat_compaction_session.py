"""Standard-chat OpenAI Responses compaction over durable chat history."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Iterable, Sequence

from agents.memory import OpenAIResponsesCompactionSession, Session
from openai import AsyncOpenAI
from sqlalchemy import delete, select

from src.lib.chat_history_repository import (
    ASSISTANT_CHAT_KIND,
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
    ChatMessageRecord,
    MAX_MESSAGE_PAGE_SIZE,
)
from src.lib.chat_transcript import extract_flow_assistant_message
from src.lib.openai_agents.config import get_standard_chat_compaction_token_threshold
from src.lib.runtime_payload_budget import json_size
from src.models.sql.chat_message import ChatMessage as ChatMessageModel
from src.models.sql.database import SessionLocal


logger = logging.getLogger(__name__)

CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE = "context_compaction"
_PROJECTION_SCHEMA = "standard_chat_context_projection.v1"


def _stable_item_key(item: Any) -> str:
    return json.dumps(item, sort_keys=True, ensure_ascii=True, default=str)


def _dedupe_items_preserving_latest(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        key = _stable_item_key(item)
        if key not in by_key:
            order.append(key)
        by_key[key] = dict(item)
    return [by_key[key] for key in order]


def _message_item(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
            continue
        text = block.get("content")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(part.strip() for part in parts if part and part.strip()).strip()


def _chat_replay_items(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    replay_items: list[dict[str, Any]] = []
    for item in items:
        role = str(item.get("role") or "").strip()
        if role not in {"user", "assistant"}:
            continue
        content = _content_text(item.get("content"))
        if not content:
            continue
        replay_items.append(_message_item(role, content))
    return replay_items


def _completed_exchange_items(
    messages: Iterable[ChatMessageRecord],
    *,
    excluded_turn_ids: set[str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pending_user: ChatMessageRecord | None = None

    for message in messages:
        if message.turn_id in excluded_turn_ids:
            continue
        if not message.content.strip():
            continue
        if message.role == "user":
            pending_user = message
            continue
        if pending_user is None:
            continue
        if message.role == "assistant" and message.message_type == "text":
            items.append(_message_item("user", pending_user.content))
            items.append(_message_item("assistant", message.content))
            pending_user = None
            continue
        if message.role == "flow":
            assistant_message = extract_flow_assistant_message(message)
            if assistant_message is None:
                continue
            items.append(_message_item("user", pending_user.content))
            items.append(_message_item("assistant", assistant_message))
            pending_user = None

    return items


def estimate_standard_chat_context_tokens(items: Sequence[dict[str, Any]]) -> int:
    """Estimate standard-chat model-live token usage from response input items."""

    return json_size(list(items)).estimated_tokens


def should_compact_standard_chat_context(context: dict[str, Any]) -> bool:
    """Return whether the SDK compaction hook should compact this chat session."""

    session_items = context.get("session_items")
    if not isinstance(session_items, list):
        raise TypeError("SDK compaction context must include list session_items")
    estimated_tokens = estimate_standard_chat_context_tokens(session_items)
    threshold = get_standard_chat_compaction_token_threshold()
    should_compact = estimated_tokens >= threshold
    if should_compact:
        logger.info(
            "Standard chat context compaction threshold reached",
            extra={
                "estimated_tokens": estimated_tokens,
                "threshold": threshold,
                "item_count": len(session_items),
            },
        )
    return should_compact


class DurableChatHistorySession(Session):
    """Agents SDK session backed by chat_messages context projection rows.

    Canonical text, flow, payload, and trace rows remain untouched for transcript
    recall. This session stores only the model-live projection that the SDK may
    compact and replace.
    """

    _ignore_ids_for_matching = True

    def __init__(
        self,
        *,
        session_id: str,
        user_id: str,
        current_turn_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.current_turn_id = current_turn_id
        self._replacement_in_progress = False

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._get_items_sync, limit)

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        await asyncio.to_thread(self._add_items_sync, [dict(item) for item in items])

    async def pop_item(self) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._pop_item_sync)

    async def clear_session(self) -> None:
        await asyncio.to_thread(self._clear_session_sync)

    def _get_items_sync(self, limit: int | None) -> list[dict[str, Any]]:
        db = SessionLocal()
        try:
            repository = ChatHistoryRepository(db)
            if repository.get_session(session_id=self.session_id, user_auth_sub=self.user_id) is None:
                return []
            projection = self._latest_projection_row(db)
            excluded_turn_ids = self._current_turn_ids()
            if projection is not None:
                projection_items = self._projection_items(projection)
                covered_turn_ids = self._projection_covered_turn_ids(projection)
                durable_items = self._durable_items(
                    repository,
                    after=projection.created_at,
                    excluded_turn_ids=excluded_turn_ids | covered_turn_ids,
                )
                items = projection_items + durable_items
            else:
                items = self._durable_items(
                    repository,
                    after=None,
                    excluded_turn_ids=excluded_turn_ids,
                )
            if limit is not None:
                return items[-max(0, limit) :]
            return items
        except ChatHistorySessionNotFoundError:
            return []
        finally:
            db.close()

    def _add_items_sync(self, items: list[dict[str, Any]]) -> None:
        db = SessionLocal()
        try:
            repository = ChatHistoryRepository(db)
            if repository.get_session(session_id=self.session_id, user_auth_sub=self.user_id) is None:
                raise ChatHistorySessionNotFoundError("Chat session not found")
            projection = self._latest_projection_row(db)
            replacement_in_progress = self._replacement_in_progress
            self._replacement_in_progress = False
            if replacement_in_progress:
                projection_items = list(items)
                base_items: list[dict[str, Any]] = []
                covered_turn_ids = self._current_turn_ids()
            else:
                projection_items = _chat_replay_items(items)
                if not projection_items:
                    return
                if projection is not None:
                    base_items = self._projection_items(projection)
                    covered_turn_ids = self._projection_covered_turn_ids(projection)
                else:
                    base_items = self._durable_items(
                        repository,
                        after=None,
                        excluded_turn_ids=self._current_turn_ids(),
                    )
                    covered_turn_ids = set()

            covered_turn_ids |= self._current_turn_ids()
            projected_items = _dedupe_items_preserving_latest([*base_items, *projection_items])
            self._upsert_projection(
                repository=repository,
                existing=projection,
                items=projected_items,
                covered_turn_ids=covered_turn_ids,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _pop_item_sync(self) -> dict[str, Any] | None:
        db = SessionLocal()
        try:
            projection = self._latest_projection_row(db)
            if projection is None:
                return None
            items = self._projection_items(projection)
            if not items:
                db.delete(projection)
                db.commit()
                return None
            popped = items.pop()
            payload = dict(projection.payload_json or {})
            payload["items"] = items
            projection.payload_json = payload
            projection.content = self._projection_content(items)
            db.commit()
            return popped
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _clear_session_sync(self) -> None:
        db = SessionLocal()
        try:
            repository = ChatHistoryRepository(db)
            if repository.get_session(session_id=self.session_id, user_auth_sub=self.user_id) is None:
                return
            db.execute(
                delete(ChatMessageModel).where(
                    ChatMessageModel.session_id == self.session_id,
                    ChatMessageModel.chat_kind == ASSISTANT_CHAT_KIND,
                    ChatMessageModel.message_type == CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE,
                )
            )
            db.commit()
            self._replacement_in_progress = True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _current_turn_ids(self) -> set[str]:
        return {self.current_turn_id} if self.current_turn_id else set()

    def _latest_projection_row(self, db) -> ChatMessageModel | None:
        return db.scalar(
            select(ChatMessageModel)
            .where(
                ChatMessageModel.session_id == self.session_id,
                ChatMessageModel.chat_kind == ASSISTANT_CHAT_KIND,
                ChatMessageModel.message_type == CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE,
            )
            .order_by(ChatMessageModel.created_at.desc(), ChatMessageModel.message_id.desc())
        )

    def _durable_items(
        self,
        repository: ChatHistoryRepository,
        *,
        after: datetime | None,
        excluded_turn_ids: set[str],
    ) -> list[dict[str, Any]]:
        messages: list[ChatMessageRecord] = []
        cursor = None
        while True:
            page = repository.list_messages(
                session_id=self.session_id,
                user_auth_sub=self.user_id,
                chat_kind=ASSISTANT_CHAT_KIND,
                limit=MAX_MESSAGE_PAGE_SIZE,
                cursor=cursor,
            )
            messages.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        if after is not None:
            messages = [message for message in messages if message.created_at > after]
        return _completed_exchange_items(messages, excluded_turn_ids=excluded_turn_ids)

    def _projection_items(self, projection: ChatMessageModel) -> list[dict[str, Any]]:
        payload = projection.payload_json if isinstance(projection.payload_json, dict) else {}
        if payload.get("schema") != _PROJECTION_SCHEMA:
            return []
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        return [dict(item) for item in items if isinstance(item, dict)]

    def _projection_covered_turn_ids(self, projection: ChatMessageModel) -> set[str]:
        payload = projection.payload_json if isinstance(projection.payload_json, dict) else {}
        raw_turn_ids = payload.get("covered_turn_ids")
        if not isinstance(raw_turn_ids, list):
            return set()
        return {str(turn_id) for turn_id in raw_turn_ids if str(turn_id).strip()}

    def _upsert_projection(
        self,
        *,
        repository: ChatHistoryRepository,
        existing: ChatMessageModel | None,
        items: list[dict[str, Any]],
        covered_turn_ids: set[str],
    ) -> None:
        payload = {
            "schema": _PROJECTION_SCHEMA,
            "items": items,
            "covered_turn_ids": sorted(covered_turn_ids),
            "current_turn_id": self.current_turn_id,
        }
        content = self._projection_content(items)
        if existing is not None:
            existing.content = content
            existing.payload_json = payload
            return

        repository.append_message(
            session_id=self.session_id,
            user_auth_sub=self.user_id,
            chat_kind=ASSISTANT_CHAT_KIND,
            role="assistant",
            message_type=CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE,
            content=content,
            payload_json=payload,
        )

    def _projection_content(self, items: Sequence[dict[str, Any]]) -> str:
        return (
            "Compacted standard-chat model-live context projection "
            f"({len(items)} item(s)); exact transcript remains in durable chat history."
        )


def build_standard_chat_compaction_session(
    *,
    session_id: str,
    user_id: str,
    current_turn_id: str,
    model: str,
    client: AsyncOpenAI | None = None,
) -> OpenAIResponsesCompactionSession:
    """Build the SDK compaction wrapper for one standard-chat supervisor turn."""

    durable_session = DurableChatHistorySession(
        session_id=session_id,
        user_id=user_id,
        current_turn_id=current_turn_id,
    )
    return OpenAIResponsesCompactionSession(
        session_id=session_id,
        underlying_session=durable_session,
        client=client,
        model=model,
        should_trigger_compaction=should_compact_standard_chat_context,
    )


__all__ = [
    "CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE",
    "DurableChatHistorySession",
    "build_standard_chat_compaction_session",
    "estimate_standard_chat_context_tokens",
    "should_compact_standard_chat_context",
]
