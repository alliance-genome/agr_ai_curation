"""Disease records stored in the previous format, before ALL-1283 (read time only).

The previous format kept the disease relation, genetic sex and annotation type
as flat ``<slot>_name`` / ``<slot>_vocabulary`` / ``<slot>_id`` strings and the
evidence codes, qualifiers and with/from genes as lists of strings. Such
records are never rewritten. At read time ``legacy_display_payload`` (the
pack's registered ``legacy_display_mapper``) reshapes those strings into the
current value objects without any state; the shared legacy rule
(``resolvable_values.effective_payload``) then reads every value, so an
unverified one shows its stored text as "(legacy, unverified)" paper wording.
Exports apply that rule themselves; the disease review rows apply it here. The
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
    MENTION_KEY,
    declared_resolvable_fields,
    effective_payload,
    has_resolution_state,
)
from src.schemas.curation_workspace import DomainEnvelopeReviewRow
from src.schemas.domain_envelope import (
    DomainEnvelope,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)

from .constants import DISEASE_DOMAIN_PACK_ID, DISEASE_OBJECT_TYPE, DISEASE_SUBJECT_SUBTYPES


PREVIOUS_FORMAT_FINDING_CODE = "agr.alliance.disease.previous_format"
PREVIOUS_FORMAT_MESSAGE = "Recorded in the previous disease format; re-run extraction to validate."

_ANNOTATION_OBJECT_TYPES = frozenset(
    {DISEASE_OBJECT_TYPE, *(subtype[0] for subtype in DISEASE_SUBJECT_SUBTYPES.values())}
)
# Flat vocabulary slots of the previous format (<slot>_name/_vocabulary/_id); each is
# now the value object <slot>.
_PREVIOUS_VOCABULARY_SLOTS = ("disease_relation", "genetic_sex", "annotation_type")
_VOCABULARY_KEYS = ("name", "vocabulary", "id")
# Lists that held plain strings in the previous format.
_PREVIOUS_STRING_LISTS = ("evidence_code_curies", "disease_qualifier_names", "with_gene_identifiers")
# Values that were already objects; the previous format stored them without paper wording.
_PREVIOUS_OBJECT_VALUES = ("disease_annotation_object", "disease_annotation_subject", "data_provider")


# Every value a current-format annotation stores with the contract state.
_CURRENT_VALUE_KEYS = (
    *_PREVIOUS_OBJECT_VALUES,
    *_PREVIOUS_VOCABULARY_SLOTS,
    *_PREVIOUS_STRING_LISTS,
)


def _holds_contract_state(payload: Mapping[str, Any]) -> bool:
    for key in _CURRENT_VALUE_KEYS:
        value = payload.get(key)
        values = value if isinstance(value, list) else [value]
        if any(has_resolution_state(item) for item in values):
            return True
    return False


def is_previous_format(payload: Mapping[str, Any]) -> bool:
    """Whether a stored disease annotation payload predates the resolvable-value format.

    Only a record in which no value carries the contract state at all is the
    previous format; a current record (every one stores at least its
    annotation type with a state) never is, even after a curator edits one of
    its values.
    """

    if _holds_contract_state(payload):
        return False
    if any(
        f"{slot}_{key}" in payload for slot in _PREVIOUS_VOCABULARY_SLOTS for key in _VOCABULARY_KEYS
    ):
        return True
    for list_key in _PREVIOUS_STRING_LISTS:
        values = payload.get(list_key)
        if isinstance(values, list) and any(isinstance(item, str) for item in values):
            return True
    return any(
        isinstance(payload.get(key), Mapping) and MENTION_KEY not in payload[key]
        for key in _PREVIOUS_OBJECT_VALUES
    )


def previous_format_display_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A read-time copy of a previous-format payload in the current value shape.

    The flat relation, genetic sex and annotation type strings become the
    value objects that replaced them, holding their stored text under the
    value's own keys and no state, so the shared legacy rule reads them like
    any other value stored before the contract. The string list elements stay
    as stored: they sit at declared paths, where the shared legacy rule
    already reads a plain string. The stored record is not changed.
    """

    display = copy.deepcopy(dict(payload))
    for slot in _PREVIOUS_VOCABULARY_SLOTS:
        stored = {key: display.pop(f"{slot}_{key}", None) for key in _VOCABULARY_KEYS}
        if stored["name"] is not None:
            display[slot] = {key: value for key, value in stored.items() if value is not None}
    return display


def legacy_display_payload(object_type: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The pack's legacy display mapper: previous-format annotations in the current shape.

    Registered for the disease pack (``legacy_display_mapper``) so exports read
    previous-format records the way the review screen does; any other payload
    comes back unchanged.
    """

    if object_type in _ANNOTATION_OBJECT_TYPES and is_previous_format(payload):
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
class DiseaseReviewRowMaterializer(DomainPackMetadataReviewRowMaterializer):
    """Review rows for disease; every value reads through the shared legacy rule."""

    def materialize(
        self,
        envelope: DomainEnvelope,
        *,
        envelope_revision: int,
    ) -> list[DomainEnvelopeReviewRow]:
        return super().materialize(
            _display_envelope(envelope, self.metadata),
            envelope_revision=envelope_revision,
            stored_envelope=envelope,
        )


def validate_disease_envelope(envelope: DomainEnvelope) -> tuple[ValidationFinding, ...]:
    """One curator-facing finding per disease annotation stored in the previous format."""

    if envelope.domain_pack_id != DISEASE_DOMAIN_PACK_ID:
        return ()
    return tuple(
        ValidationFinding(
            severity=ValidationFindingSeverity.BLOCKER,
            status=ValidationFindingStatus.OPEN,
            code=PREVIOUS_FORMAT_FINDING_CODE,
            message=PREVIOUS_FORMAT_MESSAGE,
            object_ref=obj.to_object_ref(),
            # Structural checks and validator dispatch skip the object, so this is its one finding.
            details={"previous_format": True, NOT_VALIDATABLE_DETAIL_KEY: True},
        )
        for obj in envelope.extracted_objects
        if obj.object_type in _ANNOTATION_OBJECT_TYPES and is_previous_format(obj.payload)
    )


__all__ = [
    "DiseaseReviewRowMaterializer",
    "PREVIOUS_FORMAT_FINDING_CODE",
    "PREVIOUS_FORMAT_MESSAGE",
    "is_previous_format",
    "legacy_display_payload",
    "previous_format_display_payload",
    "validate_disease_envelope",
]
