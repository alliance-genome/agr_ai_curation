"""Extracted vs validated values: the shared resolvable-value contract (ALL-1283).

Every validated value (an entity ref, an ontology term, a fixed-choice
selection, or one element of an extractor-proposed ID list) is stored as one
object. It keeps its domain's own id/label keys and adds:

- ``mention``: the paper's wording (or the extractor's chosen text for a
  fixed-choice field). Written once by the builder; nothing overwrites it.
- ``resolution_state``: exactly ``resolved`` or ``unresolved``.
- ``resolution_reason``: None when resolved, otherwise one of
  ``UNRESOLVED_REASONS``.

The invariant: the state is ``resolved`` if and only if a validator (or a
deterministic lookup that is the validation) supplied the identity; an
unresolved value has empty id/label keys. A value the paper never mentions is
absent, which is different from unresolved.

Values stored before this contract carry no contract state (no
``resolution_state``, or another word there without a ``resolution_reason``).
Their effective state is decided at read time (``effective_resolution``): resolved
only when they hold an identity AND a validator write-back event
(``metadata.validator_resolved_value_materialization``) covers their path;
otherwise unresolved with reason ``legacy_unverified``.

Domain builders, the materializer and every display surface use these helpers;
none of them re-implements the rules.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.schemas.domain_envelope import parse_field_path


MENTION_KEY = "mention"
RESOLUTION_STATE_KEY = "resolution_state"
RESOLUTION_REASON_KEY = "resolution_reason"

RESOLVED = "resolved"
UNRESOLVED = "unresolved"
RESOLUTION_STATES = (RESOLVED, UNRESOLVED)

# Validator failure classifications (validator_result_classification.
# validator_failure_classification) are stored as the reason unchanged.
REASON_NOT_FOUND = "not_found"
REASON_AMBIGUOUS = "ambiguous"
REASON_CONFLICT = "conflict"
REASON_BLOCKED = "blocked"
REASON_TRANSIENT = "transient"
REASON_INVALID_SCHEMA = "invalid_schema"
REASON_MISSING_EXPECTED_RESULT_FIELD = "missing_expected_result_field"
VALIDATOR_FAILURE_REASONS = (
    REASON_NOT_FOUND,
    REASON_AMBIGUOUS,
    REASON_CONFLICT,
    REASON_BLOCKED,
    REASON_TRANSIENT,
    REASON_INVALID_SCHEMA,
    REASON_MISSING_EXPECTED_RESULT_FIELD,
)
# No validator ran yet: pending, binding in development, or skipped.
REASON_NOT_VALIDATED = "not_validated"
# Read time only, for values stored before this contract.
REASON_LEGACY_UNVERIFIED = "legacy_unverified"
STORED_UNRESOLVED_REASONS = (*VALIDATOR_FAILURE_REASONS, REASON_NOT_VALIDATED)
UNRESOLVED_REASONS = (*STORED_UNRESOLVED_REASONS, REASON_LEGACY_UNVERIFIED)

# The cell text for an unresolved value on every non-JSON surface.
UNRESOLVED_DISPLAY = "UNRESOLVED"
# Column header suffix for a resolvable value's ``.mention`` leaf.
PAPER_WORDING_SUFFIX = "(paper wording)"
# Appended to a legacy value's stored text, shown as paper wording.
LEGACY_UNVERIFIED_SUFFIX = "(legacy, unverified)"

VALIDATOR_MATERIALIZATION_METADATA_KEY = "validator_resolved_value_materialization"


class ResolvableValueError(ValueError):
    """A resolvable value violates the extracted-vs-validated invariant."""


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
    """Whether a stored value carries the contract's explicit state.

    That is a ``resolution_state`` of ``resolved`` or ``unresolved`` written
    together with its ``resolution_reason`` key. Values stored before the
    contract (including ones with other ``resolution_state`` words) do not.
    """

    return (
        isinstance(value, Mapping)
        and value.get(RESOLUTION_STATE_KEY) in RESOLUTION_STATES
        and RESOLUTION_REASON_KEY in value
    )


def holds_resolution(value: Any) -> bool:
    """Whether a stored mapping is a resolvable value (it has a state or a paper mention)."""

    return isinstance(value, Mapping) and (
        RESOLUTION_STATE_KEY in value or isinstance(value.get(MENTION_KEY), str)
    )


def check_resolvable_value(value: Any, *, identity_keys: Sequence[str]) -> None:
    """Raise ``ResolvableValueError`` unless ``value`` satisfies the invariant.

    ``identity_keys`` are the id/label keys the caller's domain uses.
    """

    if not isinstance(value, Mapping):
        raise ResolvableValueError(f"A resolvable value must be an object, not {type(value).__name__}")
    if not identity_keys:
        raise ResolvableValueError("check_resolvable_value needs the value's id/label keys")
    mention = value.get(MENTION_KEY)
    if MENTION_KEY in value and not (isinstance(mention, str) and mention.strip()):
        raise ResolvableValueError("mention must be the non-empty paper wording")
    state = value.get(RESOLUTION_STATE_KEY)
    reason = value.get(RESOLUTION_REASON_KEY)
    has_identity = any(not _is_empty(value.get(key)) for key in identity_keys)
    if state == RESOLVED:
        if reason is not None:
            raise ResolvableValueError(f"A resolved value has no resolution_reason, got {reason!r}")
        if not has_identity:
            raise ResolvableValueError(
                "A resolved value needs the identity a validator supplied "
                f"(one of {', '.join(identity_keys)})"
            )
        return
    if state == UNRESOLVED:
        if reason not in STORED_UNRESOLVED_REASONS:
            raise ResolvableValueError(
                f"An unresolved value needs a resolution_reason in {STORED_UNRESOLVED_REASONS}, got {reason!r}"
            )
        if has_identity:
            raise ResolvableValueError(
                "An unresolved value never carries an identity; "
                f"{', '.join(key for key in identity_keys if not _is_empty(value.get(key)))} must be empty"
            )
        return
    raise ResolvableValueError(f"resolution_state must be one of {RESOLUTION_STATES}, got {state!r}")


def _required_mention(mention: Any) -> str:
    if not isinstance(mention, str) or not mention.strip():
        raise ResolvableValueError("A resolvable value needs its paper wording (mention)")
    return mention.strip()


def resolved_value(
    mention: str,
    identity: Mapping[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    """A value whose identity a validator or deterministic lookup supplied.

    ``identity`` holds the domain's id/label keys (e.g. ``{"curie": ..., "name": ...}``);
    ``extra`` holds other domain keys kept alongside (e.g. ``vocabulary``).
    """

    if not identity:
        raise ResolvableValueError("A resolved value needs its identity keys")
    value = {
        **extra,
        **dict(identity),
        MENTION_KEY: _required_mention(mention),
        RESOLUTION_STATE_KEY: RESOLVED,
        RESOLUTION_REASON_KEY: None,
    }
    check_resolvable_value(value, identity_keys=tuple(identity))
    return value


def unresolved_value(
    mention: str,
    *,
    identity_keys: Sequence[str],
    reason: str = REASON_NOT_VALIDATED,
    **extra: Any,
) -> dict[str, Any]:
    """A staged value no validator has resolved; its identity keys are written as null."""

    value = {
        **extra,
        **{key: None for key in identity_keys},
        MENTION_KEY: _required_mention(mention),
        RESOLUTION_STATE_KEY: UNRESOLVED,
        RESOLUTION_REASON_KEY: reason,
    }
    check_resolvable_value(value, identity_keys=identity_keys)
    return value


def mark_resolved(value: MutableMapping[str, Any], identity: Mapping[str, Any]) -> None:
    """Write a validator-supplied identity and the resolved state; ``mention`` is untouched."""

    if not identity or all(_is_empty(item) for item in identity.values()):
        raise ResolvableValueError("mark_resolved needs the identity a validator supplied")
    value.update(identity)
    value[RESOLUTION_STATE_KEY] = RESOLVED
    value[RESOLUTION_REASON_KEY] = None
    check_resolvable_value(value, identity_keys=tuple(identity))


def mark_unresolved(value: MutableMapping[str, Any], reason: str) -> None:
    """Record why a value is unresolved; never touches its id/label or ``mention``.

    A value some earlier validator resolved keeps that identity and state:
    unresolved write-back only applies to values that were never resolved.
    """

    if reason not in STORED_UNRESOLVED_REASONS:
        raise ResolvableValueError(
            f"resolution_reason must be one of {STORED_UNRESOLVED_REASONS}, got {reason!r}"
        )
    if has_resolution_state(value) and value[RESOLUTION_STATE_KEY] == RESOLVED:
        return
    value[RESOLUTION_STATE_KEY] = UNRESOLVED
    value[RESOLUTION_REASON_KEY] = reason


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
    reason: str = REASON_NOT_VALIDATED,
) -> list[dict[str, Any]]:
    """Stage each proposed mention as its own unresolved element."""

    return [unresolved_value(mention, identity_keys=identity_keys, reason=reason) for mention in mentions]


def unresolved_positions(values: Sequence[Any]) -> list[int]:
    """Indexes of list elements whose stored state is not ``resolved``."""

    return [
        index
        for index, item in enumerate(values)
        if not (isinstance(item, Mapping) and item.get(RESOLUTION_STATE_KEY) == RESOLVED)
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
) -> tuple[str, str | None]:
    """The (state, reason) a stored value reads with.

    A value with the contract's explicit state reads as stored. A value
    stored before the contract (no state, or another ``resolution_state``
    word) is resolved only when it holds an identity and a validator
    write-back event covers it (``validator_event_covers``); otherwise it is
    unresolved with reason ``legacy_unverified``.
    """

    if has_resolution_state(value):
        return str(value.get(RESOLUTION_STATE_KEY)), value.get(RESOLUTION_REASON_KEY)
    if covered_by_validator and any(not _is_empty(value.get(key)) for key in identity_keys):
        return RESOLVED, None
    return UNRESOLVED, REASON_LEGACY_UNVERIFIED


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

    Values with an explicit state come back unchanged. A legacy value that is
    not verified reads as unresolved/``legacy_unverified``: its identity keys
    are emptied and its stored text becomes paper wording labelled
    "(legacy, unverified)". Nothing is written back to storage.
    """

    if not isinstance(value, Mapping) or has_resolution_state(value):
        return value
    state, reason = effective_resolution(
        value, identity_keys=spec.identity_keys, covered_by_validator=covered_by_validator,
    )
    annotated = dict(value)
    annotated[RESOLUTION_STATE_KEY] = state
    annotated[RESOLUTION_REASON_KEY] = reason
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
        if key not in (MENTION_KEY, RESOLUTION_STATE_KEY, RESOLUTION_REASON_KEY)
    ]
    if any(not _is_empty(item) for item in identity) and validator_event_covers(
        object_metadata, _format_path(target_tokens)
    ):
        return None
    stored = mention or (str(leaf).strip() if leaf is not None and not isinstance(leaf, (Mapping, list)) else "")
    return f"{stored} {LEGACY_UNVERIFIED_SUFFIX}" if stored else UNRESOLVED_DISPLAY


