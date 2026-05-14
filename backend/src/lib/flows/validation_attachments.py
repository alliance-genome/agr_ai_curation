"""Flow validation attachment policy derived from domain-pack metadata."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from src.lib.domain_packs.registry import load_domain_pack_registry
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationAttachmentOption,
    validate_active_validator_agent_references,
)
from src.schemas.flows import (
    VALIDATION_ATTACHMENT_EDGE_ROLE,
    FlowDefinition,
    FlowValidationAttachmentGroup,
    FlowValidationAttachmentSelection,
)


class FlowValidationAttachmentError(ValueError):
    """Raised when a flow validation attachment selection violates policy."""


@dataclass(frozen=True)
class _ValidationAttachmentEdgeGroup:
    edge_id: str
    source_node_id: str
    validator_node_id: str
    binding_id: str
    replaces_attachment_id: str | None = None


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


def apply_flow_validation_attachment_defaults(
    flow_definition: FlowDefinition,
    *,
    agent_registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> FlowDefinition:
    """Attach default validation selections to extraction nodes from metadata."""

    hydrated = flow_definition.model_copy(deep=True)
    options_by_node_id: dict[str, tuple[ValidationAttachmentOption, ...]] = {}
    selections_by_node_id: dict[str, list[FlowValidationAttachmentSelection]] = {}

    for node in hydrated.nodes:
        node.data.validation_groups = []
        if node.type != "agent":
            if node.data.validation_attachments:
                raise FlowValidationAttachmentError(
                    "validation_attachments are only allowed on agent nodes"
                )
            continue

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

        options_by_node_id[node.id] = options
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

    sidecar_groups_by_source = _validation_attachment_edge_groups_by_source(
        hydrated,
        selections_by_node_id=selections_by_node_id,
        options_by_node_id=options_by_node_id,
    )

    for node in hydrated.nodes:
        if node.id not in selections_by_node_id:
            continue
        node.data.validation_groups = _resolved_validation_groups(
            selections_by_node_id[node.id],
            sidecar_groups_by_source.get(node.id, ()),
        )

    return hydrated


def validation_schedule_from_node_data(
    node_data: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Build the supervisor-facing validation schedule for one extraction node."""

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


def _validation_attachment_edge_groups_by_source(
    flow_definition: FlowDefinition,
    *,
    selections_by_node_id: Mapping[str, list[FlowValidationAttachmentSelection]],
    options_by_node_id: Mapping[str, tuple[ValidationAttachmentOption, ...]],
) -> dict[str, tuple[_ValidationAttachmentEdgeGroup, ...]]:
    """Validate sidecar validator edges and resolve them by extraction node."""

    node_by_id = {node.id: node for node in flow_definition.nodes}
    groups_by_source: dict[str, list[_ValidationAttachmentEdgeGroup]] = {}
    seen_binding_ids_by_source: dict[str, set[str]] = {}

    for edge in flow_definition.edges:
        if edge.role != VALIDATION_ATTACHMENT_EDGE_ROLE:
            continue

        source_node = node_by_id.get(edge.source)
        target_node = node_by_id.get(edge.target)
        if source_node is None or target_node is None:
            continue
        if source_node.type != "agent" or source_node.id not in options_by_node_id:
            raise FlowValidationAttachmentError(
                "validation_attachment edges must originate directly from "
                "an extraction agent node with validation attachment metadata"
            )
        if target_node.type != "agent" or target_node.id == source_node.id:
            raise FlowValidationAttachmentError(
                "validation_attachment edges must target a distinct validator agent node"
            )

        selections_by_attachment_id = {
            selection.attachment_id: selection
            for selection in selections_by_node_id[source_node.id]
        }
        binding_id = edge.satisfies_binding_id
        replaces_attachment_id = edge.replaces_attachment_id
        if replaces_attachment_id:
            replaced_selection = selections_by_attachment_id.get(replaces_attachment_id)
            if replaced_selection is None:
                raise FlowValidationAttachmentError(
                    "validation_attachment edge references unknown "
                    f"replaces_attachment_id '{replaces_attachment_id}'"
                )
            if replaced_selection.validator_binding_id is None:
                raise FlowValidationAttachmentError(
                    "validation_attachment edge cannot replace an attachment "
                    "without a validator_binding_id"
                )
            binding_id = replaced_selection.validator_binding_id

        if not binding_id:
            raise FlowValidationAttachmentError(
                "validation_attachment edges must name a validator binding"
            )

        seen_binding_ids = seen_binding_ids_by_source.setdefault(source_node.id, set())
        if binding_id in seen_binding_ids:
            raise FlowValidationAttachmentError(
                "validation_attachment edges from one extraction node must "
                f"name distinct validator bindings; duplicate '{binding_id}'"
            )
        seen_binding_ids.add(binding_id)

        groups_by_source.setdefault(source_node.id, []).append(
            _ValidationAttachmentEdgeGroup(
                edge_id=edge.id,
                source_node_id=source_node.id,
                validator_node_id=target_node.id,
                binding_id=binding_id,
                replaces_attachment_id=replaces_attachment_id,
            )
        )

    return {
        source_node_id: tuple(groups)
        for source_node_id, groups in groups_by_source.items()
    }


