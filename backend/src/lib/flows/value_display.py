"""Standard display text for structured curation values in non-JSON outputs.

Domain packs declare how a structured value reads through a display spec on a
model definition or a field (``metadata.display``):

- ``{label: <leaf path>, id: <leaf path>}`` renders "label (id)"; either role is
  optional and each is one (possibly dotted) leaf path, never a list of
  fallbacks. ``state`` plus ``resolved_states`` name an explicit resolution
  leaf. A declared value whose own label and id are empty renders empty.
- ``{label: <key>, id: <key>, mention: <key>}`` declares a resolvable value
  (``src.lib.domain_packs.resolvable_values``): the extracted paper wording
  sits at ``mention`` and the validated identity at ``label``/``id``. A
  resolved value renders "label (id)"; an unresolved one renders the literal
  ``UNRESOLVED`` with neither label nor paper wording in the cell. The paper
  wording is its own field (``<field>.mention``), never part of this cell.
  An optional ``validated: [<key>, ...]`` names further keys only a
  validator fills (e.g. a taxon); they count as identity, and the cell still
  reads "label (id)".
- ``{compose: [<child path>, ...], separator: "; "}`` joins the display text of
  child values (each child carries its own resolved spec). An entry may be a
  mapping ``{path, display}``; one without a path reads the value itself with
  its ``display`` (e.g. "label (id)" followed by other parts).

Without a spec a generic reading applies: a value stored with the contract
state (``resolution_state`` and ``lookup_outcome``) reads as above with the
generic keys; a mapping that merely holds a ``mention`` key is not a
resolvable value; a term-like value holding only one of ``curie|id|identifier`` and
one of ``name|label|display_name`` reads "label (id)"; any other value renders
all its ``key: value`` pairs so nothing is dropped.

A resolvable value's own vocabulary leaves (``resolution_state``,
``lookup_outcome``) carry an internal ``value_labels`` spec so their codes read
in plain words ("Matched", "Not found", ...).

A resolvable value's state is the one stored with it. A value stored before
that contract has no state and reads as unresolved unless the caller applied
the read-time legacy rule (``resolvable_values.effective_payload``) first.
Lists join with "; ", and lists of structured records (or of lists) with " | "
so record boundaries stay visible; a list nested inside a record joins its
items with ", ". CSV, TSV and chat cells therefore never contain JSON or
Python object text; JSON output keeps the raw values.

Validation findings never change a cell: a value reads as it is stored. A
reviewer's doubt about a field is its own export field ("Review uncertainty").
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from src.lib.domain_packs.resolvable_values import (
    CONTRACT_KEYS,
    EXTRACTOR_PROPOSAL_PREFIX,
    UNRESOLVED_DISPLAY,
    has_resolution_state,
    is_resolved,
    resolvable_spec_from_display,
    without_overruled,
)

GENERIC_ID_KEYS = ("curie", "id", "identifier")
GENERIC_LABEL_KEYS = ("name", "label", "display_name")
LIST_SEPARATOR = "; "
RECORD_SEPARATOR = " | "
NESTED_SEPARATOR = ", "
UNRESOLVED = "unresolved"
# Cell text for a stored controlled-vocabulary word outside its vocabulary.
INVALID_VOCABULARY_VALUE = "Invalid value"

_log = logging.getLogger(__name__)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _first(value: Mapping[str, Any], keys: Sequence[str]) -> str:
    """First non-empty scalar at the given keys; declared keys may be dotted paths."""

    for key in keys:
        candidate = _child(value, key) if "." in str(key) else value.get(key)
        if not _is_empty(candidate) and not isinstance(candidate, (dict, list)):
            return _scalar_text(candidate)
    return ""


def _child(value: Any, path: str) -> Any:
    current = value
    for token in str(path).split("."):
        if isinstance(current, list):
            current = [_child(item, token) for item in current]
            continue
        if not isinstance(current, Mapping):
            return None
        current = current.get(token)
    return current


def _labeled(label: str, identifier: str, unresolved: bool) -> str:
    if unresolved:
        if label and identifier:
            return f"{label} ({identifier}, {UNRESOLVED})"
        return f"{label or identifier} ({UNRESOLVED})"
    if label and identifier:
        return f"{label} ({identifier})"
    return label or identifier


def _pairs_text(value: Mapping[str, Any], marked: bool) -> str:
    parts = []
    for key, item in value.items():
        text = _render(item, None, nested=True, marked=marked)
        if text:
            parts.append(f"{key}: {text}")
    return LIST_SEPARATOR.join(parts)


def _compose_text(value: Mapping[str, Any], spec: Mapping[str, Any], marked: bool) -> str:
    separator = str(spec.get("separator") or LIST_SEPARATOR)
    parts = []
    for entry in spec["compose"]:
        path = str(entry.get("path") or "") if isinstance(entry, Mapping) else str(entry)
        child_spec = entry.get("display") if isinstance(entry, Mapping) else None
        if path:
            text = _render(_child(value, path), child_spec, nested=True, marked=marked)
        else:
            text = _mapping_text(value, child_spec, marked)
        if text:
            parts.append(text)
    # A composite renders only its declared parts; nothing else substitutes.
    return separator.join(parts)


def _mapping_text(value: Mapping[str, Any], spec: Mapping[str, Any] | None, marked: bool) -> str:
    if spec and spec.get("compose"):
        return _compose_text(value, spec, marked)
    # A declared resolvable value, or a value stored with the contract state;
    # a mapping that merely holds a ``mention`` key is read like any other.
    if (spec and spec.get("mention")) or has_resolution_state(value):
        return _resolvable_text(value, spec)
    if spec and (spec.get("label") or spec.get("id")):
        label = _first(value, [spec["label"]]) if spec.get("label") else ""
        identifier = _first(value, [spec["id"]]) if spec.get("id") else ""
        unresolved = False
        state_key = spec.get("state")
        if state_key:
            state = _child(value, state_key)
            if not _is_empty(state) and _scalar_text(state) not in {
                str(item) for item in spec.get("resolved_states") or []
            }:
                unresolved = True
        # A declared identifier role that is empty while a label exists marks
        # a paper-grounded proposal that was not resolved.
        if spec.get("id") and not identifier and label:
            unresolved = True
        # A declared field renders only its own label and id: when both are
        # empty the cell is empty, never another field such as a mention.
        return _labeled(label, identifier, unresolved and marked) if label or identifier else ""
    # The generic "label (id)" reading only applies to term-like values whose
    # content is exactly one label and/or one identifier; anything more renders
    # every key so no undeclared content (e.g. candidate matches or a second
    # identifier) is dropped from a cell.
    present = {key for key, item in value.items() if not _is_empty(item)}
    if (
        present
        and present <= set(GENERIC_LABEL_KEYS) | set(GENERIC_ID_KEYS)
        and len(present & set(GENERIC_LABEL_KEYS)) <= 1
        and len(present & set(GENERIC_ID_KEYS)) <= 1
    ):
        label = _first(value, GENERIC_LABEL_KEYS)
        identifier = _first(value, GENERIC_ID_KEYS)
        if label or identifier:
            return _labeled(label, identifier, False)
    return _pairs_text(value, marked)


def _resolvable_text(value: Mapping[str, Any], spec: Mapping[str, Any] | None) -> str:
    """A resolvable value: "label (id)" when resolved, else the literal UNRESOLVED.

    Only the stored state decides; the paper wording is never shown here, so
    an unresolved value never reads as if it were the validated item.
    """

    # A declared resolvable value (mention role) is checked against its own id/label keys.
    identity_keys = (
        resolvable_spec_from_display(spec).identity_keys
        if spec and spec.get("mention")
        else ()
    )
    if not is_resolved(value, identity_keys=identity_keys):
        # Unresolved, stored before the contract, or a stored record that breaks it.
        return UNRESOLVED_DISPLAY
    if spec and (spec.get("label") or spec.get("id")):
        label = _first(value, [spec["label"]]) if spec.get("label") else ""
        identifier = _first(value, [spec["id"]]) if spec.get("id") else ""
        return _labeled(label, identifier, False)
    label = _first(value, GENERIC_LABEL_KEYS)
    identifier = _first(value, GENERIC_ID_KEYS)
    if label or identifier:
        return _labeled(label, identifier, False)
    # Undeclared identity keys: show the validated content, never the paper wording.
    # Neither the extractor's proposals nor an overruled identity is the value.
    identity = {
        key: item
        for key, item in without_overruled(value).items()
        if key not in CONTRACT_KEYS and not str(key).startswith(EXTRACTOR_PROPOSAL_PREFIX)
    }
    return _pairs_text(identity, False)


def display_text(
    value: Any,
    spec: Mapping[str, Any] | None = None,
    *,
    marked: bool = True,
    nested: bool = False,
) -> str:
    """Readable text for one stored value; empty values give "".

    Open validation findings never change the text. ``marked=False`` leaves
    out the unresolved marker that a declared value's own ``state`` leaf, or
    its empty declared identifier, adds (for keys such as map_value lookups).
    ``nested=True`` reads a value that is one item of a list (a split column
    or a list element), so a list value joins its items with ", " as it does
    inside the whole cell.
    """

    return _render(value, spec, nested=nested, marked=marked)


def _render(value: Any, spec: Mapping[str, Any] | None, *, nested: bool, marked: bool) -> str:
    if _is_empty(value):
        return ""
    if isinstance(value, (list, tuple)):
        parts = [text for item in value if (text := _render(item, spec, nested=True, marked=marked))]
        if nested:
            # Items of a list inside a record: " | " and "; " keep their meaning.
            separator = NESTED_SEPARATOR
        elif any(isinstance(item, (Mapping, list, tuple)) for item in value):
            separator = RECORD_SEPARATOR
        else:
            separator = LIST_SEPARATOR
        return separator.join(parts)
    if isinstance(value, Mapping):
        return _mapping_text(value, spec, marked)
    text = _scalar_text(value)
    if spec and spec.get("value_labels"):
        # A controlled-vocabulary leaf (e.g. a lookup outcome) reads in plain words;
        # a stored word outside the vocabulary is marked, never raised on.
        labels = spec["value_labels"]
        if text not in labels:
            _log.warning("Stored value %r is outside its controlled vocabulary", text)
            return f"{INVALID_VOCABULARY_VALUE} ({text})"
        return labels[text]
    return text


__all__ = [
    "display_text",
    "GENERIC_ID_KEYS",
    "GENERIC_LABEL_KEYS",
    "LIST_SEPARATOR",
    "NESTED_SEPARATOR",
    "RECORD_SEPARATOR",
    "UNRESOLVED",
    "UNRESOLVED_DISPLAY",
]
