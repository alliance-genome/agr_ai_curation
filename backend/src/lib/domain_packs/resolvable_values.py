"""Extracted vs validated values: the shared resolvable-value contract (ALL-1283).

Every validated value (an entity ref, an ontology term, a fixed-choice
selection, or one element of an extractor-proposed ID list) is stored as one
object. It keeps its domain's own id/label keys and adds:

- ``mention``: the paper's wording (or the extractor's chosen text for a
  fixed-choice field). Written once by the builder; nothing overwrites it.
- ``resolution_state``: the closed vocabulary ``ResolutionState``.
- ``lookup_outcome``: the closed vocabulary ``LookupOutcome``; always set,
  ``matched`` (a validator) or ``curator_override`` (a curator) exactly
  when the value is resolved.
- ``validator_explanation``: the validator's own explanation (free text,
  nullable), and ``validator_curator_message``: its curator message, kept
  apart from the explanation.

The invariant: the state is ``resolved`` if and only if a validator (or a
deterministic lookup that is the validation), or a curator override,
supplied the identity, if and only if the lookup outcome is ``matched`` or
``curator_override``; an unresolved value has empty id/label keys. A
curator override also records ``curator_override`` (who and when) and wins
over later validator runs (``apply_curator_identity``). A value the paper never mentions is absent, which is different
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

import copy
import logging
import math
import re
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from src.lib.domain_packs.validator_result_classification import ValidatorFailureClassification
from src.schemas.domain_envelope import parse_field_path


MENTION_KEY = "mention"
RESOLUTION_STATE_KEY = "resolution_state"
LOOKUP_OUTCOME_KEY = "lookup_outcome"
VALIDATOR_EXPLANATION_KEY = "validator_explanation"
VALIDATOR_CURATOR_MESSAGE_KEY = "validator_curator_message"
# Who overrode the value's validation and when (informational, never a validator input).
CURATOR_OVERRIDE_KEY = "curator_override"
# Every key the contract adds to a value; the rest are the domain's own keys.
CONTRACT_KEYS = (
    MENTION_KEY,
    RESOLUTION_STATE_KEY,
    LOOKUP_OUTCOME_KEY,
    VALIDATOR_EXPLANATION_KEY,
    VALIDATOR_CURATOR_MESSAGE_KEY,
    CURATOR_OVERRIDE_KEY,
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
    # A curator set the identity; it wins over later validator runs.
    CURATOR_OVERRIDE = "curator_override"


RESOLVED = ResolutionState.RESOLVED.value
UNRESOLVED = ResolutionState.UNRESOLVED.value
RESOLUTION_STATES = tuple(state.value for state in ResolutionState)
LOOKUP_OUTCOMES = tuple(outcome.value for outcome in LookupOutcome)

OUTCOME_MATCHED = LookupOutcome.MATCHED.value
OUTCOME_CURATOR_OVERRIDE = LookupOutcome.CURATOR_OVERRIDE.value
# The outcomes of a resolved value.
RESOLVED_OUTCOMES = (OUTCOME_MATCHED, OUTCOME_CURATOR_OVERRIDE)
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
# Only these outcomes may overrule a value that already reads as resolved: the
# lookup ran and decided against it. The rest (a transient error, invalid or
# incomplete validator output, a blocked lookup, an allowed-term violation)
# add a finding but never touch a resolved value (ALL-1283 review H2).
DECISIVE_OUTCOMES = (
    LookupOutcome.NOT_FOUND.value,
    LookupOutcome.AMBIGUOUS.value,
    LookupOutcome.CONFLICT.value,
    LookupOutcome.REJECTED_CANDIDATES.value,
)
STORED_UNRESOLVED_OUTCOMES = tuple(
    outcome.value
    for outcome in LookupOutcome
    if outcome
    not in (LookupOutcome.MATCHED, LookupOutcome.CURATOR_OVERRIDE, LookupOutcome.LEGACY_UNVERIFIED)
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
    OUTCOME_CURATOR_OVERRIDE: "Curator override",
}
RESOLUTION_STATE_LABELS: dict[str, str] = {RESOLVED: "Resolved", UNRESOLVED: "Unresolved"}

# A validator that overrules a resolved value keeps the old identity under
# these keys: informational only (never the value, never exported, never a
# validator input). ``proposed_*`` stays the extractor's own proposal.
OVERRULED_KEY_PREFIX = "overruled_"
EXTRACTOR_PROPOSAL_PREFIX = "proposed_"

NOT_VALIDATED_EXPLANATION = "Not validated yet."
LEGACY_EXPLANATION = "Recorded before validation tracking; not verified."
INVALID_RECORD_EXPLANATION = "The stored validation record is invalid; treated as unresolved."

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
# Appended to the stored text of a value whose stored record breaks the contract.
INVALID_RECORD_SUFFIX = "(invalid record, unverified)"

VALIDATOR_MATERIALIZATION_METADATA_KEY = "validator_resolved_value_materialization"

_log = logging.getLogger(__name__)


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
    # Further keys only a validator fills (e.g. a taxon); part of the identity.
    validated_keys: tuple[str, ...] = ()
    # Payload paths whose validator coverage also covers this value: the
    # sources of a declared mirror copy (``materializes_to_field_paths``).
    covered_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not (self.id_key or self.label_key):
            raise ResolvableValueError("A resolvable value needs an id key or a label key")

    @property
    def identity_keys(self) -> tuple[str, ...]:
        """Every key a validator supplies: id, label and the declared ``validated`` keys."""

        return tuple(key for key in (self.id_key, self.label_key, *self.validated_keys) if key)


def resolvable_spec_from_display(display: Mapping[str, Any] | None) -> ResolvableSpec | None:
    """The resolvable spec a pack display declaration names, or None.

    A display spec declares a resolvable value by naming a ``mention`` role
    next to its ``label``/``id`` roles (``src.lib.flows.value_display``); an
    optional ``validated`` list names further keys only a validator fills.
    """

    if not isinstance(display, Mapping) or not display.get("mention"):
        return None
    return ResolvableSpec(
        id_key=display.get("id") or None,
        label_key=display.get("label") or None,
        mention_key=str(display["mention"]),
        validated_keys=tuple(str(key) for key in display.get("validated") or ()),
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


def _optional_text(value: Any, key: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ResolvableValueError(f"{key} must be text or null, got {type(value).__name__}")


def _check_contract_fields(value: Any) -> tuple[str, str]:
    """The vocabulary and state/outcome rules that need no domain keys; returns (state, outcome)."""

    if not isinstance(value, Mapping):
        raise ResolvableValueError(f"A resolvable value must be an object, not {type(value).__name__}")
    mention = value.get(MENTION_KEY)
    # No paper wording (absent or null) marks a container stored before the
    # contract: the builder helpers always write a non-empty mention and the
    # validator write-back never touches it, so such a container can be
    # re-validated any number of times.
    if mention is not None and not (isinstance(mention, str) and mention.strip()):
        raise ResolvableValueError("mention must be the non-empty paper wording")
    _optional_text(value.get(VALIDATOR_EXPLANATION_KEY), VALIDATOR_EXPLANATION_KEY)
    _optional_text(value.get(VALIDATOR_CURATOR_MESSAGE_KEY), VALIDATOR_CURATOR_MESSAGE_KEY)
    state = value.get(RESOLUTION_STATE_KEY)
    outcome = value.get(LOOKUP_OUTCOME_KEY)
    if state not in RESOLUTION_STATES:
        raise ResolvableValueError(f"resolution_state must be one of {RESOLUTION_STATES}, got {state!r}")
    if outcome not in LOOKUP_OUTCOMES:
        raise ResolvableValueError(f"lookup_outcome must be one of {LOOKUP_OUTCOMES}, got {outcome!r}")
    if state == RESOLVED and outcome not in RESOLVED_OUTCOMES:
        raise ResolvableValueError(
            f"A resolved value's lookup_outcome is one of {RESOLVED_OUTCOMES}, got {outcome!r}"
        )
    override = value.get(CURATOR_OVERRIDE_KEY)
    if outcome == OUTCOME_CURATOR_OVERRIDE:
        if not (
            isinstance(override, Mapping)
            and override.get("actor_id")
            and override.get("actor_display_name")
            and override.get("at")
        ):
            raise ResolvableValueError(
                "A curator override records who (actor_id, actor_display_name) and when (at)"
            )
    elif CURATOR_OVERRIDE_KEY in value:
        raise ResolvableValueError("Only a curator_override value records a curator override")
    if state == UNRESOLVED and outcome not in STORED_UNRESOLVED_OUTCOMES:
        raise ResolvableValueError(
            f"An unresolved value's lookup_outcome is one of {STORED_UNRESOLVED_OUTCOMES}, got {outcome!r}"
        )
    return str(state), str(outcome)


def check_resolvable_value(value: Any, *, identity_keys: Sequence[str]) -> None:
    """Raise ``ResolvableValueError`` unless ``value`` satisfies the invariant.

    ``identity_keys`` are the id/label keys the caller's domain uses. The state
    and outcome must be values of their closed vocabularies.
    """

    if not identity_keys:
        raise ResolvableValueError("check_resolvable_value needs the value's id/label keys")
    state, _outcome = _check_contract_fields(value)
    has_identity = any(not _is_empty(value.get(key)) for key in identity_keys)
    if state == RESOLVED and not has_identity:
        raise ResolvableValueError(
            "A resolved value needs the identity a validator supplied "
            f"(one of {', '.join(identity_keys)})"
        )
    if state == UNRESOLVED and has_identity:
        raise ResolvableValueError(
            "An unresolved value never carries an identity; "
            f"{', '.join(key for key in identity_keys if not _is_empty(value.get(key)))} must be empty"
        )


def stored_state_problem(value: Any, *, identity_keys: Sequence[str] = ()) -> str | None:
    """Why a value stored with the contract state breaks the contract, or None.

    With ``identity_keys`` the whole invariant is checked; without them, the
    vocabularies and the state/outcome pairing. Read paths use this instead of
    raising, so one bad stored value never fails a whole envelope read.
    """

    try:
        if identity_keys:
            check_resolvable_value(value, identity_keys=identity_keys)
        else:
            _check_contract_fields(value)
    except ResolvableValueError as exc:
        return str(exc)
    return None


def is_curator_override(value: Any) -> bool:
    """Whether a curator set this value's identity (a valid ``curator_override`` state)."""

    return (
        has_resolution_state(value)
        and value[LOOKUP_OUTCOME_KEY] == OUTCOME_CURATOR_OVERRIDE
        and stored_state_problem(value) is None
    )


