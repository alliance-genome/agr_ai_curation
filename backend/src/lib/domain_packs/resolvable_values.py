"""Extracted vs validated values: the shared resolvable-value contract (ALL-1283).

Every validated value (an entity ref, an ontology term, a fixed-choice
selection, or one element of an extractor-proposed ID list) is stored as one
object. It keeps its domain's own id/label keys and adds:

- ``mention``: the paper's wording (or the extractor's chosen text for a
  fixed-choice field). Written once by the builder; nothing overwrites it.
- ``resolution_state``: the closed vocabulary ``ResolutionState``.
- ``lookup_outcome``: the closed vocabulary ``LookupOutcome``; always set,
  ``matched`` exactly when the value is resolved.
- ``validator_explanation``: the validator's own explanation (free text,
  nullable), and ``validator_curator_message``: its curator message, kept
  apart from the explanation.

The invariant: the state is ``resolved`` if and only if a validator (or a
deterministic lookup that is the validation) supplied the identity, if and
only if the lookup outcome is ``matched``; an unresolved value has empty
id/label keys. A value the paper never mentions is absent, which is different
from unresolved.

Values stored before this contract carry no contract state (no
``resolution_state`` and ``lookup_outcome`` pair). Their effective state is
decided at read time (``effective_resolution``): resolved only when they hold
an identity AND a validator write-back event
(``metadata.validator_resolved_value_materialization``) covers their path;
otherwise unresolved with outcome ``legacy_unverified``.

Lookup outcomes are derived in code only: validator failure classifications
map through one exhaustive table (``lookup_outcome_for_failure``).

Domain builders, the materializer and every display surface use these helpers;
none of them re-implements the rules.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.lib.domain_packs.validator_result_classification import ValidatorFailureClassification
from src.schemas.domain_envelope import parse_field_path


MENTION_KEY = "mention"
RESOLUTION_STATE_KEY = "resolution_state"
LOOKUP_OUTCOME_KEY = "lookup_outcome"
VALIDATOR_EXPLANATION_KEY = "validator_explanation"
VALIDATOR_CURATOR_MESSAGE_KEY = "validator_curator_message"
# Every key the contract adds to a value; the rest are the domain's own keys.
CONTRACT_KEYS = (
    MENTION_KEY,
    RESOLUTION_STATE_KEY,
    LOOKUP_OUTCOME_KEY,
    VALIDATOR_EXPLANATION_KEY,
    VALIDATOR_CURATOR_MESSAGE_KEY,
)


class ResolutionState(StrEnum):
    """Closed vocabulary for ``resolution_state``."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class LookupOutcome(StrEnum):
    """Closed vocabulary for ``lookup_outcome``."""

    MATCHED = "matched"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"
    BLOCKED = "blocked"
    TRANSIENT = "transient"
    INVALID_SCHEMA = "invalid_schema"
    MISSING_EXPECTED_RESULT_FIELD = "missing_expected_result_field"
    # Every lookup succeeded, but the validator judged that no candidate fits.
    REJECTED_CANDIDATES = "rejected_candidates"
    # No validator ran yet: pending, binding in development, or skipped.
    NOT_VALIDATED = "not_validated"
    # Read time only, for values stored before this contract.
    LEGACY_UNVERIFIED = "legacy_unverified"


RESOLVED = ResolutionState.RESOLVED.value
UNRESOLVED = ResolutionState.UNRESOLVED.value
RESOLUTION_STATES = tuple(state.value for state in ResolutionState)
LOOKUP_OUTCOMES = tuple(outcome.value for outcome in LookupOutcome)

