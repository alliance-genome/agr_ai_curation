"""GO proposal values under the extracted-vs-validated contract (ALL-1283).

Every GO value a curator reviews keeps the paper wording (``mention``) apart
from the identity validation confirms. Extraction never searches a database:
the builder stages the gene product, GO term, reference and with/from entries
with their paper wording and, when the paper itself prints an identifier, that
identifier as the extractor's proposal (``proposed_curie``). Those values are
unresolved and not yet validated; only validators (or a curator override)
resolve them. The evidence code and qualifiers are fixed choices the builder
maps with its own local tables (mapping, not search).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.lib.domain_packs.resolvable_values import (
    CONTRACT_KEYS,
    MENTION_KEY,
    OUTCOME_CONFLICT,
    OUTCOME_NOT_FOUND,
    RESOLUTION_STATE_KEY,
    RESOLVED,
    has_resolution_state,
    resolved_value,
    unresolved_value,
)

from .constants import GO_EVIDENCE_CODE_ECO, GO_QUALIFIERS_BY_ASPECT


GENE_PRODUCT_IDENTITY = ("curie", "label")
GO_TERM_IDENTITY = ("curie", "label")
EVIDENCE_CODE_IDENTITY = ("code", "eco_curie")
# The Alliance reference ID, plus the PMID and DOI validation confirms beside it (either may be null).
REFERENCE_IDENTITY = ("curie", "pmid", "doi")
WITH_FROM_IDENTITY = ("curie",)
QUALIFIER_IDENTITY = ("name",)

# Single resolvable values and their identity keys; ``with_from`` and
# ``qualifiers`` are lists whose every element is its own value.
RESOLVABLE_VALUE_FIELDS = {
    "gene_product": GENE_PRODUCT_IDENTITY,
    "go_term": GO_TERM_IDENTITY,
    "evidence_code": EVIDENCE_CODE_IDENTITY,
    "reference_curie": REFERENCE_IDENTITY,
}
RESOLVABLE_LIST_FIELDS = {"with_from": WITH_FROM_IDENTITY, "qualifiers": QUALIFIER_IDENTITY}
# Values the builder maps with its own local tables; every other value awaits validation.
MAPPED_FIELDS = frozenset({"evidence_code", "qualifiers"})

# The key that keeps an identifier the paper itself prints: validator input only.
PROPOSED_CURIE_KEY = "proposed_curie"
# Values that may carry a paper-stated identifier.
PROPOSAL_FIELDS = frozenset({"gene_product", "go_term", "reference_curie", "with_from"})

# Keys only validation (or a curator override) writes; the extractor never does.
BUILDER_OWNED_KEYS = frozenset(key for key in CONTRACT_KEYS if key != MENTION_KEY)

UNKNOWN_QUALIFIER_EXPLANATION = "Not a GO relation qualifier in this workflow's qualifier vocabulary."
QUALIFIER_ASPECT_EXPLANATION = "GO relation qualifier not allowed for the annotation's GO aspect."
UNKNOWN_EVIDENCE_CODE_EXPLANATION = (
    "GO evidence code not supported by this workflow's ECO table."
)


def is_resolved(value: Any) -> bool:
    return has_resolution_state(value) and value[RESOLUTION_STATE_KEY] == RESOLVED


def _proposal(proposed_curie: str | None) -> dict[str, str]:
    return {PROPOSED_CURIE_KEY: proposed_curie} if proposed_curie else {}


def gene_product_value(
    mention: str,
    *,
    proposed_curie: str | None,
    entity_type: str,
    taxon_curie: str,
) -> dict[str, Any]:
    return unresolved_value(
        mention,
        identity_keys=GENE_PRODUCT_IDENTITY,
        entity_type=entity_type,
        taxon_curie=taxon_curie,
        **_proposal(proposed_curie),
    )


def go_term_value(mention: str, *, proposed_curie: str | None, aspect: str) -> dict[str, Any]:
    return unresolved_value(
        mention, identity_keys=GO_TERM_IDENTITY, aspect=aspect, **_proposal(proposed_curie)
    )


def evidence_code_value(mention: str) -> dict[str, Any]:
    """Map the chosen code to its ECO class with the builder's GO evidence-code table."""

    code = mention.strip().upper()
    eco_curie = GO_EVIDENCE_CODE_ECO.get(code)
    if eco_curie is None:
        return unresolved_value(
            mention,
            identity_keys=EVIDENCE_CODE_IDENTITY,
            outcome=OUTCOME_NOT_FOUND,
            explanation=UNKNOWN_EVIDENCE_CODE_EXPLANATION,
        )
    return resolved_value(mention, {"code": code, "eco_curie": eco_curie})


def reference_value(mention: str, *, proposed_curie: str | None) -> dict[str, Any]:
    return unresolved_value(mention, identity_keys=REFERENCE_IDENTITY, **_proposal(proposed_curie))


def with_from_value(mention: str, *, proposed_curie: str | None) -> dict[str, Any]:
    return unresolved_value(mention, identity_keys=WITH_FROM_IDENTITY, **_proposal(proposed_curie))


def qualifier_value(mention: str, *, aspect: str) -> dict[str, Any]:
    """Map the chosen qualifier with the builder's GO relation vocabulary for the aspect."""

    name = "_".join(mention.strip().lower().replace("-", " ").split())
    if name in GO_QUALIFIERS_BY_ASPECT.get(aspect, frozenset()):
        return resolved_value(mention, {"name": name})
    known = any(name in allowed for allowed in GO_QUALIFIERS_BY_ASPECT.values())
    return unresolved_value(
        mention,
        identity_keys=QUALIFIER_IDENTITY,
        outcome=OUTCOME_CONFLICT if known else OUTCOME_NOT_FOUND,
        explanation=QUALIFIER_ASPECT_EXPLANATION if known else UNKNOWN_QUALIFIER_EXPLANATION,
    )


def qualifier_values(mentions: Sequence[str], *, aspect: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for mention in mentions:
        value = qualifier_value(mention, aspect=aspect)
        if value not in values:
            values.append(value)
    return values


__all__ = [
    "BUILDER_OWNED_KEYS",
    "EVIDENCE_CODE_IDENTITY",
    "GENE_PRODUCT_IDENTITY",
    "GO_TERM_IDENTITY",
    "MAPPED_FIELDS",
    "PROPOSAL_FIELDS",
    "PROPOSED_CURIE_KEY",
    "QUALIFIER_IDENTITY",
    "REFERENCE_IDENTITY",
    "RESOLVABLE_LIST_FIELDS",
    "RESOLVABLE_VALUE_FIELDS",
    "UNKNOWN_EVIDENCE_CODE_EXPLANATION",
    "WITH_FROM_IDENTITY",
    "evidence_code_value",
    "gene_product_value",
    "go_term_value",
    "is_resolved",
    "qualifier_value",
    "qualifier_values",
    "reference_value",
    "with_from_value",
]
