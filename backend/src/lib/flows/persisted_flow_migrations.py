"""Generic forward-only migrations for stored flow definitions.

Packages declare exact retired catalog references. Core applies those
declarations without owning any organization-specific agent or attachment IDs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.lib.packages.persisted_flow_migration_loader import PersistedFlowMigration


class PersistedFlowMigrationError(ValueError):
    """Raised when a retired reference is present in an unsafe shape."""


@dataclass(frozen=True)
class PersistedFlowMigrationResult:
    """Result of applying known forward-only definition migrations."""

    definition: dict[str, Any]
    applied_migrations: tuple[str, ...] = ()
    removed_attachment_ids: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.applied_migrations)


def _retired_reference_in_validation_groups(
    nodes: list[Any],
    *,
    binding_id: str,
    attachment_ids: frozenset[str],
) -> bool:
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        data = node.get("data")
        if not isinstance(data, Mapping):
            continue
        groups = data.get("validation_groups")
        if not isinstance(groups, list):
            continue
        is_target_node = _node_has_retired_attachment(data, attachment_ids)
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            if is_target_node and group.get("binding_id") == binding_id:
                return True
            if is_target_node and group.get("validator_binding_id") == binding_id:
                return True
            if group.get("attachment_id") in attachment_ids:
                return True
            if group.get("replaces_attachment_id") in attachment_ids:
                return True
    return False


def _node_has_retired_attachment(
    data: Mapping[str, Any],
    attachment_ids: frozenset[str],
) -> bool:
    attachments = data.get("validation_attachments")
    if not isinstance(attachments, list):
        return False
    return any(
        isinstance(attachment, Mapping)
        and attachment.get("attachment_id") in attachment_ids
        for attachment in attachments
    )


def _retired_reference_in_edges(
    edges: Any,
    *,
    target_node_ids: frozenset[str],
    binding_id: str,
    attachment_ids: frozenset[str],
) -> bool:
    if not isinstance(edges, list):
        return False
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        if (
            edge.get("source") in target_node_ids
            and edge.get("satisfies_binding_id") == binding_id
        ):
            return True
        if edge.get("replaces_attachment_id") in attachment_ids:
            return True
    return False


def _validate_retired_attachment(
    attachment: Mapping[str, Any],
    *,
    expected_bindings: Mapping[str, str | None],
) -> None:
    attachment_id = str(attachment.get("attachment_id") or "")
    binding_id = attachment.get("validator_binding_id")
    expected_binding_id = expected_bindings[attachment_id]
    normalized_binding_id = str(binding_id).strip() if binding_id is not None else None
    if normalized_binding_id != expected_binding_id:
        raise PersistedFlowMigrationError(
            f"Retired validation attachment has an unexpected binding: {attachment_id}"
        )


def migrate_persisted_flow_definition(
    definition: Mapping[str, Any],
    *,
    migrations: Sequence[PersistedFlowMigration] | None = None,
) -> PersistedFlowMigrationResult:
    """Apply package-declared retired-selection repairs to a copied definition.

    Unknown selections and all current selections are preserved so create and
    update validation remains strict. Package metadata is cached by its loader.
    """

    if migrations is None:
        from src.lib.packages.persisted_flow_migration_loader import (
            load_persisted_flow_migration_catalog,
        )

        migrations = load_persisted_flow_migration_catalog().migrations

    migrated = deepcopy(dict(definition))
    nodes = migrated.get("nodes")
    if not isinstance(nodes, list):
        return PersistedFlowMigrationResult(definition=migrated)

    applied_migrations: list[str] = []
    removed_attachment_ids: list[str] = []
    for migration in migrations:
        expected_bindings = {
            attachment.attachment_id: attachment.validator_binding_id
            for attachment in migration.retired_attachments
        }
        attachment_ids = frozenset(expected_bindings)
        target_node_ids = frozenset(
            node_id
            for node in nodes
            if isinstance(node, Mapping)
            and isinstance(node.get("data"), Mapping)
            and _node_has_retired_attachment(node["data"], attachment_ids)
            and isinstance((node_id := node.get("id")), str)
        )
        if _retired_reference_in_validation_groups(
            nodes,
            binding_id=migration.retired_binding_id,
            attachment_ids=attachment_ids,
        ) or _retired_reference_in_edges(
            migrated.get("edges"),
            target_node_ids=target_node_ids,
            binding_id=migration.retired_binding_id,
            attachment_ids=attachment_ids,
        ):
            raise PersistedFlowMigrationError(
                f"Retired validation references from migration "
                f"'{migration.migration_id}' are used by a validation group or edge"
            )

        removed_for_migration: list[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            data = node.get("data")
            if not isinstance(data, dict):
                continue
            attachments = data.get("validation_attachments")
            if not isinstance(attachments, list):
                continue

            retained: list[Any] = []
            for attachment in attachments:
                attachment_id = (
                    str(attachment.get("attachment_id") or "")
                    if isinstance(attachment, Mapping)
                    else ""
                )
                if attachment_id not in attachment_ids:
                    retained.append(attachment)
                    continue
                _validate_retired_attachment(
                    attachment,
                    expected_bindings=expected_bindings,
                )
                removed_for_migration.append(attachment_id)
            if len(retained) != len(attachments):
                data["validation_attachments"] = retained

        if removed_for_migration:
            applied_migrations.append(migration.migration_id)
            removed_attachment_ids.extend(removed_for_migration)

    return PersistedFlowMigrationResult(
        definition=migrated,
        applied_migrations=tuple(applied_migrations),
        removed_attachment_ids=tuple(removed_attachment_ids),
    )


__all__ = [
    "PersistedFlowMigrationError",
    "PersistedFlowMigrationResult",
    "migrate_persisted_flow_definition",
]
