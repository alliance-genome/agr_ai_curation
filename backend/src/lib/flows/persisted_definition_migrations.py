"""Versioned, forward-only migrations for persisted flow definitions."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


RETIRED_ALLELE_VALIDATION_MIGRATION = (
    "2026-09-03-retire-allele-pending-envelope-validator"
)
_ALLELE_EXTRACTOR_AGENT_ID = "allele_extractor"
_ALLELE_DOMAIN_PACK_ID = "agr.alliance.allele"
_RETIRED_VALIDATOR_ID = "allele_pending_envelope_validator"
_RETIRED_BINDING_ATTACHMENT_IDS = frozenset(
    {
        "agr.alliance.allele:binding:allele_pending_envelope_validator:object:Allele:*",
        "agr.alliance.allele:binding:allele_pending_envelope_validator:object:AlleleMention:*",
        "agr.alliance.allele:binding:allele_pending_envelope_validator:object:AllelePaperEvidenceAssociation:*",
        "agr.alliance.allele:binding:allele_pending_envelope_validator:object:EvidenceQuote:*",
        "agr.alliance.allele:binding:allele_pending_envelope_validator:object:Reference:*",
    }
)
_RETIRED_METADATA_ATTACHMENT_ID = (
    "agr.alliance.allele:metadata:allele_pending_envelope_validator:pack:*:*"
)


class PersistedFlowDefinitionMigrationError(ValueError):
    """Raised when a retired reference does not match its reviewed legacy shape."""


@dataclass(frozen=True)
class PersistedFlowDefinitionMigrationResult:
    """An in-memory migrated definition and curator-facing migration notices."""

    definition: dict[str, Any]
    applied_versions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def migrate_persisted_flow_definition(
    flow_definition: Mapping[str, Any],
) -> PersistedFlowDefinitionMigrationResult:
    """Apply reviewed migrations without weakening create/update validation."""

    migrated = deepcopy(dict(flow_definition))
    nodes = migrated.get("nodes")
    if not isinstance(nodes, list):
        return PersistedFlowDefinitionMigrationResult(definition=migrated)

    removed_count = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        if str(data.get("agent_id") or "") != _ALLELE_EXTRACTOR_AGENT_ID:
            _reject_unexpected_retired_reference(data)
            continue

        attachments = data.get("validation_attachments")
        if isinstance(attachments, list):
            kept_attachments = []
            for attachment in attachments:
                if _is_reviewed_retired_attachment(attachment):
                    removed_count += 1
                    continue
                _reject_unexpected_retired_reference(attachment)
                kept_attachments.append(attachment)
            data["validation_attachments"] = kept_attachments

        groups = data.get("validation_groups")
        if isinstance(groups, list):
            kept_groups = []
            for group in groups:
                if _is_reviewed_retired_group(group):
                    removed_count += 1
                    continue
                _reject_unexpected_retired_reference(group)
                kept_groups.append(group)
            data["validation_groups"] = kept_groups

    for edge in migrated.get("edges") or []:
        _reject_unexpected_retired_reference(edge)

    if not removed_count:
        return PersistedFlowDefinitionMigrationResult(definition=migrated)
    return PersistedFlowDefinitionMigrationResult(
        definition=migrated,
        applied_versions=(RETIRED_ALLELE_VALIDATION_MIGRATION,),
        warnings=(
            "Removed retired allele validation selections from this saved flow. "
            "Review the current validation attachments and save the flow to persist "
            "the repaired definition.",
        ),
    )


def _is_reviewed_retired_attachment(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    attachment_id = str(value.get("attachment_id") or "")
    domain_pack_id = str(value.get("domain_pack_id") or "")
    binding_id = str(value.get("validator_binding_id") or "")
    validator_id = str(value.get("validator_id") or "")
    if domain_pack_id != _ALLELE_DOMAIN_PACK_ID:
        return False
    if attachment_id in _RETIRED_BINDING_ATTACHMENT_IDS:
        return binding_id == _RETIRED_VALIDATOR_ID
    if attachment_id == _RETIRED_METADATA_ATTACHMENT_ID:
        return not binding_id and validator_id == _RETIRED_VALIDATOR_ID
    return False


def _is_reviewed_retired_group(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    attachment_id = str(value.get("attachment_id") or "")
    group_id = str(value.get("group_id") or "")
    binding_id = str(
        value.get("binding_id") or value.get("validator_binding_id") or ""
    )
    binding_reference = (
        attachment_id in _RETIRED_BINDING_ATTACHMENT_IDS
        or group_id in _RETIRED_BINDING_ATTACHMENT_IDS
    )
    metadata_reference = (
        attachment_id == _RETIRED_METADATA_ATTACHMENT_ID
        or group_id == _RETIRED_METADATA_ATTACHMENT_ID
    )
    return (binding_reference and binding_id == _RETIRED_VALIDATOR_ID) or (
        metadata_reference and not binding_id
    )


def _reject_unexpected_retired_reference(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _reject_unexpected_retired_reference(item)
        return
    if not isinstance(value, Mapping):
        return
    identity_values = (
        value.get("attachment_id"),
        value.get("group_id"),
        value.get("binding_id"),
        value.get("validator_binding_id"),
        value.get("validator_id"),
        value.get("replaces_attachment_id"),
    )
    if any(_RETIRED_VALIDATOR_ID in str(identity or "") for identity in identity_values):
        raise PersistedFlowDefinitionMigrationError(
            "Retired allele validation reference has an unexpected persisted shape; "
            "the saved flow was not changed and requires manual review."
        )
    for nested in value.values():
        if isinstance(nested, (Mapping, list)):
            _reject_unexpected_retired_reference(nested)


__all__ = [
    "PersistedFlowDefinitionMigrationError",
    "PersistedFlowDefinitionMigrationResult",
    "RETIRED_ALLELE_VALIDATION_MIGRATION",
    "migrate_persisted_flow_definition",
]