def is_resolved(value: Any, *, identity_keys: Sequence[str] = ()) -> bool:
    """Whether a stored value reads as resolved: a valid contract state of ``resolved``."""

    return (
        has_resolution_state(value)
        and value[RESOLUTION_STATE_KEY] == RESOLVED
        and stored_state_problem(value, identity_keys=identity_keys) is None
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
    """A value whose identity a validator, or a fixed in-code mapping table, supplied.

    Extraction never searches for an identity: it stages ``unresolved_value``,
    except for a value its pack declares as filled from a fixed in-code
    mapping table (``EXTRACTION_MAPPING_KEY``; see ``extraction_value_problems``).
    ``identity`` holds the domain's id/label keys (e.g. ``{"curie": ..., "name": ...}``);
    ``extra`` holds other domain keys kept alongside (e.g. ``vocabulary``);
    ``explanation`` says how it matched, when the caller has it.
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
    identity_keys: Sequence[str] = (),
) -> None:
    """Write a validator-supplied identity, the resolved state and the validator's own words.

    ``mention`` is untouched. ``explanation`` and ``curator_message`` come from
    the validator result and are stored apart, never merged. Any of the
    value's ``identity_keys`` the validator did not supply is cleared, so no
    stale label (or other key) sits beside the new identity, and any
    overruled identity is dropped. The extractor's own proposals
    (``proposed_*``) are validator inputs and are always kept.
    """

    if not identity or all(_is_empty(item) for item in identity.values()):
        raise ResolvableValueError("mark_resolved needs the identity a validator supplied")
    if is_curator_override(value):
        # A curator override wins over later validator runs.
        return
    for key in identity_keys:
        if key not in identity and key in value:
            value[key] = None
    value.update(identity)
    _drop_overruled(value)
    value[RESOLUTION_STATE_KEY] = RESOLVED
    value[LOOKUP_OUTCOME_KEY] = OUTCOME_MATCHED
    _write_validator_text(value, explanation, curator_message)
    check_resolvable_value(value, identity_keys=tuple(identity))


def overruled_key(key: str) -> str:
    """The key that keeps an identity a validator overruled (``curie`` -> ``overruled_curie``)."""

    return f"{OVERRULED_KEY_PREFIX}{key}"


def _drop_overruled(value: MutableMapping[str, Any]) -> None:
    """A fresh validator decision replaces any earlier overruled identity."""

    for key in [key for key in value if isinstance(key, str) and key.startswith(OVERRULED_KEY_PREFIX)]:
        del value[key]


def without_overruled(value: Any) -> Any:
    """A copy without overruled identities, for every reader that is not an audit view.

    Overruled keys are informational only: they are never the value, never
    exported and never a validator input.
    """

    if isinstance(value, list):
        return [without_overruled(item) for item in value]
    if isinstance(value, Mapping):
        return {
            key: without_overruled(item)
            for key, item in value.items()
            if not (isinstance(key, str) and key.startswith(OVERRULED_KEY_PREFIX))
        }
    return value


def mark_unresolved(
    value: MutableMapping[str, Any],
    outcome: str,
    *,
    explanation: str | None,
    curator_message: str | None = None,
    identity_keys: Sequence[str] = (),
) -> None:
    """Record why a value is unresolved, with the validator's own words; ``mention`` is untouched.

    The validator is the authority, but only a decisive outcome
    (``DECISIVE_OUTCOMES``) overrules a value that reads as resolved (e.g. a
    builder's deterministic lookup or an earlier validation): its identity is
    kept only as informational ``overruled_<key>`` keys and its
    ``identity_keys`` are cleared, so the invariant holds; the extractor's own
    ``proposed_*`` keys are never touched. A non-decisive outcome (e.g. a
    transient lookup error) leaves a resolved value exactly as it was. A value
    that never resolved takes the unresolved state with any outcome and keeps
    its id/label as stored (empty for a contract value).
    """

    if outcome not in STORED_UNRESOLVED_OUTCOMES:
        raise ResolvableValueError(
            f"lookup_outcome must be one of {STORED_UNRESOLVED_OUTCOMES}, got {outcome!r}"
        )
    if is_curator_override(value):
        # A curator override wins over later validator runs.
        return
    if is_resolved(value):
        if outcome not in DECISIVE_OUTCOMES:
            return
        if not identity_keys:
            raise ResolvableValueError("Unresolving a resolved value needs its identity keys")
        _overrule_identity(value, identity_keys)
    elif not has_resolution_state(value) and any(not _is_empty(value.get(key)) for key in identity_keys):
        # A value stored before the contract holds an identity the legacy rule
        # may read as validated. A decisive outcome sets it aside like an
        # overruled one; a non-decisive one (the lookup did not decide) leaves
        # the value untouched, and the finding records the failure.
        if outcome not in DECISIVE_OUTCOMES:
            return
        _overrule_identity(value, identity_keys)
    value[RESOLUTION_STATE_KEY] = UNRESOLVED
    value[LOOKUP_OUTCOME_KEY] = outcome
    _write_validator_text(value, explanation, curator_message)
    _check_contract_fields(value)


def _overrule_identity(value: MutableMapping[str, Any], identity_keys: Sequence[str]) -> None:
    for key in identity_keys:
        if not _is_empty(value.get(key)):
            value[overruled_key(key)] = value[key]
        if key in value:
            value[key] = None


def copy_resolution(
    source: Mapping[str, Any],
    target: MutableMapping[str, Any],
    *,
    identity_keys: Sequence[str] = (),
) -> None:
    """Give a mirror copy its source value's state, outcome and validator text.

    ``identity_keys`` are the mirror's own declared identity keys; the
    source's own keys are added to them.
    """

    if is_curator_override(target) and not is_curator_override(source):
        # A curator override on the copy wins over a validator's write to its source.
        return
    if source.get(RESOLUTION_STATE_KEY) == RESOLVED:
        target[RESOLUTION_STATE_KEY] = RESOLVED
        target[LOOKUP_OUTCOME_KEY] = source[LOOKUP_OUTCOME_KEY]
        _write_validator_text(
            target, source.get(VALIDATOR_EXPLANATION_KEY), source.get(VALIDATOR_CURATOR_MESSAGE_KEY),
        )
        if is_curator_override(source):
            target[CURATOR_OVERRIDE_KEY] = copy.deepcopy(source[CURATOR_OVERRIDE_KEY])
        else:
            target.pop(CURATOR_OVERRIDE_KEY, None)
        _drop_overruled(target)
        _check_contract_fields(target)
    elif source.get(RESOLUTION_STATE_KEY) == UNRESOLVED:
        mark_unresolved(
            target,
            source[LOOKUP_OUTCOME_KEY],
            explanation=source.get(VALIDATOR_EXPLANATION_KEY),
            curator_message=source.get(VALIDATOR_CURATOR_MESSAGE_KEY),
            # The mirror's declared keys plus the source's own keys (never its proposals).
            identity_keys=tuple(dict.fromkeys([
                *identity_keys,
                *(
                    key.removeprefix(OVERRULED_KEY_PREFIX)
                    for key in source
                    if isinstance(key, str)
                    and key not in CONTRACT_KEYS
                    and not key.startswith(EXTRACTOR_PROPOSAL_PREFIX)
                ),
            ])),
        )


# --- Curator validation override ------------------------------------------------

# Object metadata key of the audit trail of curator overrides.
CURATOR_OVERRIDE_METADATA_KEY = "curator_resolution_overrides"


def _resolution_snapshot(value: Mapping[str, Any], identity_keys: Sequence[str]) -> dict[str, Any]:
    keys = (
        *identity_keys,
        RESOLUTION_STATE_KEY,
        LOOKUP_OUTCOME_KEY,
        VALIDATOR_EXPLANATION_KEY,
        VALIDATOR_CURATOR_MESSAGE_KEY,
    )
    snapshot = {key: copy.deepcopy(value[key]) for key in keys if key in value}
    snapshot.update(
        (key, copy.deepcopy(item))
        for key, item in value.items()
        if isinstance(key, str) and key.startswith(OVERRULED_KEY_PREFIX)
    )
    return snapshot


_INTEGER_TEXT = re.compile(r"[+-]?[0-9]+")
_NUMBER_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def typed_identity_input(value: Any, *, value_type: str, label: str) -> Any:
    """A curator's entry for a numeric identity field (``integer``/``number``), as that number.

    Text is parsed (the review screen sends what the curator typed); empty
    text is no value. Anything else that is not such a number is rejected
    with a curator-facing message naming the field (``label``). Other value
    types pass through unchanged.
    """

    if value_type not in ("integer", "number") or value is None:
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if value_type == "integer" and _INTEGER_TEXT.fullmatch(text):
            return int(text)
        if value_type == "number" and _NUMBER_TEXT.fullmatch(text):
            number = float(text)
            return int(number) if number.is_integer() and _INTEGER_TEXT.fullmatch(text) else number
    elif not isinstance(value, bool) and (
        isinstance(value, int) or (value_type == "number" and isinstance(value, float) and math.isfinite(value))
    ):
        return value
    kind = "a whole number" if value_type == "integer" else "a number"
    raise ResolvableValueError(f"Enter {kind} for the {_curator_label(label)}.")


def _curator_label(label: str) -> str:
    """A field's display name in running text: "Reference ID" -> "reference ID"."""

    text = label.strip()
    if len(text) > 1 and text[0].isupper() and not text[1].isupper():
        return text[0].lower() + text[1:]
    return text


def _override_incomplete_message(missing: Sequence[str], id_key: str | None, label_key: str | None) -> str:
    """The curator-facing message for an override that leaves identity keys out, in curator words."""

    if id_key and label_key and set(missing) == {id_key, label_key}:
        return "Enter both the identifier and the name for a curator override."
    names = [
        "the identifier" if key == id_key else "the name" if key == label_key else f"the {key.replace('_', ' ')}"
        for key in missing
    ]
    listed = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    return f"Enter {listed} for a curator override."


def apply_curator_identity(
    value: MutableMapping[str, Any],
    edits: Mapping[str, Any],
    *,
    identity_keys: Sequence[str],
    id_key: str | None,
    label_key: str | None,
    actor_id: str,
    actor_display_name: str,
    at: str,
) -> dict[str, Any]:
    """A curator's edit of a resolvable value's identity keys: a validation override.

    ``edits`` maps edited identity keys to their new values. The value becomes
    resolved with ``lookup_outcome`` ``curator_override`` and a
    ``curator_override`` record (who, when, and the state before the first
    override); a validator identity it replaces moves to ``overruled_*``.
    ``mention`` and the validator's explanation are kept. A first override
    names every identity key: the declared ``id_key`` and ``label_key``
    filled, and each further (validated) key given, possibly null; nothing
    it leaves out is silently emptied. A later edit of the override may name
    a subset; the declared id and label stay filled. Otherwise it is
    rejected with a message naming what is missing. Clearing the whole
    identity reverts to unresolved (the
    validator's last unresolved outcome, else ``not_validated``); entering
    the identity the value had before the override restores that state.
    Returns the audit record.
    """

    unknown = sorted(set(edits) - set(identity_keys))
    if unknown:
        raise ResolvableValueError(f"Only identity keys take a curator override, not {', '.join(unknown)}")
    required = tuple(key for key in (id_key, label_key) if key)
    if not required or not set(required) <= set(identity_keys):
        raise ResolvableValueError("A curator override needs the value's declared id or label key")
    if not actor_id or not actor_display_name or not at:
        raise ResolvableValueError(
            "A curator override records who (actor_id, actor_display_name) and when (at)"
        )
    overridden = is_curator_override(value)
    before = _resolution_snapshot(value, identity_keys)
    base = value[CURATOR_OVERRIDE_KEY]["previous"] if overridden else before
    identity = {
        key: edits[key] if key in edits else (value.get(key) if overridden else None)
        for key in identity_keys
    }

    if all(_is_empty(item) for item in identity.values()):
        # The curator cleared the identity: the override is withdrawn.
        for key in identity_keys:
            value[key] = None
        previous_outcome = base.get(LOOKUP_OUTCOME_KEY)
        value[RESOLUTION_STATE_KEY] = UNRESOLVED
        value[LOOKUP_OUTCOME_KEY] = (
            previous_outcome if previous_outcome in STORED_UNRESOLVED_OUTCOMES else OUTCOME_NOT_VALIDATED
        )
        value.pop(CURATOR_OVERRIDE_KEY, None)
        action = "cleared"
    elif all(identity[key] == base.get(key) for key in identity_keys) and has_resolution_state(base) and (
        base.get(LOOKUP_OUTCOME_KEY) != OUTCOME_CURATOR_OVERRIDE
    ):
        # The curator entered the identity the value had before: restore that state.
        for key in [key for key in value if isinstance(key, str) and key.startswith(OVERRULED_KEY_PREFIX)]:
            del value[key]
        for key in (*identity_keys, VALIDATOR_EXPLANATION_KEY, VALIDATOR_CURATOR_MESSAGE_KEY):
            value.pop(key, None)
        value.update(copy.deepcopy(base))
        value.pop(CURATOR_OVERRIDE_KEY, None)
        action = "restored"
    else:
        missing = [
            key for key in identity_keys
            if (key in required and _is_empty(identity[key])) or (not overridden and key not in edits)
        ]
        if any(key in required for key in missing):
            # The identifier and the name are asked for together.
            missing = [*required, *(key for key in missing if key not in required)]
        if missing:
            raise ResolvableValueError(_override_incomplete_message(missing, id_key, label_key))
        if not overridden:
            _overrule_identity(value, identity_keys)
        value.update(identity)
        value[RESOLUTION_STATE_KEY] = RESOLVED
        value[LOOKUP_OUTCOME_KEY] = OUTCOME_CURATOR_OVERRIDE
        value[CURATOR_OVERRIDE_KEY] = {
            "actor_id": actor_id,
            "actor_display_name": actor_display_name,
            "at": at,
            "previous": copy.deepcopy(base),
        }
        action = "override"
    _check_contract_fields(value)
    return {
        "action": action,
        "actor_id": actor_id,
        "actor_display_name": actor_display_name,
        "at": at,
        "previous": before,
        "identity": {key: copy.deepcopy(value.get(key)) for key in identity_keys},
    }


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
        if not is_resolved(item)
    ]


# --- Read time: effective state, including values stored before the contract ----


def _path_tokens(path: str) -> tuple[str | int, ...] | None:
    if not path:
        return ()
    try:
        return parse_field_path(path)
    except ValueError:
        return None


# Write-back events that count as validator coverage: packaged domain-pack
# bindings (materialization.py) and closed-profile validator mappings
# (profile_materialization.py), which record their payload paths as
# ``field_paths`` (e.g. ``attributes.gene.gene_id``).
PROFILE_VALIDATOR_MATERIALIZATION_METADATA_KEY = "profile_validator_materialization"
# An event path segment ``[]`` names every element of a list.
_ANY_INDEX = -1


def _event_path_tokens(path: str) -> tuple[str | int, ...] | None:
    """Tokens of an event's recorded payload path; ``[]`` becomes an any-element index."""

    if "[]" not in path:
        return _path_tokens(path)
    tokens: list[str | int] = []
    for segment in path.split("."):
        key, _, brackets = segment.partition("[")
        if not key:
            return None
        tokens.append(key)
        for bracket in filter(None, f"[{brackets}".split("[")) if brackets else ():
            index = bracket.rstrip("]")
            if index == "":
                tokens.append(_ANY_INDEX)
            elif index.isdigit():
                tokens.append(int(index))
            else:
                return None
    return tuple(tokens)


def validator_materialized_paths(object_metadata: Mapping[str, Any] | None) -> tuple[tuple[str | int, ...], ...]:
    """Payload paths validator write-back events recorded for one object.

    Packaged binding events count the paths they wrote
    (``materialized_field_paths``); closed-profile validator events count
    their ``field_paths``. An event's ``original_values`` records what the
    expected-result paths held before, written or not, so it counts only for
    an event recorded before ``materialized_field_paths`` existed, which was
    written only for resolved values it wrote.
    """

    if not isinstance(object_metadata, Mapping):
        return ()
    recorded: list[Any] = []
    events = object_metadata.get(VALIDATOR_MATERIALIZATION_METADATA_KEY)
    for event in events if isinstance(events, list) else ():
        if not isinstance(event, Mapping):
            continue
        if "materialized_field_paths" in event:
            recorded.extend(event.get("materialized_field_paths") or [])
            continue
        original_values = event.get("original_values")
        if isinstance(original_values, Mapping):
            recorded.extend(original_values)
    profile_events = object_metadata.get(PROFILE_VALIDATOR_MATERIALIZATION_METADATA_KEY)
    for event in profile_events if isinstance(profile_events, list) else ():
        if isinstance(event, Mapping) and isinstance(event.get("field_paths"), list):
            recorded.extend(event["field_paths"])
    paths: list[tuple[str | int, ...]] = []
    for path in recorded:
        tokens = _event_path_tokens(str(path))
        if tokens:
            paths.append(tokens)
    return tuple(paths)


def _covers(
    path: Sequence[str | int],
    target: Sequence[str | int],
    identity_keys: Sequence[str] = (),
) -> bool:
    """Whether a recorded write path covers the value at ``target``.

    It covers the value when it wrote the whole value, one of its identity
    keys (any key when none are given, except at the object root), or the
    whole list the value is an element (or part of an element) of. A write
    elsewhere on the object never covers the object root itself.
    """

    shared = min(len(path), len(target))
    if not all(
        recorded == wanted or (recorded == _ANY_INDEX and isinstance(wanted, int))
        for recorded, wanted in zip(path[:shared], target[:shared])
    ):
        return False
    if len(path) < len(target):
        return isinstance(target[len(path)], int)
    if len(path) == len(target):
        return bool(target)
    if identity_keys:
        return path[len(target)] in identity_keys
    return bool(target)


def validator_event_covers(
    object_metadata: Mapping[str, Any] | None,
    value_path: str,
    identity_keys: Sequence[str] = (),
    *,
    recorded_paths: Sequence[tuple[str | int, ...]] | None = None,
) -> bool:
    """Whether a validator write-back event on this object covers the value at ``value_path``.

    ``value_path`` is the value's own payload path ("" for the object root).
    With ``identity_keys`` an event must have written one of them (always so
    for the object root). ``recorded_paths`` (``validator_materialized_paths``)
    may be passed to read one object's events only once.
    """

    target = _path_tokens(value_path)
    if target is None:
        return False
    paths = validator_materialized_paths(object_metadata) if recorded_paths is None else recorded_paths
    return any(_covers(path, target, identity_keys) for path in paths)


def value_covered_by_validator(
    object_metadata: Mapping[str, Any] | None,
    value_path: str,
    spec: ResolvableSpec | None,
    *,
    recorded_paths: Sequence[tuple[str | int, ...]] | None = None,
) -> bool:
    """Validator coverage of one value: its own path, or a declared mirror source (``covered_by``).

    ``recorded_paths`` (``validator_materialized_paths``) may be passed so one
    object's events are parsed once for all its values.
    """

    recorded = validator_materialized_paths(object_metadata) if recorded_paths is None else recorded_paths
    identity_keys = spec.identity_keys if spec is not None else ()
    if validator_event_covers(object_metadata, value_path, identity_keys, recorded_paths=recorded):
        return True
    return spec is not None and any(
        validator_event_covers(object_metadata, source, recorded_paths=recorded)
        for source in spec.covered_by
    )


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
    with outcome ``legacy_unverified``. A stored contract value that breaks
    the contract reads as unresolved/``invalid_schema``. Only vocabulary
    values come back; nothing raises.
    """

    if has_resolution_state(value):
        if stored_state_problem(value, identity_keys=identity_keys) is not None:
            return UNRESOLVED, OUTCOME_INVALID_SCHEMA
        return str(value[RESOLUTION_STATE_KEY]), str(value[LOOKUP_OUTCOME_KEY])
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

    Values with a valid contract state, and values this function already
    read (a pack's display copy), come back unchanged; an invalid stored
    one reads as unresolved/``invalid_schema`` (``_invalid_record_value``),
    is logged, and never raises. A legacy value reads
    with the explanation "Recorded before validation tracking; not verified."
    When not verified it reads as unresolved/``legacy_unverified``: its
    identity keys are emptied and its stored text becomes paper wording
    labelled "(legacy, unverified)". Nothing is written back to storage.
    """

    if not isinstance(value, Mapping):
        return value
    if has_resolution_state(value):
        problem = stored_state_problem(value, identity_keys=spec.identity_keys)
        if problem is None or _is_read_time_marked(value, spec.identity_keys):
            # A value already read here (a legacy or invalid-record reading) reads as it is.
            return value
        if _is_revalidated_legacy_leftover(value, spec):
            return _legacy_leftover_value(value, spec)
        return _invalid_record_value(value, spec, problem)
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


def _legacy_text_value(value: Any, spec: ResolvableSpec) -> dict[str, Any]:
    """A declared resolvable value stored before the contract as plain text (e.g. a string).

    It reads unresolved/``legacy_unverified`` with the text as paper wording
    labelled "(legacy, unverified)"; nothing verified it.
    """

    text = str(value).strip()
    return {
        **{key: None for key in spec.identity_keys},
        spec.mention_key: f"{text} {LEGACY_UNVERIFIED_SUFFIX}",
        RESOLUTION_STATE_KEY: UNRESOLVED,
        LOOKUP_OUTCOME_KEY: OUTCOME_LEGACY_UNVERIFIED,
        VALIDATOR_EXPLANATION_KEY: LEGACY_EXPLANATION,
    }


def _is_revalidated_legacy_leftover(value: Mapping[str, Any], spec: ResolvableSpec) -> bool:
    """A pre-contract value left unresolved that still holds its old identity.

    Earlier write-backs in this hotfix could leave an old record's identity
    beside an unresolved state; that identity was never verified.
    """

    if value.get(RESOLUTION_STATE_KEY) != UNRESOLVED or value.get(spec.mention_key) is not None:
        # Only a pre-contract value (no paper wording) can hold such a leftover; a
        # contract value that does breaks the invariant and reads as invalid.
        return False
    cleared = {**value, **{key: None for key in spec.identity_keys}}
    return stored_state_problem(cleared, identity_keys=spec.identity_keys) is None


def _legacy_leftover_value(value: Mapping[str, Any], spec: ResolvableSpec) -> dict[str, Any]:
    """Read an old identity left in an unresolved pre-contract value as unverified.

    The validator's outcome and explanation are kept; the old identity text
    becomes the paper wording labelled "(legacy, unverified)".
    """

    annotated = dict(value)
    stored = _stored_text(value, spec)
    for key in spec.identity_keys:
        annotated[key] = None
    annotated[spec.mention_key] = f"{stored} {LEGACY_UNVERIFIED_SUFFIX}" if stored else None
    return annotated


def _invalid_record_value(value: Mapping[str, Any], spec: ResolvableSpec, problem: str) -> dict[str, Any]:
    """A stored value that breaks the contract, read as unresolved with a clear marker."""

    _log.warning("Stored resolvable value breaks the contract and reads as unresolved: %s", problem)
    annotated = dict(value)
    stored = _stored_text(value, spec)
    for key in spec.identity_keys:
        annotated[key] = None
    annotated[spec.mention_key] = f"{stored} {INVALID_RECORD_SUFFIX}" if stored else None
    annotated[RESOLUTION_STATE_KEY] = UNRESOLVED
    annotated[LOOKUP_OUTCOME_KEY] = OUTCOME_INVALID_SCHEMA
    annotated[VALIDATOR_EXPLANATION_KEY] = INVALID_RECORD_EXPLANATION
    annotated[VALIDATOR_CURATOR_MESSAGE_KEY] = None
    return annotated


def stated_value(value: Any) -> Any:
    """A read-time copy of a resolvable value that states a valid resolution, without a spec.

    For surfaces reading a value that carries the contract state outside a
    pack declaration (e.g. custom attributes): an invalid stored one reads
    unresolved/``invalid_schema``; a valid one, or any value without the
    contract state, comes back unchanged. Declared values read through
    ``effective_payload`` instead.
    """

    if not has_resolution_state(value):
        return value
    problem = stored_state_problem(value)
    if problem is None:
        return value
    _log.warning("Stored resolvable value breaks the contract and reads as unresolved: %s", problem)
    return {
        **value,
        RESOLUTION_STATE_KEY: UNRESOLVED,
        LOOKUP_OUTCOME_KEY: OUTCOME_INVALID_SCHEMA,
        VALIDATOR_EXPLANATION_KEY: INVALID_RECORD_EXPLANATION,
        VALIDATOR_CURATOR_MESSAGE_KEY: None,
    }


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
    # Each concrete value is read once per pass: a pack may declare both a list
    # field and one of its elements (``terms`` and ``terms[0]``); the most
    # specific declaration reads the element, and the list pass skips it.
    annotated: set[tuple[str | int, ...]] = set()
    # One object's validator events are parsed once, and only when some value
    # stored before the contract needs them.
    recorded: list[tuple[tuple[str | int, ...], ...]] = []

    def covered(value_path: str, spec: ResolvableSpec) -> bool:
        if not recorded:
            recorded.append(validator_materialized_paths(object_metadata))
        return value_covered_by_validator(object_metadata, value_path, spec, recorded_paths=recorded[0])

    for field_path, spec in sorted(resolvable_fields.items(), key=lambda item: -len(item[0])):
        tokens = _path_tokens(field_path)
        if tokens is None:
            continue
        result = _annotate_at(result, tokens, (), spec, covered, annotated)
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
    covered: Callable[[str, ResolvableSpec], bool],
    annotated: set[tuple[str | int, ...]],
) -> Any:
    if isinstance(node, list):
        if remaining and isinstance(remaining[0], int):
            # An explicitly indexed declared path (e.g. ``terms[0]``) names one element.
            index = remaining[0]
            if index >= len(node):
                return node
            updated_list = list(node)
            updated_list[index] = _annotate_at(
                node[index], remaining[1:], (*walked, index), spec, covered, annotated
            )
            return updated_list
        # A declared path without an index names every element; each is its own value.
        return [
            _annotate_at(item, remaining, (*walked, index), spec, covered, annotated)
            for index, item in enumerate(node)
        ]
    if not remaining:
        if walked in annotated or _is_empty(node):
            return node
        annotated.add(walked)
        if not isinstance(node, Mapping):
            # A declared value stored before the contract as plain text.
            return _legacy_text_value(node, spec)
        # Coverage matters only for a value stored without the contract state.
        return effective_value(
            node,
            spec,
            covered_by_validator=(
                not has_resolution_state(node) and covered(_format_path(walked), spec)
            ),
        )
    if not isinstance(node, Mapping):
        return node
    key = remaining[0]
    if key not in node:
        return node
    updated = dict(node)
    updated[key] = _annotate_at(
        node[key], remaining[1:], (*walked, key), spec, covered, annotated
    )
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


