"""At-most-once extraction submission for a durable chat turn and tool input.

A stopped/unknown dispatch stays fenced. Its saved CHAT artifacts can be reviewed
separately; a genuinely new curator extraction request has a new durable turn.
"""

from sqlalchemy.dialects.postgresql import insert
from src.lib.validation_history import store
from src.models.sql.validation_review import ChatReviewDispatch


def claim(context, tool, query):
    identity = [
        context.owner_id,
        context.document_id,
        context.session_id,
        context.turn_id,
        tool,
        query,
    ]
    key = store.canonical({"dispatch": identity})[0]
    with store.SessionLocal.begin() as db:
        store._authorize(
            db,
            store.HistoryScope(
                context.owner_id, context.document_id, context.session_id
            ),
        )
        inserted = db.execute(
            insert(ChatReviewDispatch)
            .values(
                id=key,
                owner_id=context.owner_id,
                document_id=context.document_id,
                session_id=context.session_id,
                turn_id=context.turn_id,
                state="started",
            )
            .on_conflict_do_nothing()
            .returning(ChatReviewDispatch.id)
        ).scalar_one_or_none()
        row = db.get(ChatReviewDispatch, key)
        return {
            "id": key,
            "claimed": inserted is not None,
            "state": row.state,
            "result": row.result,
        }


def complete(context, key, result):
    from sqlalchemy import select

    with store.SessionLocal.begin() as db:
        store._authorize(
            db,
            store.HistoryScope(
                context.owner_id, context.document_id, context.session_id
            ),
        )
        row = db.scalar(
            select(ChatReviewDispatch)
            .where(ChatReviewDispatch.id == key)
            .with_for_update()
        )
        if row is None or (
            row.owner_id,
            row.document_id,
            row.session_id,
            row.turn_id,
        ) != (
            context.owner_id,
            context.document_id,
            context.session_id,
            context.turn_id,
        ):
            raise store.HistoryAccessError("Chat dispatch belongs to another operation")
        if row.state == "completed":
            if row.result != result:
                raise store.HistoryConflict(
                    "Chat dispatch already has a different completion"
                )
            return
        row.state, row.result = "completed", result
