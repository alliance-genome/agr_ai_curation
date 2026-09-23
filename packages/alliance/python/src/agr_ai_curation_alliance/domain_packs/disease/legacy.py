"""Disease records stored in the previous format, before ALL-1283 (read time only).

The previous format kept the disease relation, genetic sex and annotation type
as flat ``<slot>_name`` / ``<slot>_vocabulary`` / ``<slot>_id`` strings and the
evidence codes, qualifiers and with/from genes as lists of strings. Such
records are never rewritten. For display, those strings are read as the
current value objects through the shared legacy rule, so they show their stored
text as "(legacy, unverified)" paper wording; the disease term, subject and data
provider were already objects and read through the same rule wherever values
are displayed. They are not validated again: re-running extraction produces a
record in the current format.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
from src.lib.domain_packs.not_validatable import NOT_VALIDATABLE_DETAIL_KEY
from src.lib.domain_packs.resolvable_values import MENTION_KEY, ResolvableSpec, effective_value
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
_VOCABULARY_SPEC = ResolvableSpec(label_key="name")
# Lists that held plain strings in the previous format, with each element's identity key.
_PREVIOUS_STRING_LISTS = {
    "evidence_code_curies": ResolvableSpec(id_key="curie"),
    "disease_qualifier_names": ResolvableSpec(label_key="name"),
    "with_gene_identifiers": ResolvableSpec(id_key="primary_external_id"),
}
# Values that were already objects; the previous format stored them without paper wording.
_PREVIOUS_OBJECT_VALUES = ("disease_annotation_object", "disease_annotation_subject", "data_provider")


def is_previous_format(payload: Mapping[str, Any]) -> bool:
    """Whether a stored disease annotation payload predates the resolvable-value format."""

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


def _legacy(spec: ResolvableSpec, **stored: Any) -> dict[str, Any]:
    """Stored strings as a value object, read through the shared legacy rule."""

    value = {key: item for key, item in stored.items() if item is not None}
    return dict(effective_value(value, spec, covered_by_validator=False))


def previous_format_display_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A read-time copy of a previous-format payload in the current value shape.

    The flat relation, genetic sex and annotation type strings and the string
    list elements become value objects that read as unresolved,
    ``legacy_unverified``, with their stored text as "(legacy, unverified)"
    paper wording. The stored record is not changed.
    """

    display = copy.deepcopy(dict(payload))
    for slot in _PREVIOUS_VOCABULARY_SLOTS:
        stored = {key: display.pop(f"{slot}_{key}", None) for key in _VOCABULARY_KEYS}
        if stored["name"] is not None:
            display[slot] = _legacy(_VOCABULARY_SPEC, **stored)
    for list_key, spec in _PREVIOUS_STRING_LISTS.items():
        values = display.get(list_key)
        if isinstance(values, list):
            display[list_key] = [
                _legacy(spec, **{spec.identity_keys[0]: item}) if isinstance(item, str) else item
                for item in values
            ]
    return display


def _display_envelope(envelope: DomainEnvelope) -> DomainEnvelope:
    objects = [
        obj.model_copy(update={"payload": previous_format_display_payload(obj.payload)})
        if obj.object_type in _ANNOTATION_OBJECT_TYPES and is_previous_format(obj.payload)
        else obj
        for obj in envelope.extracted_objects
    ]
    return envelope.model_copy(update={"extracted_objects": objects})


@dataclass(frozen=True)
class DiseaseReviewRowMaterializer(DomainPackMetadataReviewRowMaterializer):
    """Review rows for disease; previous-format records read through the legacy rule."""

    def materialize(
        self,
        envelope: DomainEnvelope,
        *,
        envelope_revision: int,
    ) -> list[DomainEnvelopeReviewRow]:
        return super().materialize(
            _display_envelope(envelope), envelope_revision=envelope_revision
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
    "previous_format_display_payload",
    "validate_disease_envelope",
]
