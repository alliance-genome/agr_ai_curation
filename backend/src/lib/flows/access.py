"""Active flow access, using the canonical project membership boundary."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from src.lib.agent_studio.agent_service import get_project_ids_for_user
from src.models.sql import CurationFlow
from src.models.sql.agent import ProjectMember


def visible_flow_filter(user_id: int):
    """SQL predicate shared by paginated rows and totals."""
    return or_(
        CurationFlow.user_id == user_id,
        and_(
            CurationFlow.visibility == "project",
            CurationFlow.project_id.in_(
                select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
            ),
        ),
    )


def can_read_flow(db: Session, flow: CurationFlow, user_id: int) -> bool:
    """Owners and current project members can read/run an active flow."""
    return bool(flow.is_active and (
        flow.user_id == user_id or (
            flow.visibility == "project"
            and flow.project_id in get_project_ids_for_user(db, user_id)
        )
    ))


def get_visible_flow(db: Session, flow_id: UUID, user_id: int) -> CurationFlow:
    flow = db.query(CurationFlow).filter(
        CurationFlow.id == flow_id, CurationFlow.is_active.is_(True)
    ).first()
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    if not can_read_flow(db, flow, user_id):
        raise HTTPException(status_code=403, detail="Access denied")
    return flow


def generate_clone_name(db: Session, user_id: int, source_name: str) -> str:
    """Use the established (Copy), (Copy 2) convention within the name contract."""
    from src.schemas.flows import FLOW_NAME_MAX_CHARS

    names = set(db.scalars(select(CurationFlow.name).where(
        CurationFlow.user_id == user_id, CurationFlow.is_active.is_(True)
    )).all())
    copy_number = 1
    while True:
        suffix = " (Copy)" if copy_number == 1 else f" (Copy {copy_number})"
        candidate = source_name[:FLOW_NAME_MAX_CHARS - len(suffix)] + suffix
        if candidate not in names:
            return candidate
        copy_number += 1