def declared_resolvable_fields(metadata: Any, object_type: str) -> dict[str, ResolvableSpec]:
    """The resolvable values a pack declares for one object type: {field path: spec}.

    A field (or, at path "", the object root through its model) is declared
    resolvable by a display spec with a ``mention`` role, read from the field
    itself, else from its model or its referenced object's model.
    """

    models = {model.model_id: model for model in metadata.model_definitions}
    object_models = {obj.object_type: obj.model_ref for obj in metadata.object_definitions}
    object_definition = next(
        (obj for obj in metadata.object_definitions if obj.object_type == object_type), None,
    )
    if object_definition is None:
        return {}

    def model_display(model_ref: str | None) -> Any:
        model = models.get(model_ref) if model_ref else None
        return model.metadata.get("display") if model is not None else None

    specs: dict[str, ResolvableSpec] = {}
    root = resolvable_spec_from_display(model_display(object_definition.model_ref))
    if root is not None:
        specs[""] = root
    for field in object_definition.fields:
        display = field.metadata.get("display") or model_display(
            field.model_ref
            or (object_models.get(field.object_type_ref) if field.object_type_ref else None)
        )
        spec = resolvable_spec_from_display(display)
        if spec is not None:
            specs[field.field_path] = spec
    return _with_mirror_sources(specs, object_definition.fields)