OUTCOME_MATCHED = LookupOutcome.MATCHED.value
OUTCOME_NOT_FOUND = LookupOutcome.NOT_FOUND.value
OUTCOME_AMBIGUOUS = LookupOutcome.AMBIGUOUS.value
OUTCOME_CONFLICT = LookupOutcome.CONFLICT.value
OUTCOME_BLOCKED = LookupOutcome.BLOCKED.value
OUTCOME_TRANSIENT = LookupOutcome.TRANSIENT.value
OUTCOME_INVALID_SCHEMA = LookupOutcome.INVALID_SCHEMA.value
OUTCOME_MISSING_EXPECTED_RESULT_FIELD = LookupOutcome.MISSING_EXPECTED_RESULT_FIELD.value
OUTCOME_REJECTED_CANDIDATES = LookupOutcome.REJECTED_CANDIDATES.value
OUTCOME_NOT_VALIDATED = LookupOutcome.NOT_VALIDATED.value
OUTCOME_LEGACY_UNVERIFIED = LookupOutcome.LEGACY_UNVERIFIED.value

# The one table from validator failure classifications to lookup outcomes. It
# must name every classification (a test enforces it).
_OUTCOME_FOR_FAILURE: dict[ValidatorFailureClassification, LookupOutcome] = {
    "not_found": LookupOutcome.NOT_FOUND,
    "ambiguous": LookupOutcome.AMBIGUOUS,
    "conflict": LookupOutcome.CONFLICT,
    "blocked": LookupOutcome.BLOCKED,
    "transient": LookupOutcome.TRANSIENT,
    "invalid_schema": LookupOutcome.INVALID_SCHEMA,
    "missing_expected_result_field": LookupOutcome.MISSING_EXPECTED_RESULT_FIELD,
    "rejected_candidates": LookupOutcome.REJECTED_CANDIDATES,
}
# Outcomes a stored unresolved value may carry (legacy_unverified is read-time only).
STORED_UNRESOLVED_OUTCOMES = tuple(
    outcome.value
    for outcome in LookupOutcome
    if outcome not in (LookupOutcome.MATCHED, LookupOutcome.LEGACY_UNVERIFIED)
)

# Plain words for curators, e.g. a "(lookup result)" column.
LOOKUP_OUTCOME_LABELS: dict[str, str] = {
    OUTCOME_MATCHED: "Matched",
    OUTCOME_NOT_FOUND: "Not found",
    OUTCOME_AMBIGUOUS: "Several matches",
    OUTCOME_CONFLICT: "Conflict",
    OUTCOME_BLOCKED: "Blocked",
    OUTCOME_TRANSIENT: "Temporary error",
    OUTCOME_INVALID_SCHEMA: "Invalid validator output",
    OUTCOME_MISSING_EXPECTED_RESULT_FIELD: "Validator result incomplete",
    OUTCOME_REJECTED_CANDIDATES: "Candidates rejected",
    OUTCOME_NOT_VALIDATED: "Not validated yet",
    OUTCOME_LEGACY_UNVERIFIED: "Legacy, unverified",
}
RESOLUTION_STATE_LABELS: dict[str, str] = {RESOLVED: "Resolved", UNRESOLVED: "Unresolved"}

NOT_VALIDATED_EXPLANATION = "Not validated yet."
LEGACY_EXPLANATION = "Recorded before validation tracking; not verified."

# The cell text for an unresolved value on every non-JSON surface.
UNRESOLVED_DISPLAY = "UNRESOLVED"
# Column header suffixes for a resolvable value's own leaves.
PAPER_WORDING_SUFFIX = "(paper wording)"
STATUS_SUFFIX = "(status)"
LOOKUP_RESULT_SUFFIX = "(lookup result)"
VALIDATOR_EXPLANATION_SUFFIX = "(validator explanation)"
VALIDATOR_MESSAGE_SUFFIX = "(validator message)"
_LEAF_HEADER_SUFFIXES = {
    MENTION_KEY: PAPER_WORDING_SUFFIX,
    RESOLUTION_STATE_KEY: STATUS_SUFFIX,
    LOOKUP_OUTCOME_KEY: LOOKUP_RESULT_SUFFIX,
    VALIDATOR_EXPLANATION_KEY: VALIDATOR_EXPLANATION_SUFFIX,
    VALIDATOR_CURATOR_MESSAGE_KEY: VALIDATOR_MESSAGE_SUFFIX,
}
# Plain words for the stored codes of a resolvable value's vocabulary leaves.
LEAF_VALUE_LABELS = {
    RESOLUTION_STATE_KEY: RESOLUTION_STATE_LABELS,
    LOOKUP_OUTCOME_KEY: LOOKUP_OUTCOME_LABELS,
}
# Appended to a legacy value's stored text, shown as paper wording.
LEGACY_UNVERIFIED_SUFFIX = "(legacy, unverified)"

