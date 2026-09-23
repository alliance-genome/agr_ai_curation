"""GO records stored in the previous format, before ALL-1283 (read time only).

The previous format kept the evidence code, its ECO CURIE, the reference, each
With/From entry and each qualifier as plain strings, and the gene-product
resolution state at the payload root. Such records are never rewritten. At
read time ``legacy_display_payload`` (the pack's registered
``legacy_display_mapper``) reshapes those strings into the current value
objects without any state; the shared legacy rule
(``resolvable_values.effective_payload``) then reads every value, so an
unverified one shows its stored text as "(legacy, unverified)" paper wording.
Exports apply that rule themselves; the GO review rows apply it here. The
records are not validated again: re-running extraction produces a record in the
current format.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
from src.lib.domain_packs.not_validatable import NOT_VALIDATABLE_DETAIL_KEY
from src.lib.domain_packs.resolvable_values import (
    declared_resolvable_fields,
    effective_payload,
    has_resolution_state,
)
from src.schemas.domain_envelope import (
    DomainEnvelope,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)
from src.schemas.curation_workspace import DomainEnvelopeReviewRow

from .constants import GO_DOMAIN_PACK_ID, GO_OBJECT_TYPE
from .values import RESOLVABLE_LIST_FIELDS, RESOLVABLE_VALUE_FIELDS


PREVIOUS_FORMAT_FINDING_CODE = "agr.alliance.go.previous_format"
PREVIOUS_FORMAT_MESSAGE = "Recorded in the previous GO format; re-run extraction to validate."

# Lists that held plain strings in the previous format, with each element's identity key.
_PREVIOUS_STRING_LISTS = {"with_from": "curie", "qualifiers": "name"}


def _holds_contract_state(payload: Mapping[str, Any]) -> bool:
    for key in (*RESOLVABLE_VALUE_FIELDS, *RESOLVABLE_LIST_FIELDS):
        value = payload.get(key)
        values = value if isinstance(value, list) else [value]
        if any(has_resolution_state(item) for item in values):
            return True
    return False


def is_previous_format(payload: Mapping[str, Any]) -> bool:
    """Whether a stored GO payload predates the resolvable-value format.

    Only a record in which no value carries the contract state is the previous
    format; a current record never is, whatever a curator later edits.
    """

    if _holds_contract_state(payload):
        return False
    return (
        isinstance(payload.get("evidence_code"), str)
        or "evidence_eco_curie" in payload
        or isinstance(payload.get("reference_curie"), str)
        or "resolution_state" in payload
        or any(
            isinstance(payload.get(key), list) and any(isinstance(item, str) for item in payload[key])
            for key in _PREVIOUS_STRING_LISTS
        )
    )


def previous_format_display_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A read-time copy of a previous-format payload in the current value shape.

    The evidence code (with its ECO CURIE), reference, With/From and qualifier
    strings become value objects holding their stored text under the value's
    own keys and no state, so the shared legacy rule reads them like any other
    value stored before the contract. The stored record is not changed.
    """

    display = copy.deepcopy(dict(payload))
    display.pop("resolution_state", None)
    eco_curie = display.pop("evidence_eco_curie", None)
    if isinstance(display.get("evidence_code"), str):
        display["evidence_code"] = {
            key: value
            for key, value in (("code", display["evidence_code"]), ("eco_curie", eco_curie))
            if value is not None
        }
    if isinstance(display.get("reference_curie"), str):
        display["reference_curie"] = {"curie": display["reference_curie"]}
    for list_key, identity_key in _PREVIOUS_STRING_LISTS.items():
        values = display.get(list_key)
        if isinstance(values, list):
            display[list_key] = [
                {identity_key: item} if isinstance(item, str) else item for item in values
            ]
    return display


def legacy_display_payload(object_type: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The pack's legacy display mapper: previous-format GO proposals in the current shape.

    Registered for the GO pack (``legacy_display_mapper``) so exports read
    previous-format records the way the review screen does; any other payload
    comes back unchanged.
    """

    if object_type == GO_OBJECT_TYPE and is_previous_format(payload):
        return previous_format_display_payload(payload)
    return payload


def _display_envelope(envelope: DomainEnvelope, metadata: Any) -> DomainEnvelope:
    objects = []
    for obj in envelope.extracted_objects:
        payload = legacy_display_payload(obj.object_type, obj.payload)
        specs = declared_resolvable_fields(metadata, obj.object_type)
        if specs:
            payload = effective_payload(payload, specs, object_metadata=obj.metadata)
        objects.append(obj.model_copy(update={"payload": dict(payload)}))
    return envelope.model_copy(update={"extracted_objects": objects})


@dataclass(frozen=True)
class GOReviewRowMaterializer(DomainPackMetadataReviewRowMaterializer):
    """Review rows for GO; every value reads through the shared legacy rule."""

    def materialize(
        self,
        envelope: DomainEnvelope,
        *,
        envelope_revision: int,
    ) -> list[DomainEnvelopeReviewRow]:
        return super().materialize(
            _display_envelope(envelope, self.metadata), envelope_revision=envelope_revision
        )


def validate_go_envelope(envelope: DomainEnvelope) -> tuple[ValidationFinding, ...]:
    """One curator-facing finding per GO record stored in the previous format.

    The finding marks the record not validatable, so it is the record's only finding.
    """

    if envelope.domain_pack_id != GO_DOMAIN_PACK_ID:
        return ()
    return tuple(
        ValidationFinding(
            severity=ValidationFindingSeverity.BLOCKER,
            status=ValidationFindingStatus.OPEN,
            code=PREVIOUS_FORMAT_FINDING_CODE,
            message=PREVIOUS_FORMAT_MESSAGE,
            object_ref=obj.to_object_ref(),
            # Structural checks and validator dispatch skip the record, so this is its one finding.
            details={"previous_format": True, NOT_VALIDATABLE_DETAIL_KEY: True},
        )
        for obj in envelope.extracted_objects
        if obj.object_type == GO_OBJECT_TYPE and is_previous_format(obj.payload)
    )


__all__ = [
    "GOReviewRowMaterializer",
    "legacy_display_payload",
    "PREVIOUS_FORMAT_FINDING_CODE",
    "PREVIOUS_FORMAT_MESSAGE",
    "is_previous_format",
    "previous_format_display_payload",
    "validate_go_envelope",
]