def _with_mirror_sources(
    specs: dict[str, ResolvableSpec], fields: Sequence[Any],
) -> dict[str, ResolvableSpec]:
    """Give each declared value the source paths of the mirror copies written into it.

    A field declaring ``materializes_to_field_paths`` copies its validated
    value into those paths, so a validator event covering the source also
    covers the copy (e.g. a subject gene copied into the entity assayed).
    """

    declared_paths = {field.field_path for field in fields}
    sources: dict[str, list[str]] = {}
    for field in fields:
        mirrors = field.metadata.get("materializes_to_field_paths")
        if not isinstance(mirrors, list):
            continue
        for mirror in mirrors:
            if not isinstance(mirror, str) or not mirror.strip():
                continue
            mirror_path = mirror.strip()
            if mirror_path not in declared_paths and "." in mirror_path:
                # A mirror may name its object type first (``Type.field``).
                mirror_path = mirror_path.split(".", 1)[1]
            # The copy is one key of the value it lands in.
            value_path = mirror_path.rpartition(".")[0]
            if value_path in specs and field.field_path not in sources.get(value_path, ()):
                sources.setdefault(value_path, []).append(field.field_path)
    return {
        path: replace(spec, covered_by=tuple(sources[path])) if path in sources else spec
        for path, spec in specs.items()
    }


