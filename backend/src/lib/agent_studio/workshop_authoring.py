"""Read-only semantic proposals for the complete local Workshop draft."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Literal
import logging

from pydantic import BaseModel, ConfigDict, ValidationError

from src.lib.agent_studio.authoring_context import workshop_draft_fingerprint
from src.lib.agent_studio.authoring_validation import (
    AuthoringValidationContext,
    AuthoringValidationFinding,
    AuthoringValidationResult,
    ValidationPhase,
    report_authoring_validation_engine_failure,
    validate_custom_agent_authoring_draft,
)
from src.lib.agent_studio.models import AgentWorkshopContext
from src.lib.openai_agents.config import (
    get_agent_studio_workshop_proposal_max_operations,
    get_agent_studio_workshop_prompt_max_chars,
)

logger = logging.getLogger(__name__)


class WorkshopOperation(BaseModel):
    """One bounded general-agent action; profile internals are not writable."""

    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal[
        "set_name", "set_description", "set_instructions", "set_group_instructions",
        "reset_group_instructions", "set_include_group_rules", "select_model",
        "add_tool", "remove_tool", "select_output", "clear_output", "set_icon",
        "set_visibility", "set_allowed_groups",
    ]
    text: str | None = None
    resource_id: str | None = None
    resource_ids: list[str] | None = None
    enabled: bool | None = None
    reasoning: str | None = None


def workshop_save_candidate(workshop: AgentWorkshopContext) -> dict[str, Any]:
    """Project the editor onto the canonical Save validation contract."""

    return {
        "name": workshop.draft_name or "",
        "description": workshop.draft_description or "",
        "custom_prompt": workshop.prompt_draft or "",
        "group_prompt_overrides": deepcopy(workshop.group_prompt_overrides or {}),
        "icon": workshop.draft_icon or "",
        "visibility": workshop.draft_visibility or "private",
        "allowed_group_ids": list(workshop.draft_allowed_group_ids or []),
        "inherited_allowed_group_ids": list(workshop.inherited_allowed_group_ids or []),
        "include_group_rules": bool(workshop.include_group_rules),
        "model_id": workshop.draft_model_id or "",
        "model_reasoning": workshop.draft_model_reasoning or None,
        "tool_ids": list(workshop.draft_tool_ids or []),
        "output_schema_key": workshop.draft_output_schema_key or None,
    }


def workshop_system_tools(db, workshop, active_group_ids):
    """Resolve template-owned mechanical tools using the existing Save rules."""
    from src.lib.agent_studio import custom_agent_service as service
    if not workshop.template_source:
        return []
    template = service._resolve_system_template_agent(
        db, workshop.template_source, active_group_ids=active_group_ids,
    )
    return service._system_managed_tool_ids(db, list(template.tool_ids or []))


def normalize_workshop_candidate(workshop):
    """Use Save's text rules before showing the exact candidate to the curator."""
    from src.lib.agent_studio import custom_agent_service as service
    candidate = workshop.model_copy(deep=True)
    candidate.draft_name = (candidate.draft_name or "").strip()
    candidate.draft_icon = candidate.draft_icon or "🔧"
    candidate.group_prompt_overrides = service.normalize_editable_group_prompt_overrides(
        candidate.group_prompt_overrides,
    )
    normalization = service.normalize_custom_overlay_for_parent(
        candidate.template_source, candidate.prompt_draft,
    )
    if normalization.status == "needs_review" or normalization.removed_layer_kinds:
        raise ValueError("Editable instructions cannot contain inherited prompt layers.")
    service.reject_locked_prompt_markers(normalization.content, target="Custom instructions")
    candidate.prompt_draft = normalization.content
    return candidate


def workshop_source_is_current(source, timestamp):
    try:
        expected = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        actual = source.updated_at
        return expected.replace(tzinfo=expected.tzinfo or timezone.utc) == actual.replace(tzinfo=actual.tzinfo or timezone.utc)
    except (ValueError, TypeError):
        return False


