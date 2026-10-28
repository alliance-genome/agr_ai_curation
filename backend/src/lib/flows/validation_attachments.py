"""Flow validation attachment policy derived from domain-pack metadata."""

from __future__ import annotations

from functools import lru_cache
import re
from typing import Any, Mapping
from sqlalchemy.orm import Session

from src.lib.domain_packs.registry import load_domain_pack_registry
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationAttachmentOption,
    validate_active_validator_agent_references,
)
from src.lib.flow_edge_roles import VALIDATION_ATTACHMENT_EDGE_ROLE
from src.lib.flows.persisted_flow_migrations import (
    PersistedFlowMigrationError,
    validate_persisted_flow_definition,
)
from src.schemas.flows import (
    FlowDefinition,
    FlowValidationAttachmentGroup,
    FlowValidationAttachmentSelection,
)


class FlowValidationAttachmentError(ValueError):
    """Raised when a flow validation attachment selection violates policy."""


@lru_cache(maxsize=1)
def _domain_pack_validation_registries() -> dict[str, DomainPackValidationRegistry]:
    registry = load_domain_pack_registry()
    if not registry.loaded_packs:
        registry = _load_package_domain_pack_registry()
    return {
        domain_pack.pack_id: DomainPackValidationRegistry.from_domain_pack(domain_pack)
        for domain_pack in registry.loaded_packs
    }


def domain_pack_validation_registries() -> dict[str, DomainPackValidationRegistry]:
    """Return cached validation registries keyed by domain-pack ID."""

    return dict(_domain_pack_validation_registries())


