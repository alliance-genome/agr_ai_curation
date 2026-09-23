"""Gene-expression stage slims stored in the previous format (read time only).

Before ALL-1283 the stage UBERON slim terms were stored as ontology terms
(``{curie: "UBERON:0000068", name: "embryo stage"}``, or a bare CURIE string).
LinkML gives the slot the range VocabularyTerm, and the curation DB stores
them as terms of the Stage Uberon Slim Terms vocabulary, named by their CURIE
(or "post embryonic, pre-adult"). Such records are never rewritten. For
display, each previous-format slim reads as a vocabulary term through the
shared legacy rule, so it shows its stored text as "(legacy, unverified)"
paper wording. The record is not validated again: re-running extraction
produces a record in the current format.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.lib.domain_packs.materialization import DomainPackMetadataReviewRowMaterializer
from src.lib.domain_packs.not_validatable import NOT_VALIDATABLE_DETAIL_KEY
from src.lib.domain_packs.resolvable_values import (
    RESOLUTION_STATE_KEY,
    ResolvableSpec,
    effective_value,
)
from src.schemas.curation_workspace import DomainEnvelopeReviewRow
from src.schemas.domain_envelope import (
    CuratableObjectEnvelope,
    DomainEnvelope,
    ValidationFinding,
    ValidationFindingSeverity,
    ValidationFindingStatus,
)

from .constants import GENE_EXPRESSION_OBJECT_TYPE


PREVIOUS_FORMAT_FINDING_CODE = "alliance.gene_expression.previous_stage_slim_format"
PREVIOUS_FORMAT_MESSAGE = (
    "Recorded in the previous stage-slim format; re-run extraction to validate."
)
STAGE_SLIM_VOCABULARY = "Stage Uberon Slim Terms"
_STAGE_SLIM_PATH = ("expression_pattern", "when_expressed", "stage_uberon_slim_terms")
_VOCABULARY_SPEC = ResolvableSpec(label_key="name")


def _stage_slims(payload: Mapping[str, Any]) -> Any:
    node: Any = payload
    for key in _STAGE_SLIM_PATH:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def _is_previous_format_slim(value: Any) -> bool:
    """A bare string or an ontology-shaped term (a ``curie`` key) outside the contract."""

    if isinstance(value, str):
        return True
    return isinstance(value, Mapping) and "curie" in value and RESOLUTION_STATE_KEY not in value


def has_previous_format_stage_slims(payload: Mapping[str, Any]) -> bool:
    """Whether a stored annotation keeps any stage slim in the previous (ontology) format."""

    slims = _stage_slims(payload)
    return isinstance(slims, list) and any(_is_previous_format_slim(item) for item in slims)


def _legacy_slim(value: Any) -> Any:
    """A previous-format slim as a vocabulary term, read through the shared legacy rule."""

    if not _is_previous_format_slim(value):
        return value
    # The vocabulary names its UBERON terms by their CURIE.
    name = value if isinstance(value, str) else value.get("curie") or value.get("name")
    stored = {"name": name, "vocabulary": STAGE_SLIM_VOCABULARY}
    return dict(effective_value(stored, _VOCABULARY_SPEC, covered_by_validator=False))


def previous_format_display_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """A read-time copy with each previous-format stage slim in the vocabulary-term shape.

    Each such slim reads as unresolved, ``legacy_unverified``, with its stored
    text as "(legacy, unverified)" paper wording. The stored record is not changed.
    """

    display = copy.deepcopy(dict(payload))
    slims = _stage_slims(display)
    if isinstance(slims, list):
        display["expression_pattern"]["when_expressed"]["stage_uberon_slim_terms"] = [
            _legacy_slim(item) for item in slims
        ]
    return display


def is_previous_format_annotation(domain_object: CuratableObjectEnvelope) -> bool:
    return domain_object.object_type == GENE_EXPRESSION_OBJECT_TYPE and has_previous_format_stage_slims(
        domain_object.payload
    )


def previous_format_finding(domain_object: CuratableObjectEnvelope) -> ValidationFinding:
    """The one curator-facing finding for an annotation with previous-format stage slims."""

    return ValidationFinding(
        severity=ValidationFindingSeverity.BLOCKER,
        status=ValidationFindingStatus.OPEN,
        code=PREVIOUS_FORMAT_FINDING_CODE,
        message=PREVIOUS_FORMAT_MESSAGE,
        object_ref=domain_object.to_object_ref(),
        # Structural checks and validator dispatch skip the object, so this is its one finding.
        details={"previous_format": True, NOT_VALIDATABLE_DETAIL_KEY: True},
    )


def _display_envelope(envelope: DomainEnvelope) -> DomainEnvelope:
    objects = [
        obj.model_copy(update={"payload": previous_format_display_payload(obj.payload)})
        if is_previous_format_annotation(obj)
        else obj
        for obj in envelope.extracted_objects
    ]
    return envelope.model_copy(update={"extracted_objects": objects})


@dataclass(frozen=True)
class GeneExpressionReviewRowMaterializer(DomainPackMetadataReviewRowMaterializer):
    """Review rows for gene expression; previous-format stage slims read through the legacy rule."""

    def materialize(
        self,
        envelope: DomainEnvelope,
        *,
        envelope_revision: int,
    ) -> list[DomainEnvelopeReviewRow]:
        return super().materialize(
            _display_envelope(envelope), envelope_revision=envelope_revision
        )


__all__ = [
    "GeneExpressionReviewRowMaterializer",
    "PREVIOUS_FORMAT_FINDING_CODE",
    "PREVIOUS_FORMAT_MESSAGE",
    "STAGE_SLIM_VOCABULARY",
    "has_previous_format_stage_slims",
    "is_previous_format_annotation",
    "previous_format_display_payload",
    "previous_format_finding",
]
