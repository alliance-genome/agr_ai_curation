"""Bounded batch projections and stateless, owner-bound discovery traversals."""

import base64
import binascii
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError, AwareDatetime
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from src.lib.openai_agents.config import (
    get_batch_page_default,
    get_batch_page_max,
    get_batch_terminal_window_seconds,
)
from src.models.sql.batch import Batch, BatchStatus
from src.models.sql.curation_flow import CurationFlow
from src.schemas.batch import (
    BatchSummary,
    BatchListResponse,
    BatchTerminalResponse,
    BatchLookupResponse,
)

BatchFilter = Literal["active", "pending", "running", "completed", "cancelled"]


class BatchQueryError(ValueError):
    """Invalid discovery request; persisted-data failures remain server errors."""


class _Cursor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["history", "terminal"]
    owner: int
    status: BatchFilter | None
    start: AwareDatetime | None
    end: AwareDatetime
    last_time: AwareDatetime
    last_id: UUID

    def encode(self) -> str:
        return base64.urlsafe_b64encode(self.model_dump_json().encode()).decode()


def _decode(value: str, kind: str, owner: int, status: BatchFilter | None) -> _Cursor:
    try:
        cursor = _Cursor.model_validate_json(
            base64.b64decode(value, altchars=b"-_", validate=True)
        )
        if (cursor.kind, cursor.owner, cursor.status) != (kind, owner, status):
            raise BatchQueryError("Cursor does not match query")
        if cursor.end > datetime.now(timezone.utc):
            raise BatchQueryError("Invalid future cursor boundary")
        if cursor.last_time > cursor.end or (
            cursor.start is not None and cursor.last_time < cursor.start
        ):
            raise BatchQueryError("Invalid cursor bounds")
        if (kind == "terminal") != (cursor.start is not None):
            raise BatchQueryError("Invalid cursor window")
        return cursor
    except (ValidationError, ValueError, binascii.Error) as exc:
        raise BatchQueryError("Invalid batch cursor for this query") from exc


def page_limit(limit: int | None) -> int:
    maximum = get_batch_page_max()
    if limit is None:
        return min(get_batch_page_default(), maximum)
    if not 1 <= limit <= maximum:
        raise BatchQueryError(f"limit must be between 1 and {maximum}")
    return limit


def _projection():
    return select(
        Batch.id,
        Batch.flow_id,
        CurationFlow.name.label("flow_name"),
        Batch.status,
        Batch.total_documents,
        Batch.completed_documents,
        Batch.failed_documents,
        Batch.created_at,
        Batch.started_at,
        Batch.completed_at,
    ).outerjoin(CurationFlow, CurationFlow.id == Batch.flow_id)


def _page(db, filters, time_column, limit, cursor):
    if cursor is not None:
        filters = [
            *filters,
            tuple_(time_column, Batch.id) < (cursor.last_time, cursor.last_id),
        ]
    rows = (
        db.execute(
            _projection()
            .where(*filters)
            .order_by(time_column.desc(), Batch.id.desc())
            .limit(limit)
        )
        .mappings()
        .all()
    )
    summaries = [BatchSummary.model_validate(row) for row in rows]
    more = False
    if len(summaries) == limit:
        last = summaries[-1]
        last_time = (
            last.created_at if time_column is Batch.created_at else last.completed_at
        )
        more = db.scalar(
            select(
                select(Batch.id)
                .where(*filters, tuple_(time_column, Batch.id) < (last_time, last.id))
                .exists()
            )
        )
    return summaries, more


def list_summaries(
    db: Session, owner: int, limit=None, cursor=None, status: BatchFilter | None = None
):
    limit = page_limit(limit)
    if status is not None and status not in ("active", *[s.value for s in BatchStatus]):
        raise BatchQueryError("Invalid batch status filter")
    token = _decode(cursor, "history", owner, status) if cursor is not None else None
    end = token.end if token else datetime.now(timezone.utc)
    filters = [Batch.user_id == owner]
    if status == "active":
        filters.append(Batch.status.in_([BatchStatus.PENDING, BatchStatus.RUNNING]))
    elif status is not None:
        filters.append(Batch.status == status)
    total = db.scalar(select(func.count(Batch.id)).where(*filters))
    batches, more = _page(
        db, [*filters, Batch.created_at <= end], Batch.created_at, limit, token
    )
    next_cursor = None
    if more:
        last = batches[-1]
        next_cursor = _Cursor(
            kind="history",
            owner=owner,
            status=status,
            start=None,
            end=end,
            last_time=last.created_at,
            last_id=last.id,
        ).encode()
    return BatchListResponse(batches=batches, total=total, next_cursor=next_cursor)


def terminal_summaries(
    db: Session, owner: int, limit=None, cursor=None, since: datetime | None = None
):
    limit = page_limit(limit)
    now = datetime.now(timezone.utc)
    floor = now - timedelta(seconds=get_batch_terminal_window_seconds())
    if cursor is not None and since is not None:
        raise BatchQueryError("Use cursor or since, not both")
    if since is not None and (since.tzinfo is None or since > now):
        raise BatchQueryError("since must be a timezone-aware time no later than now")
    token = _decode(cursor, "terminal", owner, None) if cursor is not None else None
    if token is not None:
        assert token.start is not None  # Validated by _decode.
        start, end = token.start, token.end
    else:
        start, end = since if since is not None else floor, now
    reset = (end < floor or end - start > now - floor) if token else start < floor
    if reset:
        start, end, token = floor, now, None
    filters = [
        Batch.user_id == owner,
        Batch.status.in_([BatchStatus.COMPLETED, BatchStatus.CANCELLED]),
        Batch.completed_at >= start,
        Batch.completed_at <= end,
    ]
    batches, more = _page(db, filters, Batch.completed_at, limit, token)
    next_cursor = None
    if more:
        last = batches[-1]
        next_cursor = _Cursor(
            kind="terminal",
            owner=owner,
            status=None,
            start=start,
            end=end,
            last_time=last.completed_at,
            last_id=last.id,
        ).encode()
    return BatchTerminalResponse(
        batches=batches,
        window_start=start,
        window_end=end,
        next_cursor=next_cursor,
        coverage="reset" if reset else "recent_window",
    )


def lookup_summaries(db: Session, owner: int, ids: list[UUID]):
    if not ids or len(ids) > get_batch_page_max():
        raise BatchQueryError(
            f"ids must contain between 1 and {get_batch_page_max()} entries"
        )
    rows = (
        db.execute(_projection().where(Batch.user_id == owner, Batch.id.in_(ids)))
        .mappings()
        .all()
    )
    by_id = {row["id"]: BatchSummary.model_validate(row) for row in rows}
    unique = list(dict.fromkeys(ids))
    return BatchLookupResponse(
        batches=[by_id[i] for i in unique if i in by_id],
        unavailable_ids=[i for i in unique if i not in by_id],
    )
