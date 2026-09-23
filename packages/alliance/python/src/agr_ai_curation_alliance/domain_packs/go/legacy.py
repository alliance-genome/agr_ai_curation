"""GO records stored in the previous format, before ALL-1283 (read time only).

The previous format kept the evidence code, its ECO CURIE, the reference and
each With/From entry as plain strings, and the gene-product resolution state at
the payload root. Such records are never rewritten. For display those strings
are read as the current value objects through the shared legacy rule, so they
show their stored text as "(legacy, unverified)" paper wording; the gene product
and GO term were already objects and read through the same rule wherever values
are displayed. They are not validated again: re-running extraction produces a
record in the current format.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
from src.lib.domain_packs.resolvable_values import ResolvableSpec, effective_value
from src.schemas.domain_envelope import (
    DomainEnvelope,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)
from src.schemas.curation_workspace import DomainEnvelopeReviewRow

from .constants import GO_DOMAIN_PACK_ID, GO_OBJECT_TYPE


PREVIOUS_FORMAT_FINDING_CODE = "agr.alliance.go.previous_format"
PREVIOUS_FORMAT_MESSAGE = "Recorded in the previous GO format; re-run extraction to validate."


def is_previous_format(payload: Mapping[str, Any]) -> bool:
    """Whether a stored GO payload predates the resolvable-value format."""

    with_from = payload.get("with_from")
    return (
        isinstance(payload.get("evidence_code"), str)
        or "evidence_eco_curie" in payload
        or isinstance(payload.get("reference_curie"), str)
        or "resolution_state" in payload
        or (isinstance(with_from, list) and any(isinstance(item, str) for item in with_from))
    )


# The display roles the pack declares for each value the previous format stored as a string.
_EVIDENCE_CODE_SPEC = ResolvableSpec(id_key="eco_curie", label_key="code")
_CURIE_SPEC = ResolvableSpec(id_key="curie")


def _legacy(spec: ResolvableSpec, **stored: Any) -> dict[str, Any]:
    """A stored string as a value object, read through the shared legacy rule."""

    value = {key: item for key, item in stored.items() if item is not None}
    return dict(effective_value(value, spec, covered_by_validator=False))


def previous_format_display_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A read-time copy of a previous-format payload in the current value shape.

    The old evidence code (with its ECO CURIE), reference and With/From strings
    become value objects that read as unresolved, ``legacy_unverified``, with
    their stored text as "(legacy, unverified)" paper wording. The stored
    record is not changed.
    """

    display = copy.deepcopy(dict(payload))
    display.pop("resolution_state", None)
    evidence_code = display.get("evidence_code")
    eco_curie = display.pop("evidence_eco_curie", None)
    if isinstance(evidence_code, str):
        display["evidence_code"] = _legacy(_EVIDENCE_CODE_SPEC, code=evidence_code, eco_curie=eco_curie)
    reference = display.get("reference_curie")
    if isinstance(reference, str):
        display["reference_curie"] = _legacy(_CURIE_SPEC, curie=reference)
    with_from = display.get("with_from")
    if isinstance(with_from, list):
        display["with_from"] = [
            _legacy(_CURIE_SPEC, curie=item) if isinstance(item, str) else item
            for item in with_from
        ]
    return display


def _display_envelope(envelope: DomainEnvelope) -> DomainEnvelope:
    objects = [
        obj.model_copy(update={"payload": previous_format_display_payload(obj.payload)})
        if obj.object_type == GO_OBJECT_TYPE and is_previous_format(obj.payload)
        else obj
        for obj in envelope.extracted_objects
    ]
    return envelope.model_copy(update={"extracted_objects": objects})


@dataclass(frozen=True)
class GOReviewRowMaterializer(DomainPackMetadataReviewRowMaterializer):
    """Review rows for GO; previous-format records read through the legacy rule."""

    def materialize(
        self,
        envelope: DomainEnvelope,
        *,
        envelope_revision: int,
    ) -> list[DomainEnvelopeReviewRow]:
        return super().materialize(
            _display_envelope(envelope), envelope_revision=envelope_revision
        )


def validate_go_envelope(envelope: DomainEnvelope) -> tuple[ValidationFinding, ...]:
    """One curator-facing finding per GO record stored in the previous format."""

    if envelope.domain_pack_id != GO_DOMAIN_PACK_ID:
        return ()
    return tuple(
        ValidationFinding(
            severity=ValidationFindingSeverity.BLOCKER,
            status=ValidationFindingStatus.OPEN,
            code=PREVIOUS_FORMAT_FINDING_CODE,
            message=PREVIOUS_FORMAT_MESSAGE,
            object_ref=obj.to_object_ref(),
            details={"previous_format": True},
        )
        for obj in envelope.extracted_objects
        if obj.object_type == GO_OBJECT_TYPE and is_previous_format(obj.payload)
    )


__all__ = [
    "GOReviewRowMaterializer",
    "PREVIOUS_FORMAT_FINDING_CODE",
    "PREVIOUS_FORMAT_MESSAGE",
    "is_previous_format",
    "previous_format_display_payload",
    "validate_go_envelope",
]