def validate_workshop_context(db, *, workshop, user_id, active_group_ids, phase: ValidationPhase = "proposal"):
    """Reauthorize identity/floor and reuse the same live validator as Save."""

    from src.lib.agent_access import is_resource_access_allowed
    from src.lib.agent_studio import custom_agent_service as service

    candidate = workshop_save_candidate(workshop)
    findings = []
    try:
        if workshop_save_candidate(normalize_workshop_candidate(workshop)) != candidate:
            raise ValueError("Noncanonical draft")
    except ValueError:
        findings.append(AuthoringValidationFinding(
            code="noncanonical_editable_fields", severity="error", path="custom_agent",
            message="Normalize editable text, remove copied inherited layers, and use a nonempty icon before applying.",
        ))
    if candidate["visibility"] == "project":
        try:
            service._get_primary_project_id_for_user(db, user_id)
        except ValueError:
            findings.append(AuthoringValidationFinding(
                code="project_visibility_unavailable", severity="error", path="custom_agent.visibility",
                message="Project sharing is unavailable for this account.",
            ))
    if not candidate["custom_prompt"].strip() and not workshop.template_source:
        findings.append(AuthoringValidationFinding(
            code="empty_agent_prompt", severity="error", path="custom_agent.custom_prompt",
            message="A custom agent without a template requires editable instructions.",
        ))
    if any(len(text) > get_agent_studio_workshop_prompt_max_chars() for text in [
        candidate["custom_prompt"], *candidate["group_prompt_overrides"].values(),
    ]):
        findings.append(AuthoringValidationFinding(
            code="prompt_size_limit", severity="error", path="custom_agent.custom_prompt",
            message="The editable instructions exceed the configured prompt size limit.",
        ))
    source = None
    try:
        if workshop.custom_agent_id:
            source_id = service.parse_custom_agent_id(workshop.custom_agent_id)
            if source_id is None:
                raise ValueError("Invalid source identity")
            source = service.get_custom_agent_for_user(
                db, source_id, user_id,
            )
            if not workshop_source_is_current(source, workshop.custom_agent_updated_at):
                findings.append(AuthoringValidationFinding(
                    code="stale_saved_agent", severity="error", path="custom_agent.updated_at",
                    message="The saved agent changed. Reopen it and generate a fresh proposal.",
                ))
        elif workshop.clone_source_agent_id:
            source_id = service.parse_custom_agent_id(workshop.clone_source_agent_id)
            if source_id is None:
                raise ValueError("Invalid clone source identity")
            source = service.get_custom_agent_visible_to_user(
                db, source_id, user_id,
            )
            if not workshop_source_is_current(source, workshop.clone_source_updated_at):
                raise ValueError("Stale clone source")
        elif workshop.template_source:
            source = service._resolve_system_template_agent(
                db, workshop.template_source, active_group_ids=active_group_ids,
            )
        if source is not None:
            if (workshop.custom_agent_id or workshop.clone_source_agent_id) and (
                (getattr(source, "template_source", None) or None) != (workshop.template_source or None)
            ):
                raise ValueError("Changed template relationship")
            if not is_resource_access_allowed(
                visibility_allowed=True,
                allowed_group_ids=list(source.allowed_group_ids or []),
                active_group_ids=active_group_ids,
                resource_kind="custom_agent",
            ):
                raise ValueError("Unavailable source")
            floor = list(
                source.inherited_allowed_group_ids
                if workshop.custom_agent_id else source.allowed_group_ids
            )
            if sorted(floor) != sorted(candidate["inherited_allowed_group_ids"]):
                raise ValueError("Changed inherited access")
        elif candidate["inherited_allowed_group_ids"]:
            raise ValueError("Missing inherited source")
        required_tools = workshop_system_tools(db, workshop, active_group_ids)
        if not set(required_tools).issubset(candidate["tool_ids"]):
            findings.append(AuthoringValidationFinding(
                code="missing_inherited_tools", severity="error", path="custom_agent.tool_ids",
                message="The draft must retain its template-owned runtime tools.",
            ))
    except (ValueError, service.CustomAgentAccessError, service.CustomAgentNotFoundError):
        findings.append(AuthoringValidationFinding(
            code="unavailable_workshop_source", severity="error", path="custom_agent.identity",
            message="The agent source or inherited access changed. Reopen the draft and try again.",
        ))
    try:
        sources = service._agent_validation_sources(
            db, model_id=candidate["model_id"], tool_ids=candidate["tool_ids"],
            output_schema_key=candidate["output_schema_key"],
        )
        inherited_tools = set(source.tool_ids or []) if source is not None else set()
        if service.custom_agent_name_exists(
            db, user_id, candidate["name"].strip(),
            excluding_id=source.id if workshop.custom_agent_id and source is not None else None,
        ):
            findings.append(AuthoringValidationFinding(
                code="duplicate_agent_name", severity="error", path="custom_agent.name",
                message="You already have an active custom agent with this name.",
            ))
        if workshop.custom_agent_id and inherited_tools and not candidate["tool_ids"]:
            findings.append(AuthoringValidationFinding(
                code="empty_existing_tools", severity="error", path="custom_agent.tool_ids",
                message="Keep at least one attached tool, or use Save As for a tool-free copy.",
            ))
        sources = service.authorized_agent_validation_sources(
            db, user_id=user_id, active_group_ids=active_group_ids, sources=sources,
            inherited_tool_ids=inherited_tools,
        )
        result = validate_custom_agent_authoring_draft(
            candidate,
            context=AuthoringValidationContext.from_values(
                db_user_id=user_id, active_group_ids=active_group_ids,
            ),
            sources=sources,
            phase=phase,
        )
    except Exception:
        raise report_authoring_validation_engine_failure(
            artifact_kind="custom_agent", phase=phase,
        ) from None
    return AuthoringValidationResult(
        artifact_kind="custom_agent", phase=phase,
        findings=tuple(findings) + result.findings, candidate=result.candidate,
    )


def _required(value, field):
    if value is None:
        raise ValueError(f"This operation requires {field}.")
    return value


