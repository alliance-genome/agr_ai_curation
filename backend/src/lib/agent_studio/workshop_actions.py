"""Read-only preparation of explicit curator actions in the Workshop UI."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.lib.agent_studio.capability_catalog import (
    CapabilityCatalogContext, build_authorized_capability_catalog,
)


class WorkshopActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["open_agent", "new_agent", "save", "save_as", "show_section", "return_to_flow"]
    agent_id: str | None = None
    node_id: str | None = None
    mode: Literal["scratch", "template", "clone"] | None = None
    section: Literal["setup", "output_structure", "prompt", "tools", "versions", "tool_request", "manage"] | None = None


def prepare_workshop_action(db, *, context, user_id: int, active_group_ids: list[str], request: WorkshopActionRequest):
    """Authorize a navigation/dialog request; never mutate drafts or saved rows."""
    if user_id is None or context is None:
        raise ValueError("Open Agent Studio with an authenticated editor first")
    if context.active_tab not in {"agents", "flows", "agent_workshop"}:
        raise ValueError("This action needs an Agent Studio editor")
    action = request.action
    source = None
    origin = None
    if action in {"open_agent", "new_agent"}:
        label = "Start agent draft"
        if action == "new_agent" and request.mode is None:
            raise ValueError("Choose scratch, template or clone for the new draft")
        requires_agent = action == "open_agent" or request.mode in {"template", "clone"}
        if requires_agent:
            records = build_authorized_capability_catalog(db=db, context=CapabilityCatalogContext(
                user_id=user_id, active_group_ids=tuple(active_group_ids),
                active_tab="agent_workshop", artifact_kind="agent",
            ))
            record = next((item for item in records if item.kind == "agent"
                           and item.resource_id == request.agent_id and item.selectable
                           and item.availability == "available"), None)
            if record is None:
                raise ValueError("Choose an agent from your current authorized catalog")
            custom = record.resource_id.startswith("ca_")
            if action == "open_agent" and (not custom or record.authorization_scope != "owned"):
                raise ValueError("You can edit your own custom agents. Create a clone or template draft for this agent instead")
            if request.mode == "template" and custom:
                raise ValueError("Use clone for a saved custom agent")
            if request.mode == "clone" and not custom:
                raise ValueError("Use template for a pre-made agent")
            source = {"agent_id": record.resource_id, "name": record.name,
                      "updated_at": str(record.detail.get("updated_at") or ""),
                      "agent_revision_id": record.detail.get("identity_contract", {}).get("agent_revision_id")}
            if action == "open_agent":
                label = f"Open {record.name}"
        elif request.agent_id:
            raise ValueError("A scratch draft does not select a source agent")
        if context.active_tab == "flows" and context.flow_definition is not None:
            origin = {"flow_id": context.flow_id, "flow_draft_fingerprint": context.flow_draft_fingerprint}
            if action == "open_agent":
                matches = [node for node in context.flow_definition.nodes
                           if node.agent_id == request.agent_id and (request.node_id is None or node.id == request.node_id)]
                if len(matches) != 1:
                    raise ValueError("Choose the exact flow step to edit; this agent may be used more than once")
                node = matches[0]
                origin.update(node_id=node.id, agent_id=node.agent_id, agent_revision_id=node.agent_revision_id)
            elif request.node_id:
                raise ValueError("A new agent is added separately; it does not replace an existing flow step")
        elif request.node_id:
            raise ValueError("Open the originating flow before selecting its step")
    else:
        if context.active_tab != "agent_workshop" or context.agent_workshop is None:
            raise ValueError("Open the Workshop draft first")
        if request.agent_id or request.node_id or request.mode:
            raise ValueError("This action uses the current Workshop draft")
        if action == "show_section" and request.section is None:
            raise ValueError("Choose which Workshop section to show")
        label = {"save": "Review and save agent", "save_as": "Save as a new agent",
                 "show_section": "Open Workshop section", "return_to_flow": "Review in Flow"}[action]
    if request.section is not None and action != "show_section":
        raise ValueError("section is only used when opening a Workshop section")
    return {"success": True, "contract_version": "workshop_action.v1",
            "request": request.model_dump(mode="json", exclude_none=True), "label": label,
            "source": source, "origin": origin, "active_tab": context.active_tab,
            "flow_draft_fingerprint": context.flow_draft_fingerprint,
            "workshop_draft_fingerprint": context.agent_workshop.draft_fingerprint if context.agent_workshop else None,
            "saved": False, "message": "Use the action button to continue. Nothing has been changed or saved."}