# Field metadata marking a resolvable value whose identity extraction fills from a fixed,
# local, in-code mapping table (e.g. an evidence code to its ECO term): mapping, not a
# database search, so it may be staged resolved. Every other value is staged unvalidated.
EXTRACTION_MAPPING_KEY = "extraction_mapping"
# What a fixed mapping table itself decides about an entry it does not map.
_MAPPING_MISS_OUTCOMES = frozenset({OUTCOME_NOT_FOUND, OUTCOME_CONFLICT})


def extraction_value_problems(payload: Mapping[str, Any], metadata: Any, object_type: str) -> list[str]:
    """What an extracted object's declared resolvable values claim that extraction may not.

    Extraction reads the paper; validators do every identity search. So each
    declared value an extractor stages is unresolved and ``not_validated``
    (its paper wording, with any paper-stated identifier as ``proposed_*``).
    The one exception is a value whose field declares ``EXTRACTION_MAPPING_KEY``:
    its fixed in-code table is the authority, so it may be staged resolved and
    matched, or unresolved (no identity) as a table miss (not_found) or an
    entry that does not apply (conflict). A value stored
    before the contract (no state) is left to the legacy rule. Returns one
    message per offending value; empty when the object conforms.
    """

    object_definition = next(
        (obj for obj in metadata.object_definitions if obj.object_type == object_type), None,
    )
    mapped = {
        field.field_path
        for field in (object_definition.fields if object_definition is not None else [])
        if field.metadata.get(EXTRACTION_MAPPING_KEY) is True
    }
    problems: list[str] = []
    for declared, spec in declared_resolvable_fields(metadata, object_type).items():
        tokens = _path_tokens(declared) if declared else ()
        if tokens is None:
            continue
        for path, value in _declared_values(payload, tokens, ()):
            if not has_resolution_state(value):
                continue
            state, outcome = value.get(RESOLUTION_STATE_KEY), value.get(LOOKUP_OUTCOME_KEY)
            where = f"{object_type}.{_format_path(path) or '<object root>'}"
            identity_empty = all(_is_empty(value.get(key)) for key in spec.identity_keys)
            if state == UNRESOLVED and outcome == OUTCOME_NOT_VALIDATED and identity_empty:
                continue
            if declared in mapped and (
                (state == RESOLVED and outcome == OUTCOME_MATCHED)
                # The fixed table is the authority for its field: a miss, or an entry that
                # does not apply here, is its outcome.
                or (state == UNRESOLVED and outcome in _MAPPING_MISS_OUTCOMES and identity_empty)
            ):
                continue
            problems.append(
                f"{where} was staged {state}/{outcome}; extraction stages every value unvalidated "
                "(its paper wording only) unless the pack declares it filled from a fixed mapping table"
            )
    return problems