VALIDATOR_MATERIALIZATION_METADATA_KEY = "validator_resolved_value_materialization"


class ResolvableValueError(ValueError):
    """A resolvable value violates the extracted-vs-validated invariant."""


def lookup_outcome_for_failure(classification: str) -> str:
    """The lookup outcome for a ``validator_failure_classification`` result."""

    try:
        return _OUTCOME_FOR_FAILURE[classification].value  # type: ignore[index]
    except KeyError as exc:
        raise ResolvableValueError(
            f"No lookup outcome for validator failure classification {classification!r}"
        ) from exc


def resolvable_leaf_header(parent_label: str, key: str, *, mention_key: str = MENTION_KEY) -> str | None:
    """The column header for a resolvable value's own leaf, e.g. "Anatomy (lookup result)".

    None for the value's domain keys (id, label, ...), which read under their own names.
    """

    if key == mention_key:
        suffix = PAPER_WORDING_SUFFIX
    elif key == MENTION_KEY:
        suffix = None
    else:
        suffix = _LEAF_HEADER_SUFFIXES.get(key)
    return f"{parent_label} {suffix}" if suffix else None


@dataclass(frozen=True)
class ResolvableSpec:
    """Where one kind of resolvable value keeps its identity and paper wording.

    ``id_key`` and ``label_key`` are the domain's existing keys (either may be
    absent, not both); ``mention_key`` is the paper-wording key.
    """

    id_key: str | None = None
    label_key: str | None = None
    mention_key: str = MENTION_KEY

    def __post_init__(self) -> None:
        if not (self.id_key or self.label_key):
            raise ResolvableValueError("A resolvable value needs an id key or a label key")

    @property
    def identity_keys(self) -> tuple[str, ...]:
        return tuple(key for key in (self.id_key, self.label_key) if key)


def resolvable_spec_from_display(display: Mapping[str, Any] | None) -> ResolvableSpec | None:
    """The resolvable spec a pack display declaration names, or None.

    A display spec declares a resolvable value by naming a ``mention`` role
    next to its ``label``/``id`` roles (``src.lib.flows.value_display``).
    """

    if not isinstance(display, Mapping) or not display.get("mention"):
        return None
    return ResolvableSpec(
        id_key=display.get("id") or None,
        label_key=display.get("label") or None,
        mention_key=str(display["mention"]),
    )


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def has_resolution_state(value: Any) -> bool:
    """Whether a stored value carries the contract state.

    That is a ``resolution_state`` and a ``lookup_outcome`` written together.
    Values stored before the contract (no state, or another word in
    ``resolution_state`` without a lookup outcome) do not.
    """

    return (
        isinstance(value, Mapping)
        and RESOLUTION_STATE_KEY in value
        and LOOKUP_OUTCOME_KEY in value
    )


def holds_resolution(value: Any) -> bool:
    """Whether a stored mapping is a resolvable value (it has a state or a paper mention)."""

    return isinstance(value, Mapping) and (
        RESOLUTION_STATE_KEY in value or isinstance(value.get(MENTION_KEY), str)
    )


