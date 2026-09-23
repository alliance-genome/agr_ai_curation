"""Alliance helpers for extracted-vs-validated values (ALL-1283).

Builders stage every resolvable value (a term, subject, reference, condition
component, fixed-choice selection, or proposed ID) as one object with the
paper wording in ``mention`` and the shared contract state from
``src.lib.domain_packs.resolvable_values``. An identifier or name the extractor
proposes is kept under ``proposed_<key>`` so validators can check it; it never
fills the value's own id/label keys, which only a validator writes.

Export adapters read a value's id/label through ``export_identity``, which
applies the shared read-time rule (including values stored before the
contract) and turns an unresolved value into an adapter blocker.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from src.lib.domain_packs.resolvable_values import (
    LOOKUP_OUTCOME_LABELS,
    MENTION_KEY,
    PROPOSED_KEY_PREFIX,
    RESOLVED,
    effective_resolution,
    proposed_key,
    unresolved_value,
    validator_event_covers,
)

from ._export_utils import MISSING, adapter_blocker, list_value, payload_value


# Identity keys by value kind. A controlled-vocabulary selection is named by its
# term name; vocabulary/id are the validator's snapshot written alongside it.
VOCABULARY_TERM_IDENTITY_KEYS = ("name",)
ONTOLOGY_TERM_IDENTITY_KEYS = ("curie", "name")
CONDITION_TERM_IDENTITY_KEYS = ("curie", "name")

# Condition components that name an ontology term, in staging order.
CONDITION_TERM_COMPONENTS = (
    "condition_class",
    "condition_id",
    "condition_chemical",
    "condition_taxon",
)
CONDITION_TEXT_FIELDS = ("condition_free_text", "condition_summary")


def clean_text(value: Any) -> str | None:
    text = str(value if value is not None else "").strip()
    return text or None


def staged_value(
    mention: str,
    *,
    identity_keys: Sequence[str],
    proposals: Mapping[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One value as the extractor staged it: paper wording, no validated identity yet.

    ``proposals`` maps identity keys to what the extractor proposed for them;
    each is stored under ``proposed_<key>`` and only when present.
    """

    proposed = {
        proposed_key(key): text
        for key, value in (proposals or {}).items()
        if (text := clean_text(value)) is not None
    }
    return unresolved_value(mention, identity_keys=identity_keys, **extra, **proposed)


def staged_list(mentions: Iterable[Any], *, identity_keys: Sequence[str]) -> list[dict[str, Any]]:
    """Stage each distinct proposed entry of an extractor-proposed list as its own value."""

    staged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in mentions or []:
        mention = clean_text(raw)
        if mention is None or mention in seen:
            continue
        seen.add(mention)
        staged.append(staged_value(mention, identity_keys=identity_keys))
    return staged


def condition_relations_payload(raw_relations: Any) -> list[dict[str, Any]]:
    """Materialize staged condition relations as resolvable values.

    Each staged ``{condition_relation_type, conditions: [{<component>_mention,
    <component>_curie, condition_free_text, condition_summary}]}`` becomes
    ``{condition_relation_type: <vocabulary value>, conditions: [{<component>:
    <term value>, ...}]}``. A component exists only when the extractor gave its
    paper wording; a CURIE it proposed is kept as ``proposed_curie``. The
    relation type is the extractor's chosen vocabulary text. A relation with no
    conditions is dropped.
    """

    if not isinstance(raw_relations, Sequence) or isinstance(raw_relations, (str, bytes)):
        return []
    relations: list[dict[str, Any]] = []
    for raw_relation in raw_relations:
        if not isinstance(raw_relation, Mapping):
            continue
        relation_type = clean_text(raw_relation.get("condition_relation_type"))
        if relation_type is None:
            continue
        raw_conditions = raw_relation.get("conditions")
        if not isinstance(raw_conditions, Sequence) or isinstance(raw_conditions, (str, bytes)):
            raw_conditions = []
        conditions: list[dict[str, Any]] = []
        for raw_condition in raw_conditions:
            if not isinstance(raw_condition, Mapping):
                continue
            condition: dict[str, Any] = {}
            for component in CONDITION_TERM_COMPONENTS:
                mention = clean_text(raw_condition.get(f"{component}_mention"))
                if mention is None:
                    continue
                condition[component] = staged_value(
                    mention,
                    identity_keys=CONDITION_TERM_IDENTITY_KEYS,
                    proposals={"curie": raw_condition.get(f"{component}_curie")},
                )
            for text_key in CONDITION_TEXT_FIELDS:
                text = clean_text(raw_condition.get(text_key))
                if text is not None:
                    condition[text_key] = text
            if condition:
                conditions.append(condition)
        if conditions:
            relations.append(
                {
                    "condition_relation_type": staged_value(
                        relation_type, identity_keys=VOCABULARY_TERM_IDENTITY_KEYS
                    ),
                    "conditions": conditions,
                }
            )
    return relations


