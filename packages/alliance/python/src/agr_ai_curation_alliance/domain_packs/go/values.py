"""GO proposal values under the extracted-vs-validated contract (ALL-1283).

Every GO value a curator reviews keeps the paper wording (``mention``) apart
from the identity a lookup confirmed. The builder resolves a value only from a
deterministic lookup: a run-scoped resolver output it verified, or its own
evidence-code table. Anything else is staged unresolved.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
REFERENCE_IDENTITY = ("curie",)
WITH_FROM_IDENTITY = ("curie",)
QUALIFIER_IDENTITY = ("name",)

# Single resolvable values and their identity keys; ``with_from`` is a list
# whose every element is its own value.
RESOLVABLE_VALUE_FIELDS = {
    "gene_product": GENE_PRODUCT_IDENTITY,
    "go_term": GO_TERM_IDENTITY,
    "evidence_code": EVIDENCE_CODE_IDENTITY,
    "reference_curie": REFERENCE_IDENTITY,
}
RESOLVABLE_LIST_FIELDS = {"with_from": WITH_FROM_IDENTITY, "qualifiers": QUALIFIER_IDENTITY}

# Resolution keys only the builder and validators write; the extractor never does.
BUILDER_OWNED_KEYS = frozenset(key for key in CONTRACT_KEYS if key != MENTION_KEY)

UNKNOWN_QUALIFIER_EXPLANATION = "Not a GO relation qualifier in this workflow's qualifier vocabulary."
QUALIFIER_ASPECT_EXPLANATION = "GO relation qualifier not allowed for the annotation's GO aspect."
UNKNOWN_EVIDENCE_CODE_EXPLANATION = (
    "GO evidence code not supported by this workflow's ECO table."
)


# Record keys that carry a lookup record's own label; never echoes of the query.
RECORD_LABEL_KEYS = ("symbol", "name", "label")


def record_holds(output: Any, record: Mapping[str, Any]) -> bool:
    """Whether one record of a lookup output carries a whole identity together.

    ``record`` names the ``identifier``, and optionally the ``label`` and
    ``aspect``. The identifier must be a value of one record object; the label
    must be that same object's own symbol, name or label (never an echoed
    ``query`` or any other key); the aspect must be its ``aspect``.
    """

    identifier = record["identifier"]
    if isinstance(output, Mapping):
        if (
            any(item == identifier for item in output.values())
            and ("label" not in record
                 or any(output.get(key) == record["label"] for key in RECORD_LABEL_KEYS))
            and ("aspect" not in record or output.get("aspect") == record["aspect"])
        ):
            return True
        return any(record_holds(item, record) for item in output.values())
    if isinstance(output, list):
        return any(record_holds(item, record) for item in output)
    return False


def is_resolved(value: Any) -> bool:
    return has_resolution_state(value) and value[RESOLUTION_STATE_KEY] == RESOLVED


def _identity(**keys: str | None) -> dict[str, str]:
    return {key: value for key, value in keys.items() if value}


def gene_product_value(
    mention: str,
    *,
    curie: str | None,
    label: str | None,
    entity_type: str,
    taxon_curie: str,
) -> dict[str, Any]:
    """Resolved only with a resolver-confirmed CURIE (and its label, when given)."""

    extra = {"entity_type": entity_type, "taxon_curie": taxon_curie}
    if curie:
        return resolved_value(mention, _identity(curie=curie, label=label), **extra)
    return unresolved_value(mention, identity_keys=GENE_PRODUCT_IDENTITY, **extra)


def go_term_value(
    mention: str,
    *,
    curie: str | None,
    label: str | None,
    aspect: str,
) -> dict[str, Any]:
    if curie:
        return resolved_value(mention, _identity(curie=curie, label=label), aspect=aspect)
    return unresolved_value(mention, identity_keys=GO_TERM_IDENTITY, aspect=aspect)


def evidence_code_value(mention: str) -> dict[str, Any]:
    """Look the proposed code up in the builder's GO evidence-code table."""

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


def reference_value(mention: str, *, curie: str | None) -> dict[str, Any]:
    if curie:
        return resolved_value(mention, {"curie": curie})
    return unresolved_value(mention, identity_keys=REFERENCE_IDENTITY)


def qualifier_value(mention: str, *, aspect: str) -> dict[str, Any]:
    """Look the proposed qualifier up in the builder's GO relation vocabulary for the aspect."""

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


def with_from_value(mention: str, *, curie: str | None) -> dict[str, Any]:
    if curie:
        return resolved_value(mention, {"curie": curie})
    return unresolved_value(mention, identity_keys=WITH_FROM_IDENTITY)


__all__ = [
    "BUILDER_OWNED_KEYS",
    "EVIDENCE_CODE_IDENTITY",
    "GENE_PRODUCT_IDENTITY",
    "GO_TERM_IDENTITY",
    "REFERENCE_IDENTITY",
    "RESOLVABLE_LIST_FIELDS",
    "RESOLVABLE_VALUE_FIELDS",
    "UNKNOWN_EVIDENCE_CODE_EXPLANATION",
    "WITH_FROM_IDENTITY",
    "evidence_code_value",
    "gene_product_value",
    "go_term_value",
    "QUALIFIER_IDENTITY",
    "is_resolved",
    "qualifier_value",
    "qualifier_values",
    "record_holds",
    "reference_value",
    "with_from_value",
]
