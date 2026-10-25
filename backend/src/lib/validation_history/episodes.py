"""Effective episode selection preserves explicitly abandoned preparation history."""

from sqlalchemy import select
from src.models.sql.validation_history import ValidationFact


def is_abandoned(db, artifact_id, episode_id):
    return (
        db.scalar(
            select(ValidationFact.id)
            .where(
                ValidationFact.artifact_id == artifact_id,
                ValidationFact.kind == "review_episode_abandoned",
                ValidationFact.parent_id == episode_id,
            )
            .limit(1)
        )
        is not None
    )


def latest_effective_episode(db, artifact_id):
    abandoned = select(ValidationFact.parent_id).where(
        ValidationFact.artifact_id == artifact_id,
        ValidationFact.kind == "review_episode_abandoned",
        ValidationFact.parent_id.is_not(None),
    )
    return db.scalar(
        select(ValidationFact)
        .where(
            ValidationFact.artifact_id == artifact_id,
            ValidationFact.kind == "review_episode",
            ValidationFact.id.not_in(abandoned),
        )
        .order_by(ValidationFact.sequence.desc())
        .limit(1)
    )