def _declared_values(
    node: Any, remaining: Sequence[str | int], walked: tuple[str | int, ...],
) -> Iterable[tuple[tuple[str | int, ...], Mapping[str, Any]]]:
    """(path, value) for each stored mapping a declared path names; an unindexed list fans out."""

    if isinstance(node, list):
        if remaining and isinstance(remaining[0], int):
            if remaining[0] < len(node):
                yield from _declared_values(node[remaining[0]], remaining[1:], (*walked, remaining[0]))
            return
        for index, item in enumerate(node):
            yield from _declared_values(item, remaining, (*walked, index))
        return
    if not isinstance(node, Mapping):
        return
    if not remaining:
        yield walked, node
        return
    if remaining[0] in node:
        yield from _declared_values(node[remaining[0]], remaining[1:], (*walked, remaining[0]))


def _bare_path(tokens: Sequence[str | int]) -> str:
    return ".".join(str(token) for token in tokens if isinstance(token, str))


def declared_spec_for(
    declared: Mapping[str, ResolvableSpec], tokens: Sequence[str | int],
) -> ResolvableSpec | None:
    """The spec declared for a concrete path: its indexed form (``terms[0]``) or its list field."""

    return declared.get(_format_path(tokens)) or declared.get(_bare_path(tokens))


def _is_read_time_marked(value: Mapping[str, Any], identity_keys: Sequence[str]) -> bool:
    """A legacy or invalid-record reading whose mention already carries its label.

    Only a reading counts: unresolved, with an empty identity. A stored value
    merely claiming a read-time outcome is a broken record.
    """

    if value.get(RESOLUTION_STATE_KEY) != UNRESOLVED or any(
        not _is_empty(value.get(key)) for key in identity_keys
    ):
        return False
    outcome = value.get(LOOKUP_OUTCOME_KEY)
    return outcome == OUTCOME_LEGACY_UNVERIFIED or (
        outcome == OUTCOME_INVALID_SCHEMA and value.get(VALIDATOR_EXPLANATION_KEY) == INVALID_RECORD_EXPLANATION
    )


