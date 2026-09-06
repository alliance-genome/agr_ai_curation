"""Typed, authorized database reads for curator questions about saved work.

These reads deliberately do not load a saved record into either editor. Current
draft inspection remains separate so saved settings cannot masquerade as edits.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.lib.agent_studio.execution_revision_service import (
    get_execution_revision, list_execution_revisions,
)
from src.lib.agent_studio.generic_profile_service import get_profile_revision
from src.lib.openai_agents.config import get_tool_page_default_limit
from src.models.sql.curation_flow import CurationFlow


class SavedResourceInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["list_flows", "flow", "agent_revisions", "agent_revision"]
    flow_id: str | None = None
    agent_id: str | None = None
    revision_id: str | None = None
    query: str | None = None
    offset: int = Field(default=0, ge=0)
    before_revision: int | None = Field(default=None, ge=1)


def _id(value: str | None, name: str) -> UUID:
    if not value:
        raise ValueError(f"Choose the {name} from the authorized catalog or saved work first")
    return UUID(value.removeprefix("ca_") if name == "agent" else value)


def _flow_summary(row):
    return {"flow_id": str(row.id), "name": row.name, "description": row.description,
            "updated_at": row.updated_at.isoformat(), "execution_count": row.execution_count}


def inspect_saved_resource(db, *, user_id: int, active_group_ids: list[str], request: SavedResourceInspection):
    """No writes or arbitrary SQL; all selectors are constrained to the caller."""
    if user_id is None:
        raise ValueError("Authenticated saved-work access is unavailable")
    if request.action in {"list_flows", "flow"}:
        statement = select(CurationFlow).where(
            CurationFlow.user_id == user_id, CurationFlow.is_active.is_(True),
        )
        if request.action == "flow":
            row = db.scalars(statement.where(CurationFlow.id == _id(request.flow_id, "flow"))).one_or_none()
            if row is None:
                raise ValueError("This saved flow is unavailable to you")
            return {"saved": True, "loaded_in_editor": False,
                    **_flow_summary(row), "flow_definition": row.flow_definition}
        if request.query:
            statement = statement.where(CurationFlow.name.icontains(request.query, autoescape=True))
        limit = get_tool_page_default_limit()
        rows = db.scalars(statement.order_by(CurationFlow.updated_at.desc(), CurationFlow.id)
                          .offset(request.offset).limit(limit + 1)).all()
        return {"saved": True, "flows": [_flow_summary(row) for row in rows[:limit]],
                "next_call": {"tool": "inspect_saved_studio_resource", "arguments": {
                    "action": "list_flows", "query": request.query, "offset": request.offset + limit,
                }} if len(rows) > limit else None}

    agent_id = _id(request.agent_id, "agent")
    if request.action == "agent_revisions":
        rows, cursor = list_execution_revisions(
            db, agent_id, user_id, active_group_ids=active_group_ids,
            before_revision=request.before_revision,
        )
        return {"saved": True, "agent_id": str(agent_id), "revisions": [{
            "revision_id": str(row.id), "revision": row.revision,
            "fingerprint": row.fingerprint, "notes": row.notes,
            "created_at": row.created_at.isoformat(),
        } for row, _ in rows], "next_call": {
            "tool": "inspect_saved_studio_resource", "arguments": {
                "action": "agent_revisions", "agent_id": request.agent_id, "before_revision": cursor,
            },
        } if cursor is not None else None}

    row, saved = get_execution_revision(
        db, agent_id, _id(request.revision_id, "revision"), user_id,
        active_group_ids=active_group_ids,
    )
    output_profile = None
    pin = saved.output_contract.generic_profile_ref
    if pin is not None:
        # get_execution_revision has already verified this exact profile pin and
        # the caller's access. Use the same authorized read for its field details.
        profile = get_profile_revision(db, pin.profile_id, pin.revision, user_id, include_archived=True)
        output_profile = {"profile_id": str(pin.profile_id), "revision_id": str(profile.id),
                          "revision": profile.revision, "fingerprint": profile.fingerprint,
                          "contract": profile.contract}
    return {"saved": True, "loaded_in_editor": False, "agent_id": str(agent_id),
            "revision_id": str(row.id), "revision": row.revision, "fingerprint": row.fingerprint,
            "snapshot": saved.model_dump(mode="json"), "output_profile": output_profile}
