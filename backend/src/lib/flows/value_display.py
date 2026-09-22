"""Standard display text for structured curation values in non-JSON outputs.

Domain packs declare how a structured value reads through a display spec on a
model definition or a field (``metadata.display``):

- ``{label: <leaf key>, id: <leaf key>}`` renders "label (id)"; either role is
  optional. ``state`` plus ``resolved_states`` name an explicit resolution leaf.
- ``{compose: [<child path>, ...], separator: "; "}`` joins the display text of
  child values (each child carries its own resolved spec).

Without a spec a generic reading applies: ``curie|id|identifier`` with
``name|label|display_name`` as "label (id)", otherwise ``key: value`` pairs of
the value's leaves. Lists join with "; ". CSV, TSV and chat cells therefore
never contain JSON or Python object text; JSON output keeps the raw values.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

GENERIC_ID_KEYS = ("curie", "id", "identifier")
GENERIC_LABEL_KEYS = ("name", "label", "display_name")
LIST_SEPARATOR = "; "
UNRESOLVED = "unresolved"


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
    for key in keys:
        candidate = value.get(key)
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


def _pairs_text(value: Mapping[str, Any]) -> str:
    parts = []
    for key, item in value.items():
        if _is_empty(item):
            continue
        text = display_text(item)
        if text:
            parts.append(f"{key}: {text}")
    return LIST_SEPARATOR.join(parts)


def _mapping_text(value: Mapping[str, Any], spec: Mapping[str, Any] | None, unresolved: bool) -> str:
    if spec and spec.get("compose"):
        separator = str(spec.get("separator") or LIST_SEPARATOR)
        parts = []
        for entry in spec["compose"]:
            path = entry["path"] if isinstance(entry, Mapping) else str(entry)
            child_spec = entry.get("display") if isinstance(entry, Mapping) else None
            text = display_text(_child(value, path), child_spec)
            if text:
                parts.append(text)
        text = separator.join(parts) or _pairs_text(value)
        return f"{text} ({UNRESOLVED})" if unresolved and text else text
    if spec and (spec.get("label") or spec.get("id")):
        label = _first(value, [spec["label"]]) if spec.get("label") else ""
        identifier = _first(value, [spec["id"]]) if spec.get("id") else ""
        state_key = spec.get("state")
        if state_key:
            state = value.get(state_key)
            if not _is_empty(state) and _scalar_text(state) not in {
                str(item) for item in spec.get("resolved_states") or []
            }:
                unresolved = True
        # A declared identifier role that is empty while a label exists marks
        # a paper-grounded proposal that was not resolved.
        if spec.get("id") and not identifier and label:
            unresolved = True
        if label or identifier:
            return _labeled(label, identifier, unresolved)
        text = _pairs_text(value)
        return f"{text} ({UNRESOLVED})" if unresolved and text else text
    label = _first(value, GENERIC_LABEL_KEYS)
    identifier = _first(value, GENERIC_ID_KEYS)
    if label or identifier:
        return _labeled(label, identifier, unresolved)
    text = _pairs_text(value)
    return f"{text} ({UNRESOLVED})" if unresolved and text else text


def display_text(
    value: Any,
    spec: Mapping[str, Any] | None = None,
    *,
    unresolved: bool | frozenset[int] = False,
) -> str:
    """Readable text for one stored value; empty values give "".

    ``unresolved`` is True for the whole value, or a set of list indexes whose
    elements are unresolved.
    """

    if _is_empty(value):
        return ""
    if isinstance(value, (list, tuple)):
        parts = []
        for index, item in enumerate(value):
            item_unresolved = (
                unresolved if isinstance(unresolved, bool) else index in unresolved
            )
            text = display_text(item, spec, unresolved=item_unresolved)
            if text:
                parts.append(text)
        return LIST_SEPARATOR.join(parts)
    whole = unresolved if isinstance(unresolved, bool) else False
    if isinstance(value, Mapping):
        return _mapping_text(value, spec, whole)
    text = _scalar_text(value)
    return f"{text} ({UNRESOLVED})" if whole and text else text


__all__ = ["display_text", "GENERIC_ID_KEYS", "GENERIC_LABEL_KEYS", "LIST_SEPARATOR"]