def unresolved_header_text(
    payload: Mapping[str, Any],
    field_path: str,
    *,
    object_metadata: Mapping[str, Any] | None = None,
    resolvable_fields: Mapping[str, ResolvableSpec] | None = None,
) -> str | None:
    """Explicit paper wording for a header (row label, summary) naming an unresolved value.

    ``field_path`` names a resolvable value or one of its keys (e.g. a label
    field). The value is recognised by the pack's declared
    ``resolvable_fields`` (``declared_resolvable_fields``), so a value stored
    before the contract gets the legacy rule, or else by its stored contract
    keys. Returns None when it names no resolvable value or the value is
    resolved; the caller then shows the stored value. Otherwise returns the
    paper wording labelled "(paper wording)" (or, for a legacy value, its
    stored text labelled "(legacy, unverified)"; a value already read
    through ``effective_payload`` keeps its label as is), and UNRESOLVED
    when no wording was stored. A header never shows paper wording as the item.
    """

    tokens = _path_tokens(field_path)
    if not tokens:
        return None
    declared = resolvable_fields or {}
    named = _walk(payload, tokens)
    parent_tokens = tokens[:-1] if isinstance(tokens[-1], str) else None
    parent = _walk(payload, parent_tokens) if parent_tokens is not None else None
    named_spec = declared_spec_for(declared, tokens)
    parent_spec = declared_spec_for(declared, parent_tokens) if parent_tokens is not None else None
    spec: ResolvableSpec | None = None
    if named_spec is not None and not isinstance(named, (Mapping, list)) and not _is_empty(named):
        # A declared value stored before the contract as plain text.
        return f"{str(named).strip()} {LEGACY_UNVERIFIED_SUFFIX}"
    if named_spec is not None and isinstance(named, Mapping):
        target, target_tokens, leaf, spec = named, tokens, None, named_spec
    elif parent_spec is not None and isinstance(parent, Mapping):
        target, target_tokens, leaf, spec = parent, parent_tokens, named, parent_spec
    elif has_resolution_state(named):
        target, target_tokens, leaf = named, tokens, None
    elif parent_tokens is not None and has_resolution_state(parent):
        target, target_tokens, leaf = parent, parent_tokens, named
    else:
        return None
    mention_key = spec.mention_key if spec is not None else MENTION_KEY
    mention = target.get(mention_key)
    mention = mention.strip() if isinstance(mention, str) and mention.strip() else None
    if has_resolution_state(target):
        if is_resolved(target, identity_keys=spec.identity_keys if spec is not None else ()):
            return None
        if mention and _is_read_time_marked(target, spec.identity_keys if spec is not None else ()):
            # A read-time reading (effective_payload) already labels its text.
            return mention
        if mention:
            return f"{mention} {PAPER_WORDING_SUFFIX}"
        # A pre-contract container re-validated as unresolved: its old text is unverified.
        stored = _stored_text(target, spec) if spec is not None else (
            str(leaf).strip() if leaf is not None and not isinstance(leaf, (Mapping, list)) else ""
        )
        return f"{stored} {LEGACY_UNVERIFIED_SUFFIX}" if stored else UNRESOLVED_DISPLAY
    if spec is not None:
        identity = [target.get(key) for key in spec.identity_keys]
    elif target is not named:
        identity = [leaf]
    else:
        identity = [item for key, item in target.items() if key not in CONTRACT_KEYS]
    if any(not _is_empty(item) for item in identity) and value_covered_by_validator(
        object_metadata, _format_path(target_tokens), spec
    ):
        return None
    if spec is not None:
        stored = _stored_text(target, spec)
    else:
        stored = mention or (
            str(leaf).strip() if leaf is not None and not isinstance(leaf, (Mapping, list)) else ""
        )
    return f"{stored} {LEGACY_UNVERIFIED_SUFFIX}" if stored else UNRESOLVED_DISPLAY


