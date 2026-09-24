"""Gene-expression resolvable values (ALL-1283).

Every validated gene-expression value is one object holding the paper's
wording (``mention``) beside the identity a validator supplied (extraction
never supplies one), plus the shared contract state from
``src.lib.domain_packs.resolvable_values``. This table names each such value,
the keys that carry its identity, and whether the curation DB export reads it.
The pack's display declarations (``metadata.display`` with a ``mention``
role) must name the same values; a contract test keeps the two in step.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from src.lib.domain_packs.resolvable_values import (
    LOOKUP_OUTCOME_KEY,
    LOOKUP_OUTCOME_LABELS,
    MENTION_KEY,
    RESOLUTION_STATE_KEY,
    RESOLVED,
    ResolvableSpec,
    declared_resolvable_fields,
    effective_payload,
    unresolved_value,
)

from .._resolvable_payloads import CONDITION_TERM_IDENTITY_KEYS
from .constants import GENE_EXPRESSION_DOMAIN_PACK_ID, GENE_EXPRESSION_OBJECT_TYPE


@dataclass(frozen=True)
class GeneExpressionResolvableValue:
    """One declared gene-expression resolvable value."""

    field_path: str
    label: str
    spec: ResolvableSpec
    # Every key a validator writes into the value; all empty while unresolved.
    identity_keys: tuple[str, ...]
    multivalued: bool = False
    # False for context the curation DB handoff does not export (audit only).
    exported: bool = True
    # The keys the curation DB export joins on; empty means the value's id, else its label.
    export_keys: tuple[str, ...] = ()

    @property
    def join_keys(self) -> tuple[str, ...]:
        return self.export_keys or ((self.spec.id_key or self.spec.label_key),)


TERM_IDENTITY_KEYS = ("curie", "name")
_TERM = ResolvableSpec(id_key="curie", label_key="name")
SUBJECT_IDENTITY_KEYS = ("primary_external_id", "gene_symbol")
_SUBJECT = ResolvableSpec(id_key="primary_external_id", label_key="gene_symbol")
REFERENCE_IDENTITY_KEYS = ("reference_id", "curie", "title")
_REFERENCE = ResolvableSpec(id_key="reference_id", label_key="title")
# A controlled-vocabulary term: its name, vocabulary and internal id.
RELATION_IDENTITY_KEYS = ("name", "vocabulary", "id")
_VOCABULARY_TERM = ResolvableSpec(label_key="name")
DATA_PROVIDER_IDENTITY_KEYS = ("abbreviation",)
_DATA_PROVIDER = ResolvableSpec(label_key="abbreviation")
# A condition part: the shared Alliance condition-term identity (its curie and name).
_CONDITION_TERM = ResolvableSpec(id_key="curie", label_key="name")


GENE_EXPRESSION_RESOLVABLE_VALUES: tuple[GeneExpressionResolvableValue, ...] = (
    GeneExpressionResolvableValue(
        "data_provider", "Data provider", _DATA_PROVIDER, DATA_PROVIDER_IDENTITY_KEYS,
    ),
    GeneExpressionResolvableValue(
        "expression_annotation_subject", "Subject gene", _SUBJECT, SUBJECT_IDENTITY_KEYS,
    ),
    GeneExpressionResolvableValue(
        "relation", "Relation", _VOCABULARY_TERM, RELATION_IDENTITY_KEYS,
    ),
    GeneExpressionResolvableValue(
        "single_reference", "Evidence reference", _REFERENCE, REFERENCE_IDENTITY_KEYS,
    ),
    GeneExpressionResolvableValue(
        "expression_experiment.expression_assay_used", "Expression assay", _TERM, TERM_IDENTITY_KEYS,
    ),
    GeneExpressionResolvableValue(
        "expression_experiment.single_reference",
        "Experiment evidence reference",
        _REFERENCE,
        REFERENCE_IDENTITY_KEYS,
    ),
    GeneExpressionResolvableValue(
        "expression_experiment.entity_assayed", "Entity assayed", _SUBJECT, SUBJECT_IDENTITY_KEYS,
    ),
    GeneExpressionResolvableValue(
        "expression_pattern.when_expressed.developmental_stage_start",
        "Developmental stage start",
        _TERM,
        TERM_IDENTITY_KEYS,
    ),
    # LinkML range VocabularyTerm (the Stage Uberon Slim Terms vocabulary).
    GeneExpressionResolvableValue(
        "expression_pattern.when_expressed.stage_uberon_slim_terms",
        "Stage UBERON slim term",
        _VOCABULARY_TERM,
        RELATION_IDENTITY_KEYS,
        multivalued=True,
        export_keys=("vocabulary", "name"),
    ),
    GeneExpressionResolvableValue(
        "expression_pattern.where_expressed.anatomical_structure",
        "Anatomical structure",
        _TERM,
        TERM_IDENTITY_KEYS,
    ),
    GeneExpressionResolvableValue(
        "expression_pattern.where_expressed.anatomical_structure_uberon_terms",
        "Anatomical structure UBERON slim term",
        _TERM,
        TERM_IDENTITY_KEYS,
        multivalued=True,
    ),
    GeneExpressionResolvableValue(
        "expression_pattern.where_expressed.cellular_component",
        "Cellular component",
        _TERM,
        TERM_IDENTITY_KEYS,
    ),
    GeneExpressionResolvableValue(
        "expression_pattern.where_expressed.cellular_component_qualifiers",
        "Cellular component qualifier",
        _TERM,
        TERM_IDENTITY_KEYS,
        multivalued=True,
    ),
    GeneExpressionResolvableValue(
        "condition_relations.condition_relation_type",
        "Condition relation type",
        _VOCABULARY_TERM,
        RELATION_IDENTITY_KEYS,
        exported=False,
    ),
    GeneExpressionResolvableValue(
        "condition_relations.conditions.condition_class",
        "Condition class",
        _CONDITION_TERM,
        CONDITION_TERM_IDENTITY_KEYS,
        exported=False,
    ),
    GeneExpressionResolvableValue(
        "condition_relations.conditions.condition_id",
        "Condition term",
        _CONDITION_TERM,
        CONDITION_TERM_IDENTITY_KEYS,
        exported=False,
    ),
    GeneExpressionResolvableValue(
        "condition_relations.conditions.condition_chemical",
        "Condition chemical",
        _CONDITION_TERM,
        CONDITION_TERM_IDENTITY_KEYS,
        exported=False,
    ),
    GeneExpressionResolvableValue(
        "condition_relations.conditions.condition_taxon",
        "Condition taxon",
        _CONDITION_TERM,
        CONDITION_TERM_IDENTITY_KEYS,
        exported=False,
    ),
)


_VALUES_BY_PATH = {value.field_path: value for value in GENE_EXPRESSION_RESOLVABLE_VALUES}


def resolvable_value_for(field_path: str) -> GeneExpressionResolvableValue:
    """The declared resolvable value stored at ``field_path``."""

    try:
        return _VALUES_BY_PATH[field_path]
    except KeyError as exc:
        raise ValueError(f"{field_path} is not a gene-expression resolvable value") from exc


def staged_value(field_path: str, mention: str, **extra: Any) -> dict[str, Any]:
    """A value the builder stages for validation: the paper wording, no identity yet."""

    return unresolved_value(
        mention,
        identity_keys=resolvable_value_for(field_path).identity_keys,
        **extra,
    )


def is_resolved(value: Any) -> bool:
    """Whether a value read through ``effective_gene_expression_payload`` is resolved."""

    return isinstance(value, Mapping) and value.get(RESOLUTION_STATE_KEY) == RESOLVED


def effective_gene_expression_payload(
    payload: Mapping[str, Any],
    object_metadata: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    """The payload with every declared value's read-time resolution state.

    Values stored with the contract state read as stored; values stored
    before it go through the shared legacy rule. The value specs are the
    pack's own declarations (including the mirror sources that also cover a
    copy such as entity_assayed).
    """

    return effective_payload(
        payload,
        declared_gene_expression_values(),
        object_metadata=object_metadata,
    )


@lru_cache(maxsize=1)
def declared_gene_expression_values() -> Mapping[str, ResolvableSpec]:
    """The resolvable values the bundled gene-expression pack declares, by field path.

    Read once: the bundled pack does not change while the process runs.
    """

    from ..loader import get_alliance_domain_pack

    return MappingProxyType(
        declared_resolvable_fields(
            get_alliance_domain_pack(GENE_EXPRESSION_DOMAIN_PACK_ID).metadata,
            GENE_EXPRESSION_OBJECT_TYPE,
        )
    )


def unresolved_value_message(label: str, value: Mapping[str, Any]) -> str:
    """Curator-facing text for an unresolved value: UNRESOLVED, lookup result, paper wording."""

    outcome = LOOKUP_OUTCOME_LABELS[str(value[LOOKUP_OUTCOME_KEY])]
    mention = value.get(MENTION_KEY)
    wording = f" Paper wording: {mention!r}." if isinstance(mention, str) and mention.strip() else ""
    return f"{label} is UNRESOLVED (lookup result: {outcome}).{wording}"


__all__ = [
    "DATA_PROVIDER_IDENTITY_KEYS",
    "GENE_EXPRESSION_RESOLVABLE_VALUES",
    "GeneExpressionResolvableValue",
    "REFERENCE_IDENTITY_KEYS",
    "RELATION_IDENTITY_KEYS",
    "SUBJECT_IDENTITY_KEYS",
    "TERM_IDENTITY_KEYS",
    "declared_gene_expression_values",
    "effective_gene_expression_payload",
    "is_resolved",
    "resolvable_value_for",
    "staged_value",
    "unresolved_value_message",
]