def _candidate_object_metadata(candidate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    domain_object = candidate.get("object")
    if not isinstance(domain_object, Mapping):
        return None
    metadata = domain_object.get("metadata")
    return metadata if isinstance(metadata, Mapping) else None


def export_identity(
    *,
    candidate: Mapping[str, Any],
    payload: Mapping[str, Any],
    field_path: str,
    identity_keys: Sequence[str],
    code: str,
    label: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The validated identity of the value at ``field_path``, or a blocker.

    Returns ``(identity, None)`` for a resolved value (the shared read-time
    rule decides values stored before the contract), ``(None, blocker)`` for an
    unresolved one, and ``(None, None)`` when the value is absent. An
    unresolved value never reaches an export as if it were resolved.
    """

    value = payload_value(payload, field_path)
    if value is MISSING or value is None:
        return None, None
    if not isinstance(value, Mapping):
        return None, adapter_blocker(
            candidate=candidate,
            code=code,
            field_path=field_path,
            message=f"{label} is not a validated value.",
            details={"observed_type": type(value).__name__},
        )
    state, outcome = effective_resolution(
        value,
        identity_keys=identity_keys,
        covered_by_validator=validator_event_covers(
            _candidate_object_metadata(candidate), field_path
        ),
    )
    if state == RESOLVED:
        return {key: value.get(key) for key in identity_keys}, None
    return None, adapter_blocker(
        candidate=candidate,
        code=code,
        field_path=field_path,
        message=f"{label} is unresolved ({LOOKUP_OUTCOME_LABELS[outcome]}).",
        details={"lookup_outcome": outcome, "paper_wording": value.get(MENTION_KEY)},
    )


def export_identity_list(
    *,
    candidate: Mapping[str, Any],
    payload: Mapping[str, Any],
    field_path: str,
    identity_keys: Sequence[str],
    code: str,
    label: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validated identities of every element of a resolvable list, and a blocker per unresolved one."""

    identities: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for index in range(len(list_value(payload, field_path))):
        identity, blocker = export_identity(
            candidate=candidate,
            payload=payload,
            field_path=f"{field_path}[{index}]",
            identity_keys=identity_keys,
            code=code,
            label=f"{label} {index + 1}",
        )
        if blocker is not None:
            blockers.append(blocker)
        elif identity is not None:
            identities.append(identity)
    return identities, blockers


def export_condition_relations(
    *,
    candidate: Mapping[str, Any],
    payload: Mapping[str, Any],
    code: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Condition relations as validated identities, and a blocker for every unresolved part."""

    relations: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for relation_index, raw_relation in enumerate(list_value(payload, "condition_relations")):
        relation_path = f"condition_relations[{relation_index}]"
        relation_type, blocker = export_identity(
            candidate=candidate,
            payload=payload,
            field_path=f"{relation_path}.condition_relation_type",
            identity_keys=VOCABULARY_TERM_IDENTITY_KEYS,
            code=code,
            label=f"Condition relation type {relation_index + 1}",
        )
        if blocker is not None:
            blockers.append(blocker)
        conditions: list[dict[str, Any]] = []
        raw_conditions = raw_relation.get("conditions") if isinstance(raw_relation, Mapping) else None
        for condition_index, raw_condition in enumerate(raw_conditions or []):
            if not isinstance(raw_condition, Mapping):
                continue
            condition_path = f"{relation_path}.conditions[{condition_index}]"
            condition: dict[str, Any] = {}
            for component in CONDITION_TERM_COMPONENTS:
                identity, blocker = export_identity(
                    candidate=candidate,
                    payload=payload,
                    field_path=f"{condition_path}.{component}",
                    identity_keys=CONDITION_TERM_IDENTITY_KEYS,
                    code=code,
                    label=f"Condition {component.removeprefix('condition_')} "
                    f"({relation_index + 1}.{condition_index + 1})",
                )
                if blocker is not None:
                    blockers.append(blocker)
                elif identity is not None:
                    condition[component] = identity
            for text_key in CONDITION_TEXT_FIELDS:
                if isinstance(raw_condition.get(text_key), str):
                    condition[text_key] = raw_condition[text_key]
            conditions.append(condition)
        if relation_type is not None:
            relations.append({"condition_relation_type": relation_type, "conditions": conditions})
    return relations, blockers


__all__ = [
    "CONDITION_TERM_COMPONENTS",
    "CONDITION_TERM_IDENTITY_KEYS",
    "CONDITION_TEXT_FIELDS",
    "ONTOLOGY_TERM_IDENTITY_KEYS",
    "PROPOSED_KEY_PREFIX",
    "VOCABULARY_TERM_IDENTITY_KEYS",
    "clean_text",
    "condition_relations_payload",
    "export_condition_relations",
    "export_identity",
    "export_identity_list",
    "proposed_key",
    "staged_list",
    "staged_value",
]
