"""Typed, authorized database reads for curator questions about saved work.

These reads deliberately do not load a saved record into either editor. Current
draft inspection remains separate so saved settings cannot masquerade as edits.
"""

import hashlib
import json

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from src.lib.agent_studio.execution_revision_service import (
    get_execution_revision, list_execution_revisions,
)
from src.lib.agent_studio.generic_profile_service import get_profile_revision
from src.lib.openai_agents.config import (
    get_tool_page_default_limit, get_agent_studio_provider_tool_result_inline_max_chars,
)
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
    section: Literal["all", "instructions", "prompt_manifest", "tools", "group_prompts", "output_profile", "settings"] = "all"
    group_id: str | None = None
    start: int = Field(default=0, ge=0)
    max_chars: int | None = Field(default=None, ge=1)
    content_hash: str | None = None


def _id(value: str | None, name: str) -> UUID:
    if not value:
        raise ValueError(f"Choose the {name} from the authorized catalog or saved work first")
    return UUID(value.removeprefix("ca_") if name == "agent" else value)


def _flow_summary(row):
    return {"flow_id": str(row.id), "name": row.name, "description": row.description,
            "updated_at": row.updated_at.isoformat(), "execution_count": row.execution_count}


def _read_saved_resource(db, *, user_id: int, active_group_ids: list[str], request: SavedResourceInspection):
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


def _serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _bounded_saved_record(record: dict, request: SavedResourceInspection) -> dict:
    """Keep every page, including its continuation metadata, inside the provider cap."""
    if request.group_id is not None and request.section != "group_prompts":
        raise ValueError("Choose group_prompts before selecting a group")
    selected = record
    if request.section != "all":
        if request.action != "agent_revision":
            raise ValueError("Sections are available for an exact saved agent revision")
        snapshot = record["snapshot"]
        if request.section == "instructions":
            selected = snapshot["instructions"]
        elif request.section == "prompt_manifest":
            selected = snapshot["prompt_layer_manifest"]
        elif request.section == "output_profile":
            selected = record["output_profile"]
        elif request.section == "tools":
            selected = {key: snapshot.get(key) for key in (
                "tool_ids", "system_managed_tool_ids", "group_tool_policy",
            )}
        elif request.section == "group_prompts":
            layers = snapshot.get("group_prompt_layers", {})
            overrides = snapshot.get("group_prompt_overrides", {})
            if request.group_id is not None:
                if request.group_id not in layers and request.group_id not in overrides:
                    raise ValueError("This group has no prompt layer in the saved revision")
                layers = {request.group_id: layers.get(request.group_id)}
                overrides = {request.group_id: overrides.get(request.group_id)}
            selected = {"group_prompt_layers": layers, "group_prompt_overrides": overrides,
                        "group_rules_enabled": snapshot.get("group_rules_enabled")}
        else:
            selected = {key: value for key, value in snapshot.items() if key not in {
                "instructions", "group_prompt_layers", "group_prompt_overrides",
                "prompt_layer_manifest", "tool_ids", "system_managed_tool_ids", "group_tool_policy",
            }}

    serialized = _serialized(selected)
    identity = "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    if request.start > len(serialized):
        raise ValueError("start exceeds the saved record section length")
    if (request.start or request.content_hash is not None) and request.content_hash != identity:
        raise ValueError("Saved content changed or its identity is missing; restart this section")
    cap = get_agent_studio_provider_tool_result_inline_max_chars()
    requested = min(request.max_chars or cap, cap)
    common = {"saved": True, "loaded_in_editor": False, "section": request.section,
              "content_hash": identity, "total_chars": len(serialized)}

    def fits(result):
        # The API dispatcher adds success before provider serialization.
        return len(_serialized({"success": True, **result})) <= cap

    inline = {**(record if request.section == "all" else {"detail": selected}),
              **common, "complete": True, "next_call": None}
    if request.start == 0 and len(serialized) <= requested and fits(inline):
        return inline

    def page(end):
        complete = end == len(serialized)
        args = request.model_dump(exclude_none=True)
        args.update(start=end, max_chars=requested, content_hash=identity)
        return {**common, "encoding": "json", "start": request.start, "end": end,
                "content": serialized[request.start:end], "complete": complete,
                "next_call": None if complete else {
                    "tool": "inspect_saved_studio_resource", "arguments": args,
                }}

    low, high = request.start + 1, min(request.start + requested, len(serialized))
    result = page(request.start) if request.start == len(serialized) else None
    while low <= high:
        end = (low + high) // 2
        candidate = page(end)
        if fits(candidate):
            result = candidate
            low = end + 1
        else:
            high = end - 1
    if result is None or not fits(result):
        raise ValueError("Saved record page metadata cannot fit the configured provider result limit")
    return result


def inspect_saved_resource(db, *, user_id: int, active_group_ids: list[str], request: SavedResourceInspection):
    # Reauthorize on every page; hashes are continuity checks, never access grants.
    record = _read_saved_resource(db, user_id=user_id, active_group_ids=active_group_ids, request=request)
    if request.action in {"list_flows", "agent_revisions"}:
        if request.section != "all" or request.start or request.content_hash or request.group_id:
            raise ValueError("Use the list's next_call to continue saved record listings")
        return record
    return _bounded_saved_record(record, request)
