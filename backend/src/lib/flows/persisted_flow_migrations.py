"""Forward-only migrations for stored flow definitions.

These migrations repair persisted catalog references without weakening the
strict validation applied to newly created or edited flows.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


RETIRED_ALLELE_PENDING_VALIDATOR_MIGRATION = (
    "2026-09-03.remove-allele-pending-envelope-validator"
)
RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID = (
    "allele_pending_envelope_validator"
)
RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS = frozenset(
    {
        "agr.alliance.allele:binding:allele_pending_envelope_validator:object:Allele:*",
        "agr.alliance.allele:binding:allele_pending_envelope_validator:object:AlleleMention:*",
        "agr.alliance.allele:binding:allele_pending_envelope_validator:"
        "object:AllelePaperEvidenceAssociation:*",
        "agr.alliance.allele:binding:allele_pending_envelope_validator:object:EvidenceQuote:*",
        "agr.alliance.allele:binding:allele_pending_envelope_validator:object:Reference:*",
        "agr.alliance.allele:metadata:allele_pending_envelope_validator:pack:*:*",
    }
)
_RETIRED_METADATA_ATTACHMENT_ID = (
    "agr.alliance.allele:metadata:allele_pending_envelope_validator:pack:*:*"
)


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


def _retired_reference_in_validation_groups(nodes: list[Any]) -> bool:
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        data = node.get("data")
        if not isinstance(data, Mapping):
            continue
        groups = data.get("validation_groups")
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            if group.get("binding_id") == RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID:
                return True
            if (
                group.get("validator_binding_id")
                == RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID
            ):
                return True
            if group.get("attachment_id") in RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS:
                return True
            if group.get("replaces_attachment_id") in RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS:
                return True
    return False


def _retired_reference_in_edges(edges: Any) -> bool:
    if not isinstance(edges, list):
        return False
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        if edge.get("satisfies_binding_id") == RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID:
            return True
        if edge.get("replaces_attachment_id") in RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS:
            return True
    return False


def _validate_retired_attachment(attachment: Mapping[str, Any]) -> None:
    attachment_id = str(attachment.get("attachment_id") or "")
    binding_id = attachment.get("validator_binding_id")
    expected_binding_id = (
        None
        if attachment_id == _RETIRED_METADATA_ATTACHMENT_ID
        else RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID
    )
    normalized_binding_id = str(binding_id).strip() if binding_id is not None else None
    if normalized_binding_id != expected_binding_id:
        raise PersistedFlowMigrationError(
            "Retired allele validation attachment has an unexpected binding: "
            f"{attachment_id}"
        )


def migrate_persisted_flow_definition(
    definition: Mapping[str, Any],
) -> PersistedFlowMigrationResult:
    """Remove the exact retired allele validation selections from stored flows.

    The input is never mutated. Unknown selections and all current selections
    are preserved so create/update validation remains strict.
    """

    migrated = deepcopy(dict(definition))
    nodes = migrated.get("nodes")
    if not isinstance(nodes, list):
        return PersistedFlowMigrationResult(definition=migrated)

    if _retired_reference_in_validation_groups(nodes) or _retired_reference_in_edges(
        migrated.get("edges")
    ):
        raise PersistedFlowMigrationError(
            "Retired allele validation binding is referenced by a validation group or edge"
        )

    removed_attachment_ids: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if not isinstance(data, dict) or data.get("agent_id") != "allele_extractor":
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
            if attachment_id not in RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS:
                retained.append(attachment)
                continue
            _validate_retired_attachment(attachment)
            removed_attachment_ids.append(attachment_id)
        if len(retained) != len(attachments):
            data["validation_attachments"] = retained

    if not removed_attachment_ids:
        return PersistedFlowMigrationResult(definition=migrated)

    return PersistedFlowMigrationResult(
        definition=migrated,
        applied_migrations=(RETIRED_ALLELE_PENDING_VALIDATOR_MIGRATION,),
        removed_attachment_ids=tuple(removed_attachment_ids),
    )


__all__ = [
    "PersistedFlowMigrationError",
    "PersistedFlowMigrationResult",
    "RETIRED_ALLELE_PENDING_VALIDATOR_ATTACHMENT_IDS",
    "RETIRED_ALLELE_PENDING_VALIDATOR_BINDING_ID",
    "RETIRED_ALLELE_PENDING_VALIDATOR_MIGRATION",
    "migrate_persisted_flow_definition",
]
