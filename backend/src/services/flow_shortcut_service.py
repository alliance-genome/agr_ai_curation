"""Owner-scoped shortcut changes with optimistic conflict detection."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.models.sql.user import User
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.flow_shortcut_preference import FlowShortcutPreference
from src.schemas.flow_shortcuts import FlowShortcutResponse, FlowShortcutUpdate


class ShortcutConflict(ValueError):
    pass


class ShortcutUnavailable(ValueError):
    pass


def read_shortcuts(db: Session, user_id: int) -> FlowShortcutResponse:
    row = db.get(FlowShortcutPreference, user_id)
    return FlowShortcutResponse.model_validate({"flow_ids": row.flow_ids, "revision": row.revision}) if row else FlowShortcutResponse()


def save_shortcuts(db: Session, user_id: int, update: FlowShortcutUpdate) -> FlowShortcutResponse:
    # Serialize the initial insert too; lock only this curator's preference owner.
    db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one()
    row = db.get(FlowShortcutPreference, user_id, populate_existing=True)
    if update.revision != (row.revision if row else 0):
        raise ShortcutConflict("Your flow list changed in another tab. Refresh it and try again.")
    owned = set(db.scalars(select(CurationFlow.id).where(
        CurationFlow.user_id == user_id, CurationFlow.is_active.is_(True),
        CurationFlow.id.in_(update.flow_ids),
    ))) if update.flow_ids else set()
    if owned != set(update.flow_ids):
        raise ShortcutUnavailable("A selected flow is no longer available. Refresh your list.")
    if row is None:
        row = FlowShortcutPreference(user_id=user_id, flow_ids=[], revision=0)
        db.add(row)
    row.flow_ids = [str(value) for value in update.flow_ids]
    row.revision += 1
    db.flush()
    result = FlowShortcutResponse.model_validate({"flow_ids": row.flow_ids, "revision": row.revision})
    db.commit()
    return result