def apply_workshop_operations(base, operations):
    """Compile only named editor operations, preserving all other fields."""

    candidate = base.model_copy(deep=True)
    text_fields = {
        "set_name": "draft_name", "set_description": "draft_description",
        "set_instructions": "prompt_draft", "set_icon": "draft_icon",
        "set_visibility": "draft_visibility",
    }
    for item in operations:
        op = WorkshopOperation.model_validate(item)
        if op.operation in text_fields:
            setattr(candidate, text_fields[op.operation], _required(op.text, "text"))
        elif op.operation == "set_include_group_rules":
            candidate.include_group_rules = _required(op.enabled, "enabled")
        elif op.operation == "select_model":
            candidate.draft_model_id = _required(op.resource_id, "resource_id")
            candidate.draft_model_reasoning = op.reasoning
        elif op.operation == "set_allowed_groups":
            candidate.draft_allowed_group_ids = _required(op.resource_ids, "resource_ids")
        elif op.operation in {"select_output", "clear_output"}:
            candidate.draft_output_schema_key = (
                _required(op.resource_id, "resource_id") if op.operation == "select_output" else None
            )
        elif op.operation in {"add_tool", "remove_tool"}:
            tool_id = _required(op.resource_id, "resource_id")
            tools = list(candidate.draft_tool_ids or [])
            if op.operation == "add_tool" and tool_id not in tools:
                tools.append(tool_id)
            elif op.operation == "remove_tool":
                tools = [item for item in tools if item != tool_id]
            candidate.draft_tool_ids = tools
        else:
            group_id = _required(op.resource_id, "resource_id")
            overrides = dict(candidate.group_prompt_overrides or {})
            if op.operation == "set_group_instructions":
                overrides[group_id] = _required(op.text, "text")
            else:
                overrides.pop(group_id, None)
            candidate.group_prompt_overrides = overrides
    # Assignment on context models is deliberately not trusted as validation.
    return AgentWorkshopContext.model_validate(candidate.model_dump())


def propose_workshop_update(*, db, base, tool_input, user_id, active_group_ids, state):
    """Return a transient candidate; retain invalid candidates for bounded repair."""

    fingerprint = workshop_draft_fingerprint(base)
    if tool_input.get("base_draft_fingerprint") != fingerprint or base.draft_fingerprint != fingerprint:
        return {"success": False, "code": "stale_draft_fingerprint",
                "error": "Refresh the current Workshop draft before proposing changes."}
    operations = tool_input.get("operations")
    assumptions = tool_input.get("assumptions") or []
    if (not isinstance(assumptions, list)
            or len(assumptions) > get_agent_studio_workshop_proposal_max_operations()
            or any(not isinstance(item, str) for item in assumptions)):
        return {"success": False, "error": "Provide a bounded list of textual assumptions."}
    if not isinstance(operations, list) or not 1 <= len(operations) <= get_agent_studio_workshop_proposal_max_operations():
        return {"success": False, "error": "Provide a bounded, non-empty list of semantic operations."}
    working = state.get("candidate") if state.get("base") == fingerprint else None
    try:
        candidate = apply_workshop_operations(working or base, operations)
        try:
            candidate = normalize_workshop_candidate(candidate)
        except ValueError:
            # Keep the invalid editable values for canonical findings and same-turn repair.
            pass
        required_tools = workshop_system_tools(db, candidate, active_group_ids)
        candidate.draft_tool_ids = list(dict.fromkeys([*(candidate.draft_tool_ids or []), *required_tools]))
    except (ValueError, ValidationError):
        return {"success": False, "error": "Invalid Workshop operation. Use the declared typed fields."}
    result = validate_workshop_context(
        db, workshop=candidate, user_id=user_id, active_group_ids=active_group_ids,
    )
    state.update(base=fingerprint, candidate=candidate)
    before, after = workshop_save_candidate(base), workshop_save_candidate(candidate)
    diff = [
        {"kind": "added" if not before[key] and value else "removed" if before[key] and not value else "changed",
         "path": f"custom_agent.{key}", "before": before[key], "after": value}
        for key, value in after.items() if before[key] != value
    ]
    candidate.draft_fingerprint = workshop_draft_fingerprint(candidate)
    logger.info("Workshop proposal validated", extra={
        "phase": "proposal", "valid": result.valid, "operation_count": len(operations),
        "finding_count": len(result.findings), "changed_field_count": len(diff),
    })
    return {
        "contract_version": "workshop_authoring_proposal.v1",
        "artifact_kind": "custom_agent",
        "artifact_identity": base.custom_agent_id,
        "assumptions": assumptions,
        "success": result.valid, "valid": result.valid, "saved": False,
        "pending_user_approval": result.valid and bool(diff),
        "base_draft_fingerprint": fingerprint,
        "candidate_draft_fingerprint": candidate.draft_fingerprint,
        "candidate": candidate.model_dump(mode="json", exclude_none=True),
        "diff": diff, "findings": result.to_dict()["findings"],
        "change_summary": str(tool_input.get("change_summary") or "Review the proposed Workshop changes."),
    }
