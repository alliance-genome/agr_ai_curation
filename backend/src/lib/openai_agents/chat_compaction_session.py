"""Durable chat working context; shared runtime owns native compaction."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from typing import Any, Iterable, Sequence

from agents.memory import Session
from sqlalchemy import create_engine, select, text
from sqlalchemy.pool import NullPool

from src.lib.chat_history_repository import (
    ASSISTANT_CHAT_KIND,
    ChatHistoryRepository,
    ChatHistorySessionNotFoundError,
    ChatMessageRecord,
    MAX_MESSAGE_PAGE_SIZE,
)
from src.lib.chat_transcript import extract_flow_assistant_message
from src.models.sql.chat_message import ChatMessage as ChatMessageModel
from src.models.sql.chat_session import ChatSession as ChatSessionModel
from src.models.sql.database import SessionLocal


CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE = "context_compaction"
_PROJECTION_SCHEMA = "standard_chat_context_projection.v1"


def _message_item(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


async def _owned_io(operation, *args):
    """Drain owned DB work before cancellation can release its invocation."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        # Retrieve a concurrent DB exception without replacing cancellation.
        if not task.cancelled():
            task.exception()
        raise


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
        chat_kind: str = ASSISTANT_CHAT_KIND,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self.current_turn_id = current_turn_id
        self.chat_kind = chat_kind
        self._lease = None
        self._acquiring = False
        self._lease_key = int.from_bytes(hashlib.sha256(
            f"{user_id}\0{chat_kind}\0{session_id}".encode()).digest()[:8], "big", signed=True)

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        return await _owned_io(self._get_items_sync, limit)

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        await _owned_io(self._add_items_sync, [dict(item) for item in items])

    async def pop_item(self) -> dict[str, Any] | None:
        return await _owned_io(self._pop_item_sync)

    async def clear_session(self) -> None:
        await _owned_io(self._clear_session_sync)

    async def acquire_run(self) -> None:
        from .model_context_view import ContextRecoveryError
        if self._lease is not None or self._acquiring:
            raise ContextRecoveryError("This session object already owns a running request.")
        self._acquiring = True
        try:
            await _owned_io(self._acquire_run_sync)
        except BaseException:
            await self.release_run()
            raise
        finally:
            self._acquiring = False

    async def release_run(self, result=None) -> None:
        try:
            task = getattr(result, "run_loop_task", None)
            if result is not None and task is not None and not task.done():
                result.cancel()
                cancelled = False
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        if not task.done():
                            cancelled = True
                    except Exception:
                        break
                if not task.cancelled():
                    task.exception()
                if cancelled:
                    raise asyncio.CancelledError
        finally:
            await _owned_io(self._release_run_sync)

    def _authorize(self, repository):
        session = repository.get_session(session_id=self.session_id, user_auth_sub=self.user_id)
        if session is None or session.chat_kind != self.chat_kind:
            raise ChatHistorySessionNotFoundError("Chat session not found")

    def _acquire_run_sync(self):
        from .model_context_view import ContextRecoveryError
        with SessionLocal() as db:
            self._authorize(ChatHistoryRepository(db))
            lease_engine = create_engine(db.get_bind().url, poolclass=NullPool)
        # Long model runs must not reserve a connection in the application pool.
        # NullPool closes this dedicated PostgreSQL connection on release.
        connection = lease_engine.connect()
        try:
            # Cross-worker invocation ownership, without holding a SQL row lock
            # through model calls. The connection closes on process exit too.
            if connection.dialect.name != "postgresql":
                raise ContextRecoveryError("Concurrent chat execution requires PostgreSQL session locks.")
            acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": self._lease_key})
            connection.commit()
            if not acquired:
                raise ContextRecoveryError("This conversation already has a running request. Wait for it to finish or cancel it first.")
            self._lease = connection
        except BaseException:
            connection.invalidate()
            connection.close()
            raise

    def _release_run_sync(self):
        connection, self._lease = self._lease, None
        if connection is not None:
            try:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self._lease_key})
                connection.commit()
            except BaseException:
                connection.invalidate()
                raise
            finally:
                connection.close()

    def _lock_session(self, db):
        locked = db.execute(select(ChatSessionModel.session_id).where(
            ChatSessionModel.session_id == self.session_id,
            ChatSessionModel.user_auth_sub == self.user_id,
            ChatSessionModel.chat_kind == self.chat_kind,
            ChatSessionModel.deleted_at.is_(None)).with_for_update()).scalar_one_or_none()
        if locked is None:
            raise ChatHistorySessionNotFoundError("Chat session not found")

    def _get_items_sync(self, limit: int | None) -> list[dict[str, Any]]:
        db = SessionLocal()
        try:
            repository = ChatHistoryRepository(db)
            self._authorize(repository)
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
                return items[-limit:] if limit > 0 else []
            return items
        finally:
            db.close()

    def _add_items_sync(self, items: list[dict[str, Any]]) -> None:
        db = SessionLocal()
        try:
            repository = ChatHistoryRepository(db)
            self._authorize(repository)
            self._lock_session(db)
            projection = self._latest_projection_row(db)
            projection_items = items
            if projection is not None:
                covered_turn_ids = self._projection_covered_turn_ids(projection)
                base_items = self._projection_items(projection) + self._durable_items(
                    repository, after=projection.created_at,
                    excluded_turn_ids=self._current_turn_ids() | covered_turn_ids)
            else:
                base_items = self._durable_items(repository, after=None, excluded_turn_ids=self._current_turn_ids())
                covered_turn_ids = set()

            covered_turn_ids |= self._current_turn_ids()
            from .context_compaction import compacted_window, compaction_key
            native_keys = {key for item in items if (key := compaction_key(item))}
            # SDK add_items already contains the new delta. Equal user messages
            # are separate occurrences, and every tool/opaque item is retained.
            projected_items = compacted_window([*base_items, *projection_items], native_keys)
            self._upsert_projection(
                db=db,
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
            self._authorize(ChatHistoryRepository(db))
            self._lock_session(db)
            projection = self._latest_projection_row(db)
            if projection is None:
                return None
            items = self._projection_items(projection)
            if not items:
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
            self._authorize(repository)
            self._lock_session(db)
            self._upsert_projection(db=db, repository=repository,
                existing=self._latest_projection_row(db), items=[], covered_turn_ids=self._current_turn_ids())
            db.commit()
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
                ChatMessageModel.chat_kind == self.chat_kind,
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
                chat_kind=self.chat_kind,
                limit=MAX_MESSAGE_PAGE_SIZE,
                cursor=cursor,
                excluded_message_types={CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE},
                after_created_at=after,
            )
            messages.extend(page.items)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return _completed_exchange_items(messages, excluded_turn_ids=excluded_turn_ids)

    def _projection_items(self, projection: ChatMessageModel) -> list[dict[str, Any]]:
        payload = projection.payload_json if isinstance(projection.payload_json, dict) else {}
        if payload.get("schema") != _PROJECTION_SCHEMA:
            raise ValueError("Unsupported durable working-context schema")
        items = payload.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("Invalid durable working-context items")
        return [dict(item) for item in items]

    def _projection_covered_turn_ids(self, projection: ChatMessageModel) -> set[str]:
        payload = projection.payload_json if isinstance(projection.payload_json, dict) else {}
        raw_turn_ids = payload.get("covered_turn_ids")
        if not isinstance(raw_turn_ids, list):
            return set()
        return {str(turn_id) for turn_id in raw_turn_ids if str(turn_id).strip()}

    def _upsert_projection(
        self,
        *,
        db,
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
            db.delete(existing)
            db.flush()

        repository.append_message(
            session_id=self.session_id,
            user_auth_sub=self.user_id,
            chat_kind=self.chat_kind,
            role="assistant",
            message_type=CHAT_CONTEXT_COMPACTION_MESSAGE_TYPE,
            content=content,
            payload_json=payload,
        )

    def _projection_content(self, items: Sequence[dict[str, Any]]) -> str:
        return (
            f"Model working context for {self.chat_kind} "
            f"({len(items)} item(s)); exact transcript remains in durable chat history."
        )


def build_standard_chat_session(
    *, session_id: str, user_id: str, current_turn_id: str,
) -> DurableChatHistorySession:
    return DurableChatHistorySession(
        session_id=session_id, user_id=user_id, current_turn_id=current_turn_id,
    )
