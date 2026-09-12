"""Real database coverage: hiding changes only preferences, never saved flows."""
from uuid import uuid4
import pytest
from sqlalchemy import delete
from src.models.sql.database import SessionLocal
from src.models.sql.user import User
from src.models.sql.curation_flow import CurationFlow
from src.models.sql.flow_shortcut_preference import FlowShortcutPreference
from src.schemas.flow_shortcuts import FlowShortcutUpdate
from src.services.flow_shortcut_service import read_shortcuts, save_shortcuts, ShortcutConflict, ShortcutUnavailable


def test_shortcut_ownership_order_conflicts_and_hide_all():
    db = SessionLocal()
    users = []
    try:
        owner = User(auth_sub='shortcut-owner-'+uuid4().hex)
        other = User(auth_sub='shortcut-other-'+uuid4().hex)
        db.add_all([owner, other])
        db.commit()
        users = [owner.id, other.id]
        flows = [CurationFlow(user_id=uid, name=uuid4().hex, flow_definition={}) for uid in [owner.id, owner.id, other.id]]
        db.add_all(flows)
        db.commit()
        a, b, foreign = [flow.id for flow in flows]
        assert read_shortcuts(db, owner.id).flow_ids is None
        saved = save_shortcuts(db, owner.id, FlowShortcutUpdate(flow_ids=[b, a], revision=0))
        assert saved.flow_ids == [b, a]
        with SessionLocal() as fresh:
            assert read_shortcuts(fresh, owner.id).flow_ids == [b, a]
            assert read_shortcuts(fresh, other.id).flow_ids is None
        with pytest.raises(ShortcutConflict):
            save_shortcuts(db, owner.id, FlowShortcutUpdate(flow_ids=[a], revision=0))
        db.rollback()
        with pytest.raises(ShortcutUnavailable):
            save_shortcuts(db, owner.id, FlowShortcutUpdate(flow_ids=[foreign], revision=1))
        db.rollback()
        assert read_shortcuts(db, owner.id).flow_ids == [b, a]
        saved = save_shortcuts(db, owner.id, FlowShortcutUpdate(flow_ids=[], revision=1))
        assert saved.flow_ids == []
        assert all(db.get(CurationFlow, id).is_active for id in [a,b,foreign])
        db.get(CurationFlow, a).is_active = False
        db.commit()
        with pytest.raises(ShortcutUnavailable):
            save_shortcuts(db, owner.id, FlowShortcutUpdate(flow_ids=[a], revision=2))
        db.rollback()
    finally:
        db.rollback()
        if users:
            db.execute(delete(FlowShortcutPreference).where(FlowShortcutPreference.user_id.in_(users)))
            db.execute(delete(CurationFlow).where(CurationFlow.user_id.in_(users)))
            db.execute(delete(User).where(User.id.in_(users)))
            db.commit()
        db.close()