def _optional_text(value: Any, key: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ResolvableValueError(f"{key} must be text or null, got {type(value).__name__}")


def check_resolvable_value(value: Any, *, identity_keys: Sequence[str]) -> None:
    """Raise ``ResolvableValueError`` unless ``value`` satisfies the invariant.

    ``identity_keys`` are the id/label keys the caller's domain uses. The state
    and outcome must be values of their closed vocabularies.
    """

    if not isinstance(value, Mapping):
        raise ResolvableValueError(f"A resolvable value must be an object, not {type(value).__name__}")
    if not identity_keys:
        raise ResolvableValueError("check_resolvable_value needs the value's id/label keys")
    mention = value.get(MENTION_KEY)
    if MENTION_KEY in value and not (isinstance(mention, str) and mention.strip()):
        raise ResolvableValueError("mention must be the non-empty paper wording")
    _optional_text(value.get(VALIDATOR_EXPLANATION_KEY), VALIDATOR_EXPLANATION_KEY)
    _optional_text(value.get(VALIDATOR_CURATOR_MESSAGE_KEY), VALIDATOR_CURATOR_MESSAGE_KEY)
    state = value.get(RESOLUTION_STATE_KEY)
    outcome = value.get(LOOKUP_OUTCOME_KEY)
    if state not in RESOLUTION_STATES:
        raise ResolvableValueError(f"resolution_state must be one of {RESOLUTION_STATES}, got {state!r}")
    if outcome not in LOOKUP_OUTCOMES:
        raise ResolvableValueError(f"lookup_outcome must be one of {LOOKUP_OUTCOMES}, got {outcome!r}")
    has_identity = any(not _is_empty(value.get(key)) for key in identity_keys)
    if state == RESOLVED:
        if outcome != OUTCOME_MATCHED:
            raise ResolvableValueError(f"A resolved value's lookup_outcome is matched, got {outcome!r}")
        if not has_identity:
            raise ResolvableValueError(
                "A resolved value needs the identity a validator supplied "
                f"(one of {', '.join(identity_keys)})"
            )
        return
    if outcome not in STORED_UNRESOLVED_OUTCOMES:
        raise ResolvableValueError(
            f"An unresolved value's lookup_outcome is one of {STORED_UNRESOLVED_OUTCOMES}, got {outcome!r}"
        )
    if has_identity:
        raise ResolvableValueError(
            "An unresolved value never carries an identity; "
            f"{', '.join(key for key in identity_keys if not _is_empty(value.get(key)))} must be empty"
        )


def _required_mention(mention: Any) -> str:
    if not isinstance(mention, str) or not mention.strip():
        raise ResolvableValueError("A resolvable value needs its paper wording (mention)")
    return mention.strip()


def resolved_value(
    mention: str,
    identity: Mapping[str, Any],
    *,
    explanation: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A value whose identity a deterministic lookup (that is the validation) supplied.

    ``identity`` holds the domain's id/label keys (e.g. ``{"curie": ..., "name": ...}``);
    ``extra`` holds other domain keys kept alongside (e.g. ``vocabulary``);
    ``explanation`` says how the lookup matched, when the caller has it.
    """

    if not identity:
        raise ResolvableValueError("A resolved value needs its identity keys")
    value = {
        **extra,
        **dict(identity),
        MENTION_KEY: _required_mention(mention),
        RESOLUTION_STATE_KEY: RESOLVED,
        LOOKUP_OUTCOME_KEY: OUTCOME_MATCHED,
        VALIDATOR_EXPLANATION_KEY: explanation,
    }
    check_resolvable_value(value, identity_keys=tuple(identity))
    return value


def unresolved_value(
    mention: str,
    *,
    identity_keys: Sequence[str],
    outcome: str = OUTCOME_NOT_VALIDATED,
    explanation: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A staged value no validator has resolved; its identity keys are written as null.

    A value staged for validation (``not_validated``) explains itself as
    "Not validated yet."
    """

    if outcome == OUTCOME_NOT_VALIDATED and explanation is None:
        explanation = NOT_VALIDATED_EXPLANATION
    value = {
        **extra,
        **{key: None for key in identity_keys},
        MENTION_KEY: _required_mention(mention),
        RESOLUTION_STATE_KEY: UNRESOLVED,
        LOOKUP_OUTCOME_KEY: outcome,
        VALIDATOR_EXPLANATION_KEY: explanation,
    }
    check_resolvable_value(value, identity_keys=identity_keys)
    return value


def _write_validator_text(
    value: MutableMapping[str, Any],
    explanation: str | None,
    curator_message: str | None,
) -> None:
    value[VALIDATOR_EXPLANATION_KEY] = explanation
    value[VALIDATOR_CURATOR_MESSAGE_KEY] = curator_message


def mark_resolved(
    value: MutableMapping[str, Any],
    identity: Mapping[str, Any],
    *,
    explanation: str | None,
    curator_message: str | None = None,
) -> None:
    """Write a validator-supplied identity, the resolved state and the validator's own words.

    ``mention`` is untouched. ``explanation`` and ``curator_message`` come from
    the validator result and are stored apart, never merged.
    """

    if not identity or all(_is_empty(item) for item in identity.values()):
        raise ResolvableValueError("mark_resolved needs the identity a validator supplied")
    value.update(identity)
    value[RESOLUTION_STATE_KEY] = RESOLVED
    value[LOOKUP_OUTCOME_KEY] = OUTCOME_MATCHED
    _write_validator_text(value, explanation, curator_message)
    check_resolvable_value(value, identity_keys=tuple(identity))


def mark_unresolved(
    value: MutableMapping[str, Any],
    outcome: str,
    *,
    explanation: str | None,
    curator_message: str | None = None,
) -> None:
    """Record why a value is unresolved; never touches its id/label or ``mention``.

    A value some earlier validator resolved keeps that identity and state:
    unresolved write-back only applies to values that were never resolved.
    """

    if outcome not in STORED_UNRESOLVED_OUTCOMES:
        raise ResolvableValueError(
            f"lookup_outcome must be one of {STORED_UNRESOLVED_OUTCOMES}, got {outcome!r}"
        )
    if has_resolution_state(value) and value[RESOLUTION_STATE_KEY] == RESOLVED:
        return
    value[RESOLUTION_STATE_KEY] = UNRESOLVED
    value[LOOKUP_OUTCOME_KEY] = outcome
    _write_validator_text(value, explanation, curator_message)


def copy_resolution(source: Mapping[str, Any], target: MutableMapping[str, Any]) -> None:
    """Give a mirror copy its source value's state, outcome and validator text."""

    if source.get(RESOLUTION_STATE_KEY) == RESOLVED:
        target[RESOLUTION_STATE_KEY] = RESOLVED
        target[LOOKUP_OUTCOME_KEY] = OUTCOME_MATCHED
        _write_validator_text(
            target, source.get(VALIDATOR_EXPLANATION_KEY), source.get(VALIDATOR_CURATOR_MESSAGE_KEY),
        )
    elif source.get(RESOLUTION_STATE_KEY) == UNRESOLVED:
        mark_unresolved(
            target,
            source[LOOKUP_OUTCOME_KEY],
            explanation=source.get(VALIDATOR_EXPLANATION_KEY),
            curator_message=source.get(VALIDATOR_CURATOR_MESSAGE_KEY),
        )


# --- Lists of resolvable values ------------------------------------------------


def check_resolvable_list(values: Any, *, identity_keys: Sequence[str]) -> None:
    """Every element of an extractor-proposed list is its own resolvable value."""

    if not isinstance(values, list):
        raise ResolvableValueError(f"A resolvable list must be a list, not {type(values).__name__}")
    for index, item in enumerate(values):
        try:
            check_resolvable_value(item, identity_keys=identity_keys)
        except ResolvableValueError as exc:
            raise ResolvableValueError(f"element {index}: {exc}") from exc


def unresolved_list(
    mentions: Iterable[str],
    *,
    identity_keys: Sequence[str],
    outcome: str = OUTCOME_NOT_VALIDATED,
) -> list[dict[str, Any]]:
    """Stage each proposed mention as its own unresolved element."""

    return [unresolved_value(mention, identity_keys=identity_keys, outcome=outcome) for mention in mentions]


def unresolved_positions(values: Sequence[Any]) -> list[int]:
    """Indexes of list elements whose stored state is not ``resolved``."""

    return [
        index
        for index, item in enumerate(values)
        if not (has_resolution_state(item) and item[RESOLUTION_STATE_KEY] == RESOLVED)
    ]


# --- Read time: effective state, including values stored before the contract ----


def _path_tokens(path: str) -> tuple[str | int, ...] | None:
    if not path:
        return ()
    try:
        return parse_field_path(path)
    except ValueError:
        return None


def validator_materialized_paths(object_metadata: Mapping[str, Any] | None) -> tuple[tuple[str | int, ...], ...]:
    """Payload paths a validator write-back event recorded for one object."""

    if not isinstance(object_metadata, Mapping):
        return ()
    events = object_metadata.get(VALIDATOR_MATERIALIZATION_METADATA_KEY)
    if not isinstance(events, list):
        return ()
    paths: list[tuple[str | int, ...]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        recorded = [*(event.get("materialized_field_paths") or [])]
        original_values = event.get("original_values")
        if isinstance(original_values, Mapping):
            recorded.extend(original_values)
        for path in recorded:
            tokens = _path_tokens(str(path))
            if tokens:
                paths.append(tokens)
    return tuple(paths)


def validator_event_covers(
    object_metadata: Mapping[str, Any] | None,
    value_path: str,
) -> bool:
    """Whether a validator write-back event on this object covers the value at ``value_path``.

    ``value_path`` is the value's own payload path ("" for the object root);
    an event covers it when it wrote the value or one of its keys.
    """

    target = _path_tokens(value_path)
    if target is None:
        return False
    return any(path[: len(target)] == target for path in validator_materialized_paths(object_metadata))


def effective_resolution(
    value: Mapping[str, Any],
    *,
    identity_keys: Sequence[str],
    covered_by_validator: bool,
) -> tuple[str, str]:
    """The (resolution_state, lookup_outcome) a stored value reads with.

    A value with the contract state reads as stored. A value stored before
    the contract (no state, or another ``resolution_state`` word) is resolved
    (``matched``) only when it holds an identity and a validator write-back
    event covers it (``validator_event_covers``); otherwise it is unresolved
    with outcome ``legacy_unverified``. Only vocabulary values come back.
    """

    if has_resolution_state(value):
        state, outcome = value.get(RESOLUTION_STATE_KEY), value.get(LOOKUP_OUTCOME_KEY)
        if state not in RESOLUTION_STATES or outcome not in LOOKUP_OUTCOMES:
            raise ResolvableValueError(
                f"Stored resolution {state!r}/{outcome!r} is outside the controlled vocabulary"
            )
        return str(state), str(outcome)
    if covered_by_validator and any(not _is_empty(value.get(key)) for key in identity_keys):
        return RESOLVED, OUTCOME_MATCHED
    return UNRESOLVED, OUTCOME_LEGACY_UNVERIFIED


def _stored_text(value: Mapping[str, Any], spec: ResolvableSpec) -> str:
    mention = value.get(spec.mention_key)
    if isinstance(mention, str) and mention.strip():
        return mention.strip()
    label = str(value.get(spec.label_key) or "").strip() if spec.label_key else ""
    identifier = str(value.get(spec.id_key) or "").strip() if spec.id_key else ""
    if label and identifier:
        return f"{label} ({identifier})"
    return label or identifier


def effective_value(
    value: Any,
    spec: ResolvableSpec,
    *,
    covered_by_validator: bool,
) -> Any:
    """A read-time copy of a resolvable value with its effective state written in.

    Values with the contract state come back unchanged. A legacy value reads
    with the explanation "Recorded before validation tracking; not verified."
    When not verified it reads as unresolved/``legacy_unverified``: its
    identity keys are emptied and its stored text becomes paper wording
    labelled "(legacy, unverified)". Nothing is written back to storage.
    """

    if not isinstance(value, Mapping) or has_resolution_state(value):
        return value
    state, outcome = effective_resolution(
        value, identity_keys=spec.identity_keys, covered_by_validator=covered_by_validator,
    )
    annotated = dict(value)
    annotated[RESOLUTION_STATE_KEY] = state
    annotated[LOOKUP_OUTCOME_KEY] = outcome
    annotated[VALIDATOR_EXPLANATION_KEY] = LEGACY_EXPLANATION
    if state == UNRESOLVED:
        stored = _stored_text(value, spec)
        for key in spec.identity_keys:
            annotated[key] = None
        annotated[spec.mention_key] = f"{stored} {LEGACY_UNVERIFIED_SUFFIX}" if stored else None
    return annotated


def effective_payload(
    payload: Mapping[str, Any],
    resolvable_fields: Mapping[str, ResolvableSpec],
    *,
    object_metadata: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    """A read-time copy of one object's payload with every declared resolvable value's state.

    ``resolvable_fields`` maps declared field paths ("" for the object root;
    a list field's elements are each a value) to their spec. Only values
    stored without a state change; the stored payload is never modified.
    """

    if not resolvable_fields:
        return payload
    result: Any = dict(payload)
    for field_path, spec in sorted(resolvable_fields.items(), key=lambda item: len(item[0])):
        tokens = _path_tokens(field_path)
        if tokens is None:
            continue
        result = _annotate_at(result, tokens, (), spec, object_metadata)
    return result


def _format_path(tokens: Sequence[str | int]) -> str:
    text = ""
    for token in tokens:
        if isinstance(token, int):
            text += f"[{token}]"
        else:
            text = f"{text}.{token}" if text else token
    return text


def _annotate_at(
    node: Any,
    remaining: Sequence[str | int],
    walked: tuple[str | int, ...],
    spec: ResolvableSpec,
    object_metadata: Mapping[str, Any] | None,
) -> Any:
    if isinstance(node, list):
        # Declared paths name list fields without indexes; each element is its own value.
        return [
            _annotate_at(item, remaining, (*walked, index), spec, object_metadata)
            for index, item in enumerate(node)
        ]
    if not remaining:
        if not isinstance(node, Mapping):
            return node
        return effective_value(
            node,
            spec,
            covered_by_validator=validator_event_covers(object_metadata, _format_path(walked)),
        )
    if not isinstance(node, Mapping):
        return node
    key = remaining[0]
    if key not in node:
        return node
    updated = dict(node)
    updated[key] = _annotate_at(node[key], remaining[1:], (*walked, key), spec, object_metadata)
    return updated


def _walk(node: Any, tokens: Sequence[str | int]) -> Any:
    for token in tokens:
        if isinstance(token, int):
            if not isinstance(node, list) or token >= len(node):
                return None
            node = node[token]
        elif isinstance(node, Mapping):
            node = node.get(token)
        else:
            return None
    return node


def unresolved_header_text(
    payload: Mapping[str, Any],
    field_path: str,
    *,
    object_metadata: Mapping[str, Any] | None = None,
) -> str | None:
    """Explicit paper wording for a header (row label, summary) naming an unresolved value.

    ``field_path`` names a resolvable value or one of its keys (e.g. a label
    field). Returns None when it names no resolvable value or the value is
    resolved; the caller then shows the stored value. Otherwise returns the
    paper wording labelled "(paper wording)" (or, for a legacy value, its
    stored text labelled "(legacy, unverified)"), and UNRESOLVED when no
    wording was stored. A header never shows paper wording as the item.
    """

    tokens = _path_tokens(field_path)
    if not tokens:
        return None
    named = _walk(payload, tokens)
    if holds_resolution(named):
        target, target_tokens, leaf = named, tokens, None
    elif isinstance(tokens[-1], str) and holds_resolution(parent := _walk(payload, tokens[:-1])):
        target, target_tokens, leaf = parent, tokens[:-1], named
    else:
        return None
    mention = target.get(MENTION_KEY)
    mention = mention.strip() if isinstance(mention, str) and mention.strip() else None
    if has_resolution_state(target):
        if target[RESOLUTION_STATE_KEY] == RESOLVED:
            return None
        return f"{mention} {PAPER_WORDING_SUFFIX}" if mention else UNRESOLVED_DISPLAY
    identity = [leaf] if target is not named else [
        item for key, item in target.items()
        if key not in CONTRACT_KEYS
    ]
    if any(not _is_empty(item) for item in identity) and validator_event_covers(
        object_metadata, _format_path(target_tokens)
    ):
        return None
    stored = mention or (str(leaf).strip() if leaf is not None and not isinstance(leaf, (Mapping, list)) else "")
    return f"{stored} {LEGACY_UNVERIFIED_SUFFIX}" if stored else UNRESOLVED_DISPLAY


__all__ = [
    "CONTRACT_KEYS",
    "LEAF_VALUE_LABELS",
    "LEGACY_EXPLANATION",
    "LEGACY_UNVERIFIED_SUFFIX",
    "LOOKUP_OUTCOMES",
    "LOOKUP_OUTCOME_KEY",
    "LOOKUP_OUTCOME_LABELS",
    "LOOKUP_RESULT_SUFFIX",
    "LookupOutcome",
    "MENTION_KEY",
    "NOT_VALIDATED_EXPLANATION",
    "OUTCOME_AMBIGUOUS",
    "OUTCOME_BLOCKED",
    "OUTCOME_CONFLICT",
    "OUTCOME_INVALID_SCHEMA",
    "OUTCOME_LEGACY_UNVERIFIED",
    "OUTCOME_MATCHED",
    "OUTCOME_MISSING_EXPECTED_RESULT_FIELD",
    "OUTCOME_NOT_FOUND",
    "OUTCOME_NOT_VALIDATED",
    "OUTCOME_REJECTED_CANDIDATES",
    "OUTCOME_TRANSIENT",
    "PAPER_WORDING_SUFFIX",
    "RESOLUTION_STATES",
    "RESOLUTION_STATE_KEY",
    "RESOLUTION_STATE_LABELS",
    "RESOLVED",
    "ResolutionState",
    "ResolvableSpec",
    "ResolvableValueError",
    "STATUS_SUFFIX",
    "STORED_UNRESOLVED_OUTCOMES",
    "UNRESOLVED",
    "UNRESOLVED_DISPLAY",
    "VALIDATOR_CURATOR_MESSAGE_KEY",
    "VALIDATOR_EXPLANATION_KEY",
    "VALIDATOR_EXPLANATION_SUFFIX",
    "VALIDATOR_MATERIALIZATION_METADATA_KEY",
    "VALIDATOR_MESSAGE_SUFFIX",
    "check_resolvable_list",
    "check_resolvable_value",
    "copy_resolution",
    "effective_payload",
    "effective_resolution",
    "effective_value",
    "has_resolution_state",
    "holds_resolution",
    "lookup_outcome_for_failure",
    "mark_resolved",
    "mark_unresolved",
    "resolvable_leaf_header",
    "resolvable_spec_from_display",
    "resolved_value",
    "unresolved_header_text",
    "unresolved_list",
    "unresolved_positions",
    "unresolved_value",
    "validator_event_covers",
    "validator_materialized_paths",
]