__all__ = [
    "CONTRACT_KEYS",
    "CURATOR_OVERRIDE_KEY",
    "CURATOR_OVERRIDE_METADATA_KEY",
    "EXTRACTION_MAPPING_KEY",
    "EXTRACTOR_PROPOSAL_PREFIX",
    "DECISIVE_OUTCOMES",
    "INVALID_RECORD_EXPLANATION",
    "INVALID_RECORD_SUFFIX",
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
    "OUTCOME_CURATOR_OVERRIDE",
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
    "PROFILE_VALIDATOR_MATERIALIZATION_METADATA_KEY",
    "OVERRULED_KEY_PREFIX",
    "RESOLUTION_STATES",
    "RESOLUTION_STATE_KEY",
    "RESOLUTION_STATE_LABELS",
    "RESOLVED",
    "RESOLVED_OUTCOMES",
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
    "apply_curator_identity",
    "check_resolvable_list",
    "check_resolvable_value",
    "copy_resolution",
    "declared_resolvable_fields",
    "declared_spec_for",
    "effective_payload",
    "effective_resolution",
    "effective_value",
    "extraction_value_problems",
    "has_resolution_state",
    "is_curator_override",
    "is_resolved",
    "lookup_outcome_for_failure",
    "mark_resolved",
    "mark_unresolved",
    "overruled_key",
    "resolvable_leaf_header",
    "resolvable_spec_from_display",
    "resolved_value",
    "stated_value",
    "stored_state_problem",
    "unresolved_header_text",
    "unresolved_list",
    "unresolved_positions",
    "unresolved_value",
    "validator_event_covers",
    "validator_materialized_paths",
    "without_overruled",
    "value_covered_by_validator",
]
