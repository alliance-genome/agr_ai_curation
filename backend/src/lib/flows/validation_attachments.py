"""Flow validation attachment policy derived from domain-pack metadata."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Mapping

from src.lib.domain_packs.registry import load_domain_pack_registry
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationAttachmentOption,
)
from src.schemas.flows import FlowDefinition, FlowValidationAttachmentSelection


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
    for node in hydrated.nodes:
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
            if existing is not None and existing.opt_out_reason:
                payload["opt_out_reason"] = existing.opt_out_reason
            selections.append(FlowValidationAttachmentSelection(**payload))

        node.data.validation_attachments = selections

    return hydrated


def validation_schedule_from_node_data(
    node_data: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Build the supervisor-facing validation schedule for one extraction node."""

    attachments = node_data.get("validation_attachments") or []
    scheduled: list[dict[str, Any]] = []
    opt_outs: list[dict[str, Any]] = []
    inactive_metadata: list[dict[str, Any]] = []

    for raw_attachment in attachments:
        attachment = _plain_attachment(raw_attachment)
        state = attachment.get("state")
        enabled = bool(attachment.get("enabled"))
        has_binding = bool(attachment.get("validator_binding_id"))

        if state == "active" and enabled and has_binding:
            scheduled.append(_schedule_entry(attachment))
            continue

        if state == "active" and has_binding:
            if attachment.get("required") or attachment.get("export_blocking"):
                opt_outs.append(_schedule_entry(attachment))
            continue

        inactive_metadata.append(_schedule_entry(attachment))

    return {
        "scheduled_validators": scheduled,
        "opt_outs": opt_outs,
        "inactive_metadata": inactive_metadata,
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
    from src.lib.domain_packs.registry import (
        DomainPackRegistry,
        load_domain_pack_registry,
    )

    packages_dir = resolve_packages_dir(None)
    loaded_packs = []
    failed_packs = []
    validation_errors = []

    if packages_dir.exists():
        for package_dir in sorted(packages_dir.iterdir(), key=lambda path: path.name):
            domain_packs_dir = package_dir / "domain_packs"
            if not domain_packs_dir.is_dir():
                continue
            package_registry = load_domain_pack_registry(
                domain_packs_dir,
                fail_on_validation_error=False,
            )
            loaded_packs.extend(package_registry.loaded_packs)
            failed_packs.extend(package_registry.failed_packs)
            validation_errors.extend(package_registry.validation_errors)

    registry = DomainPackRegistry(
        packs_dir=packages_dir,
        loaded_packs=tuple(sorted(loaded_packs, key=lambda pack: pack.pack_id)),
        failed_packs=tuple(sorted(failed_packs, key=lambda item: item.pack_id)),
        validation_errors=tuple(validation_errors),
    )
    registry.raise_for_validation_errors()
    return registry


def _plain_attachment(raw_attachment: Any) -> dict[str, Any]:
    if hasattr(raw_attachment, "model_dump"):
        return raw_attachment.model_dump()
    if isinstance(raw_attachment, Mapping):
        return dict(raw_attachment)
    raise FlowValidationAttachmentError(
        f"Unexpected validation attachment type: {type(raw_attachment).__name__}"
    )


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
        "state",
        "scope",
        "object_type",
        "object_role",
        "field_path",
        "field_type",
        "required",
        "export_blocking",
        "allow_opt_out",
        "opt_out_reason_required",
        "opt_out_reason",
        "blocked_by",
        "reason",
    )
    return {
        key: attachment[key]
        for key in keys
        if key in attachment and attachment[key] not in (None, "")
    }


__all__ = [
    "FlowValidationAttachmentError",
    "apply_flow_validation_attachment_defaults",
    "validation_attachment_catalog_by_agent",
    "validation_attachment_options_for_agent",
    "validation_schedule_from_node_data",
]
