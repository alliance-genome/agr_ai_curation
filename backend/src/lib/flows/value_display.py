"""Standard display text for structured curation values in non-JSON outputs.

Domain packs declare how a structured value reads through a display spec on a
model definition or a field (``metadata.display``):

- ``{label: <leaf path>, id: <leaf path>}`` renders "label (id)"; either role is
  optional and each is one (possibly dotted) leaf path, never a list of
  fallbacks. ``state`` plus ``resolved_states`` name an explicit resolution
  leaf. A declared value whose own label and id are empty renders empty.
- ``{compose: [<child path>, ...], separator: "; "}`` joins the display text of
  child values (each child carries its own resolved spec).

Without a spec a generic reading applies: a term-like value holding only one
of ``curie|id|identifier`` and one of ``name|label|display_name`` reads
"label (id)"; any other value renders all its ``key: value`` pairs so nothing
is dropped.
Lists join with "; ", and lists of structured records with " | " so record
boundaries stay visible. CSV, TSV and chat cells therefore never contain JSON
or Python object text; JSON output keeps the raw values.

Unresolved markers are placed on the part of a value an open finding names:
``unresolved`` is True for the whole value or a set of finding paths relative
to the value (tuples of keys and list indexes).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from src.schemas.domain_envelope import parse_field_path

GENERIC_ID_KEYS = ("curie", "id", "identifier")
GENERIC_LABEL_KEYS = ("name", "label", "display_name")
LIST_SEPARATOR = "; "
RECORD_SEPARATOR = " | "
UNRESOLVED = "unresolved"

PathToken = str | int
FindingPaths = frozenset[tuple[PathToken, ...]]
WHOLE: FindingPaths = frozenset({()})


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


def path_tokens(path: str) -> tuple[PathToken, ...] | None:
    """Keys and list indexes of a payload path; ``[]`` marks a fanned-out list."""

    try:
        return parse_field_path(str(path).replace("[]", ""))
    except ValueError:
        return None


def relative_finding_path(
    finding: Sequence[PathToken], target: Sequence[PathToken],
) -> tuple[PathToken, ...] | None:
    """A finding path relative to the value read at ``target``, or None.

    Where the target fans out over a list without an index, the finding's
    index there becomes a position in the fanned-out value. A finding at or
    above the target covers the whole value (the empty path).
    """

    finding_index = target_index = 0
    positions: list[PathToken] = []
    while finding_index < len(finding) and target_index < len(target):
        finding_token, target_token = finding[finding_index], target[target_index]
        if isinstance(target_token, int):
            if isinstance(finding_token, int):
                if finding_token != target_token:
                    return None
                finding_index += 1
            # A finding on the whole list covers the indexed element.
            target_index += 1
            continue
        if isinstance(finding_token, int):
            positions.append(finding_token)
            finding_index += 1
            continue
        if finding_token != target_token:
            return None
        finding_index += 1
        target_index += 1
    return (*positions, *finding[finding_index:])


def _finding_paths(unresolved: bool | Collection[tuple[PathToken, ...]]) -> FindingPaths:
    if isinstance(unresolved, bool):
        return WHOLE if unresolved else frozenset()
    return frozenset(tuple(path) for path in unresolved)


def _child_paths(paths: FindingPaths, child: Sequence[PathToken]) -> FindingPaths:
    return frozenset(
        relative
        for path in paths
        if (relative := relative_finding_path(path, child)) is not None
    )


def _pairs_text(value: Mapping[str, Any], paths: FindingPaths) -> str:
    parts = []
    for key, item in value.items():
        if _is_empty(item):
            continue
        item_paths = frozenset(path[1:] for path in paths if path[0] == key)
        text = display_text(item, unresolved=item_paths)
        if text:
            parts.append(f"{key}: {text}")
    return LIST_SEPARATOR.join(parts)


def _mapping_text(value: Mapping[str, Any], spec: Mapping[str, Any] | None, paths: FindingPaths) -> str:
    # Findings that name a key place their marker on that part; any other
    # finding on this value (the value itself, or an index into a mapping)
    # marks the value as a whole.
    keyed = frozenset(path for path in paths if path and isinstance(path[0], str))
    whole = paths != keyed
    if spec and spec.get("compose"):
        separator = str(spec.get("separator") or LIST_SEPARATOR)
        parts = []
        for entry in spec["compose"]:
            path = entry["path"] if isinstance(entry, Mapping) else str(entry)
            child_spec = entry.get("display") if isinstance(entry, Mapping) else None
            tokens = path_tokens(path) or (str(path),)
            # Each part carries only the findings on its own sub-path.
            text = display_text(_child(value, path), child_spec, unresolved=_child_paths(keyed, tokens))
            if text:
                parts.append(text)
                keyed -= {finding for finding in keyed if relative_finding_path(finding, tokens) is not None}
        # A composite renders only its declared parts; nothing else substitutes.
        # A finding on an undisplayed part still marks the composite.
        text = separator.join(parts)
        return f"{text} ({UNRESOLVED})" if (whole or keyed) and text else text
    unresolved = bool(paths)
    if spec and (spec.get("label") or spec.get("id")):
        label = _first(value, [spec["label"]]) if spec.get("label") else ""
        identifier = _first(value, [spec["id"]]) if spec.get("id") else ""
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
        return _labeled(label, identifier, unresolved) if label or identifier else ""
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
            return _labeled(label, identifier, unresolved)
    text = _pairs_text(value, keyed)
    unplaced = whole or any(path[0] not in present for path in keyed)
    return f"{text} ({UNRESOLVED})" if unplaced and text else text


def display_text(
    value: Any,
    spec: Mapping[str, Any] | None = None,
    *,
    unresolved: bool | Collection[tuple[PathToken, ...]] = False,
) -> str:
    """Readable text for one stored value; empty values give "".

    ``unresolved`` is True for the whole value, or the open finding paths
    relative to the value; each marker lands on the part a finding names.
    """

    if _is_empty(value):
        return ""
    paths = _finding_paths(unresolved)
    if isinstance(value, (list, tuple)):
        # An index names one element; a key, or an index past the end, applies
        # to every element so no finding is dropped.
        spread = frozenset(
            () if path and isinstance(path[0], int) else path
            for path in paths
            if not path or not isinstance(path[0], int) or path[0] >= len(value)
        )
        parts = []
        for index, item in enumerate(value):
            item_paths = spread | frozenset(
                path[1:] for path in paths if path and path[0] == index
            )
            text = display_text(item, spec, unresolved=item_paths)
            if text:
                parts.append(text)
        separator = (
            RECORD_SEPARATOR if any(isinstance(item, Mapping) for item in value) else LIST_SEPARATOR
        )
        return separator.join(parts)
    if isinstance(value, Mapping):
        return _mapping_text(value, spec, paths)
    text = _scalar_text(value)
    return f"{text} ({UNRESOLVED})" if paths and text else text


__all__ = [
    "display_text",
    "path_tokens",
    "relative_finding_path",
    "GENERIC_ID_KEYS",
    "GENERIC_LABEL_KEYS",
    "LIST_SEPARATOR",
    "RECORD_SEPARATOR",
]