def _resolved_validation_groups(
    selections: list[FlowValidationAttachmentSelection],
    sidecar_groups: tuple[_ValidationAttachmentEdgeGroup, ...],
) -> list[FlowValidationAttachmentGroup]:
    """Return automatic, skipped, replaced, and supplemental validator groups."""

    sidecar_by_binding_id = {
        group.binding_id: group
        for group in sidecar_groups
    }
    selection_binding_ids = {
        selection.validator_binding_id
        for selection in selections
        if selection.validator_binding_id
    }
    groups: list[FlowValidationAttachmentGroup] = []

    for selection in selections:
        sidecar_group = (
            sidecar_by_binding_id.get(selection.validator_binding_id)
            if selection.validator_binding_id
            else None
        )
        if sidecar_group is not None and not selection.enabled:
            raise FlowValidationAttachmentError(
                "validation attachment binding "
                f"'{sidecar_group.binding_id}' cannot be both disabled and replaced"
            )

        if sidecar_group is not None:
            state = "replaced"
        elif selection.state == "active" and selection.enabled:
            state = "automatic"
        else:
            state = "skipped"

        groups.append(
            FlowValidationAttachmentGroup(
                **_validation_group_payload(
                    group_id=selection.attachment_id,
                    state=state,
                    binding_id=selection.validator_binding_id,
                    attachment_id=selection.attachment_id,
                    label=selection.label,
                    required=selection.required,
                    blocking=selection.blocking,
                    allow_opt_out=selection.allow_opt_out,
                    sidecar_group=sidecar_group,
                )
            )
        )

    for sidecar_group in sidecar_groups:
        if sidecar_group.binding_id in selection_binding_ids:
            continue
        groups.append(
            FlowValidationAttachmentGroup(
                **_validation_group_payload(
                    group_id=f"edge:{sidecar_group.edge_id}",
                    state="supplemental",
                    binding_id=sidecar_group.binding_id,
                    attachment_id=None,
                    label=None,
                    required=False,
                    blocking=False,
                    allow_opt_out=False,
                    sidecar_group=sidecar_group,
                )
            )
        )

    return groups


def _validation_group_payload(
    *,
    group_id: str,
    state: str,
    binding_id: str | None,
    attachment_id: str | None,
    label: str | None,
    required: bool,
    blocking: bool,
    allow_opt_out: bool,
    sidecar_group: _ValidationAttachmentEdgeGroup | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "group_id": group_id,
        "state": state,
        "binding_id": binding_id,
        "attachment_id": attachment_id,
        "label": label,
        "required": required,
        "blocking": blocking,
        "allow_opt_out": allow_opt_out,
    }
    if sidecar_group is not None:
        payload.update(
            {
                "edge_id": sidecar_group.edge_id,
                "validator_node_id": sidecar_group.validator_node_id,
                "replaces_attachment_id": sidecar_group.replaces_attachment_id,
            }
        )
    return {
        key: value
        for key, value in payload.items()
        if value not in (None, "")
    }


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