__all__ = [
    "LEGACY_UNVERIFIED_SUFFIX",
    "MENTION_KEY",
    "PAPER_WORDING_SUFFIX",
    "REASON_AMBIGUOUS",
    "REASON_BLOCKED",
    "REASON_CONFLICT",
    "REASON_INVALID_SCHEMA",
    "REASON_LEGACY_UNVERIFIED",
    "REASON_MISSING_EXPECTED_RESULT_FIELD",
    "REASON_NOT_FOUND",
    "REASON_NOT_VALIDATED",
    "REASON_TRANSIENT",
    "RESOLUTION_REASON_KEY",
    "RESOLUTION_STATES",
    "RESOLUTION_STATE_KEY",
    "RESOLVED",
    "ResolvableSpec",
    "ResolvableValueError",
    "STORED_UNRESOLVED_REASONS",
    "UNRESOLVED",
    "UNRESOLVED_DISPLAY",
    "UNRESOLVED_REASONS",
    "VALIDATOR_FAILURE_REASONS",
    "VALIDATOR_MATERIALIZATION_METADATA_KEY",
    "check_resolvable_list",
    "check_resolvable_value",
    "effective_payload",
    "effective_resolution",
    "effective_value",
    "has_resolution_state",
    "holds_resolution",
    "mark_resolved",
    "mark_unresolved",
    "resolvable_spec_from_display",
    "resolved_value",
    "unresolved_header_text",
    "unresolved_list",
    "unresolved_positions",
    "unresolved_value",
    "validator_event_covers",
    "validator_materialized_paths",
]