def validation_attachment_catalog_by_agent(
    agent_registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return flow-builder validation options keyed by agent ID."""

    if agent_registry is None:
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        agent_registry = AGENT_REGISTRY

    return {
        agent_id: [option.to_dict() for option in _options_for_agent_entry(entry)]
        for agent_id, entry in sorted(agent_registry.items())
    }


def validation_attachment_options_for_agent(
    agent_id: str,
    agent_registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[ValidationAttachmentOption, ...]:
    """Return domain-pack validation options for one agent."""

    if agent_registry is None:
        from src.lib.agent_studio.catalog_service import AGENT_REGISTRY

        agent_registry = AGENT_REGISTRY

    entry = agent_registry.get(agent_id)
    if entry is None:
        return ()
    return _options_for_agent_entry(entry)


def reconcile_revision_attachments(
    candidate: FlowDefinition,
    *,
    entries_by_node: Mapping[str, Mapping[str, Any] | None],
    retargeted_node_ids: frozenset[str],
    db: Session | None = None,
) -> FlowDefinition:
    """Refresh checks only for an explicit, authorized exact-revision selection.

    Profile fingerprints change even for instruction-only edits. Carry opt-outs
    across that identity change only when the mapping and validator target agree.
    Replacement edges need explicit review when their declared identity changes;
    never delete them as a side effect of changing a revision.
    """
    refreshed = candidate.model_copy(deep=True)

    def identity(item):
        return (
            re.sub(r"profile-[0-9a-f]{64}-", "profile-", item.attachment_id),
            item.validator_package_id, item.validator_agent_id, item.tool_name,
            item.scope, item.object_type, item.object_role, item.field_path,
        )

    for node in refreshed.nodes:
        if node.id not in retargeted_node_ids:
            continue
        entry = entries_by_node.get(node.id)
        if entry is None:
            raise FlowValidationAttachmentError("Select an accessible saved agent revision before refreshing its checks.")
        options = _options_for_agent_entry(entry, db=db)
        old_by_identity: dict[tuple, list[FlowValidationAttachmentSelection]] = {}
        for selection in node.data.validation_attachments:
            old_by_identity.setdefault(identity(selection), []).append(selection)
        selections = []
        matched_old_ids = set()
        for option in options:
            matches = old_by_identity.get(identity(option), [])
            if len(matches) > 1:
                raise FlowValidationAttachmentError("Review this step's automatic checks before changing its revision: multiple old choices match one new check.")
            existing = matches[0] if matches else None
            if existing is not None:
                matched_old_ids.add(existing.attachment_id)
            payload = option.to_dict()
            payload["enabled"] = existing.enabled if existing is not None else option.default_enabled
            if existing is not None and not existing.enabled and option.default_enabled and not option.allow_opt_out:
                raise FlowValidationAttachmentError("The selected revision requires a check you previously disabled. Review and enable that check before changing revisions.")
            selections.append(FlowValidationAttachmentSelection(**payload))
        if options and any(not old.enabled and old.attachment_id not in matched_old_ids for old in node.data.validation_attachments):
            raise FlowValidationAttachmentError("A disabled check cannot be matched to the selected revision. Review its automatic checks before changing revisions.")
        new_ids = {item.attachment_id for item in selections}
        new_bindings = {item.validator_binding_id for item in selections}
        for edge in refreshed.edges:
            if edge.source != node.id or edge.role != VALIDATION_ATTACHMENT_EDGE_ROLE:
                continue
            if ((edge.replaces_attachment_id and edge.replaces_attachment_id not in new_ids)
                    or (edge.satisfies_binding_id and edge.satisfies_binding_id not in new_bindings)):
                raise FlowValidationAttachmentError("This revision changes a check used by an attached validator step. Remove that attachment edge and its unused validator step, then select the revision and use its automatic checks.")
        node.data.validation_attachments = selections
        node.data.validation_groups = []
    return refreshed


def apply_flow_validation_attachment_defaults(
    flow_definition: FlowDefinition,
    *,
    agent_registry: Mapping[str, Mapping[str, Any]] | None = None,
    entries_by_node: Mapping[str, Mapping[str, Any] | None] | None = None,
    db: Session | None = None,
) -> FlowDefinition:
    """Attach default validation selections to extraction nodes from metadata."""

    try:
        validate_persisted_flow_definition(flow_definition.model_dump())
    except PersistedFlowMigrationError as exc:
        raise FlowValidationAttachmentError(str(exc)) from exc

    if any(edge.role == VALIDATION_ATTACHMENT_EDGE_ROLE for edge in flow_definition.edges):
        raise FlowValidationAttachmentError(
            "Replacement and supplemental validators are not supported in flows. "
            "Remove the validator attachment connection and use the extractor's automatic checks."
        )
    from src.lib.domain_packs.flow_validator_selection import effective_flow_validation_groups
    try:
        for node in flow_definition.nodes:
            effective_flow_validation_groups([group.model_dump() for group in node.data.validation_groups])
    except ValueError as exc:
        raise FlowValidationAttachmentError(str(exc)) from exc

    hydrated = flow_definition.model_copy(deep=True)
    selections_by_node_id: dict[str, list[FlowValidationAttachmentSelection]] = {}

    for node in hydrated.nodes:
        node.data.validation_groups = []
        if node.type != "agent":
            if node.data.validation_attachments:
                raise FlowValidationAttachmentError(
                    "validation_attachments are only allowed on agent nodes"
                )
            continue

        if entries_by_node is not None and node.id in entries_by_node:
            entry = entries_by_node[node.id]
            options = _options_for_agent_entry(entry, db=db) if entry is not None else ()
        else:
            options = validation_attachment_options_for_agent(
                node.data.agent_id,
                agent_registry=agent_registry,
            )
        if not options:
            if node.data.validation_attachments:
                raise FlowValidationAttachmentError(
                    f"Agent '{node.data.agent_id}' does not declare validation attachments"
                )
            continue

        option_ids = {option.attachment_id for option in options}
        existing_by_id = {
            selection.attachment_id: selection
            for selection in node.data.validation_attachments
        }
        unknown_ids = sorted(set(existing_by_id) - option_ids)
        if unknown_ids:
            raise FlowValidationAttachmentError(
                "Unknown validation attachment selections for "
                f"agent '{node.data.agent_id}': {', '.join(unknown_ids)}"
            )

        selections: list[FlowValidationAttachmentSelection] = []
        for option in options:
            existing = existing_by_id.get(option.attachment_id)
            payload = option.to_dict()
            payload["enabled"] = (
                existing.enabled if existing is not None else option.default_enabled
            )
            selections.append(FlowValidationAttachmentSelection(**payload))

        node.data.validation_attachments = selections
        selections_by_node_id[node.id] = selections

    for node in hydrated.nodes:
        if node.id in selections_by_node_id:
            node.data.validation_groups = _resolved_validation_groups(selections_by_node_id[node.id])

    return hydrated


def validation_schedule_from_node_data(
    node_data: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Describe saved choices, including unsupported historical overrides.

    This is read-only inspection, not execution authorization. Shared flow
    authoring/preflight rejects replacement and supplemental attachments.
    """

    attachments = node_data.get("validation_attachments") or []
    groups = node_data.get("validation_groups") or []
    groups_by_attachment_id: dict[str, dict[str, Any]] = {}
    supplemental_groups: list[dict[str, Any]] = []
    for raw_group in groups:
        group = _plain_group(raw_group)
        attachment_id = group.get("attachment_id")
        if attachment_id:
            groups_by_attachment_id[str(attachment_id)] = group
        elif group.get("state") == "supplemental":
            supplemental_groups.append(group)

    scheduled: list[dict[str, Any]] = []
    opt_outs: list[dict[str, Any]] = []
    inactive_metadata: list[dict[str, Any]] = []
    replacement_validators: list[dict[str, Any]] = []
    supplemental_validators: list[dict[str, Any]] = []

    for raw_attachment in attachments:
        attachment = _plain_attachment(raw_attachment)
        group = groups_by_attachment_id.get(str(attachment.get("attachment_id") or ""))
        state = attachment.get("state")
        enabled = bool(attachment.get("enabled"))
        has_binding = bool(attachment.get("validator_binding_id"))

        if group is not None and group.get("state") == "replaced":
            replacement_validators.append(
                {
                    **_schedule_entry(attachment),
                    **_group_schedule_entry(group),
                }
            )
            continue

        if state == "active" and enabled and has_binding:
            scheduled.append(_schedule_entry(attachment))
            continue

        if state == "active" and has_binding:
            opt_outs.append(
                {
                    **_schedule_entry(attachment),
                    "skipped_by_flow_configuration": True,
                }
            )
            continue

        inactive_metadata.append(_schedule_entry(attachment))

    for group in supplemental_groups:
        supplemental_validators.append(_group_schedule_entry(group))

    return {
        "scheduled_validators": scheduled,
        "opt_outs": opt_outs,
        "inactive_metadata": inactive_metadata,
        "replacement_validators": replacement_validators,
        "supplemental_validators": supplemental_validators,
    }


def _options_for_agent_entry(
    entry: Mapping[str, Any],
    *, db: Session | None = None,
) -> tuple[ValidationAttachmentOption, ...]:
    curation = entry.get("curation")
    if not isinstance(curation, Mapping):
        return ()
    domain_pack_id = curation.get("domain_pack_id")
    if not isinstance(domain_pack_id, str) or not domain_pack_id.strip():
        return ()

    registry = _domain_pack_validation_registries().get(domain_pack_id.strip())
    if registry is None:
        raise FlowValidationAttachmentError(
            f"Agent declares unknown domain_pack_id '{domain_pack_id}'"
        )
    if entry.get("execution_receipt") is not None:
        from src.schemas.agent_execution_revision import AgentExecutionReceipt
        from src.lib.domain_packs.profile_validation import (
            profile_validation_attachment_options, resolve_profile_validation,
        )
        context = resolve_profile_validation(
            AgentExecutionReceipt.model_validate(entry["execution_receipt"]), registry.domain_pack,
            db=db,
            active_group_ids=entry.get("authenticated_group_ids") or (), user_id=entry.get("authenticated_user_id"),
        )
        if context is not None:
            return profile_validation_attachment_options(context)
    return registry.validation_attachment_options()


def _load_package_domain_pack_registry():
    from src.lib.config.package_default_sources import resolve_packages_dir
    from src.lib.config.agent_loader import build_package_scoped_agent_resolver
    from src.lib.config.schema_discovery import build_package_scoped_output_schema_resolver
    from src.lib.domain_packs.registry import load_package_domain_pack_registry
    from src.lib.packages import load_package_registry

    packages_dir = resolve_packages_dir(None)
    runtime_package_registry = load_package_registry(packages_dir)
    registry = load_package_domain_pack_registry(runtime_package_registry)
    validation_registries = [
        DomainPackValidationRegistry.from_domain_pack(domain_pack)
        for domain_pack in registry.loaded_packs
    ]
    validate_active_validator_agent_references(
        validation_registries,
        runtime_package_registry,
        agent_resolver=build_package_scoped_agent_resolver(packages_dir),
        output_schema_resolver=build_package_scoped_output_schema_resolver(packages_dir),
    )
    return registry


def _plain_attachment(raw_attachment: Any) -> dict[str, Any]:
    if hasattr(raw_attachment, "model_dump"):
        return raw_attachment.model_dump()
    if isinstance(raw_attachment, Mapping):
        return dict(raw_attachment)
    raise FlowValidationAttachmentError(
        f"Unexpected validation attachment type: {type(raw_attachment).__name__}"
    )


def _plain_group(raw_group: Any) -> dict[str, Any]:
    if hasattr(raw_group, "model_dump"):
        return raw_group.model_dump()
    if isinstance(raw_group, Mapping):
        return dict(raw_group)
    raise FlowValidationAttachmentError(
        f"Unexpected validation group type: {type(raw_group).__name__}"
    )


def _resolved_validation_groups(
    selections: list[FlowValidationAttachmentSelection],
) -> list[FlowValidationAttachmentGroup]:
    """Resolve the existing automatic checks and explicit opt-outs."""
    return [FlowValidationAttachmentGroup(
        group_id=selection.attachment_id,
        state="automatic" if selection.enabled else "skipped",
        binding_id=selection.validator_binding_id,
        attachment_id=selection.attachment_id,
        edge_id=None, validator_node_id=None, replaces_attachment_id=None,
        label=selection.label,
        required=selection.required,
        blocking=selection.blocking,
        allow_opt_out=selection.allow_opt_out,
    ) for selection in selections
       if selection.state == "active" and selection.validator_binding_id]


def _schedule_entry(attachment: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "attachment_id",
        "domain_pack_id",
        "domain_pack_version",
        "validator_id",
        "validator_binding_id",
        "validation_kind",
        "tool_name",
        "tool_method",
        "validator_package_id",
        "validator_agent_id",
        "state",
        "scope",
        "object_type",
        "object_role",
        "field_path",
        "field_type",
        "required",
        "blocking",
        "export_blocking",
        "allow_opt_out",
        "blocked_by",
        "reason",
        "state_explanation",
        "affected_fields",
    )
    return {
        key: attachment[key]
        for key in keys
        if key in attachment and attachment[key] not in (None, "")
    }


def _group_schedule_entry(group: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "group_id",
        "state",
        "binding_id",
        "attachment_id",
        "edge_id",
        "validator_node_id",
        "replaces_attachment_id",
        "required",
        "blocking",
        "allow_opt_out",
    )
    entry = {
        key: group[key]
        for key in keys
        if key in group and group[key] not in (None, "")
    }
    if "binding_id" in entry:
        entry["validator_binding_id"] = entry["binding_id"]
    return entry


__all__ = [
    "FlowValidationAttachmentError",
    "apply_flow_validation_attachment_defaults",
    "domain_pack_validation_registries",
    "validation_attachment_catalog_by_agent",
    "validation_attachment_options_for_agent",
    "validation_schedule_from_node_data",
]
