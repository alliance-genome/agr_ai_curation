"""Which of a record's evidence a validator binding receives.

Evidence records carry field tags (``field_path``, ``field_paths`` and
``envelope_targets[].field_path``) written when the evidence was recorded or
attached. A validator binding declares the fields it validates and reads. A
binding receives the record's evidence whose tags overlap its own declared
field paths, plus the record's evidence that no validator binding on the
record claims: untagged evidence, and evidence tagged only to fields that no
active validator binding on that record validates or reads.

A binding that declares an ``evidence_record`` selector without ``field_path``
reads the whole record's evidence by its own configuration, so it receives all
of it.

Everything here compares configured paths with stored tags. Nothing reads
evidence text or decides whether a quote supports a value.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from src.schemas.domain_pack_metadata import DomainPackInputSelector

_SEGMENT = re.compile(r"^(?P<name>[^\[\]]+)(?P<indexes>(?:\[\d+\])*)$")
_INDEX = re.compile(r"\[(\d+)\]")

# Selector sources that read a field of the target object's payload.
_PAYLOAD_PATH_SOURCES = frozenset({"payload", "payload_keyed_literal"})
# Selector sources that name a target-object field in ``field_path``.
_FIELD_PATH_SOURCES = frozenset({"evidence_record", "object_ref"})


def selector_field_path(selector: DomainPackInputSelector) -> str | None:
    """The target-object field path this selector reads, if it reads one."""

    if selector.source in _PAYLOAD_PATH_SOURCES:
        return selector.path or None
    if selector.source in _FIELD_PATH_SOURCES:
        return selector.field_path or None
    return None


def reads_record_evidence(input_fields: Mapping[str, DomainPackInputSelector]) -> bool:
    """True when a selector declares the whole record's evidence as an input."""

    return any(
        selector.source == "evidence_record" and selector.field_path is None
        for selector in input_fields.values()
    )


def evidence_record_field_paths(record: Mapping[str, Any]) -> tuple[str, ...]:
    """Every field tag stored on one evidence record, in first-seen order."""

    paths: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            paths.append(value)

    add(record.get("field_path"))
    raw_field_paths = record.get("field_paths")
    if _is_sequence(raw_field_paths):
        for field_path in raw_field_paths:
            add(field_path)
    for target in evidence_record_targets(record):
        add(target.get("field_path"))
    return tuple(paths)


def evidence_record_targets(record: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    targets: list[Mapping[str, Any]] = []
    raw_targets = record.get("envelope_targets")
    if _is_sequence(raw_targets):
        targets.extend(target for target in raw_targets if isinstance(target, Mapping))
    raw_target = record.get("envelope_target")
    if isinstance(raw_target, Mapping):
        targets.append(raw_target)
    return tuple(targets)


def field_paths_overlap(left: str, right: str, *, compare_indexes: bool) -> bool:
    """True when one path equals, contains or lies inside the other.

    Paths compare segment by segment on their field names, so ``a.b`` overlaps
    ``a``, ``a.b`` and ``a.b.c`` but not ``a.c`` or ``a.bc``. With
    ``compare_indexes`` a list index matters only where both paths give one for
    the same segment: ``a[1].b`` overlaps ``a.b`` and ``a[1]`` but not
    ``a[0].b``.
    """

    left_segments, right_segments = _segments(left), _segments(right)
    for (left_name, left_indexes), (right_name, right_indexes) in zip(
        left_segments, right_segments
    ):
        if left_name != right_name:
            return False
        if compare_indexes and left_indexes and right_indexes:
            shared = min(len(left_indexes), len(right_indexes))
            if left_indexes[:shared] != right_indexes[:shared]:
                return False
    return True


def binding_evidence(
    records: Sequence[Mapping[str, Any]],
    *,
    own_paths: Iterable[str],
    record_binding_paths: Iterable[str],
) -> tuple[Mapping[str, Any], ...]:
    """The records one binding receives, in the record's order.

    ``own_paths`` are the binding's own field paths, already resolved to the
    element it validates. ``record_binding_paths`` are the declared field paths
    of every active validator binding on the record; a tag that overlaps none of
    them belongs to no binding.
    """

    own = tuple(own_paths)
    claimed = tuple(record_binding_paths)
    selected = []
    for record in records:
        tags = evidence_record_field_paths(record)
        if not tags or not any(
            field_paths_overlap(tag, path, compare_indexes=False)
            for tag in tags
            for path in claimed
        ):
            selected.append(record)
            continue
        if any(
            field_paths_overlap(tag, path, compare_indexes=True)
            for tag in tags
            for path in own
        ):
            selected.append(record)
    return tuple(selected)


def _segments(path: str) -> tuple[tuple[str, tuple[int, ...]], ...]:
    segments = []
    for segment in path.split("."):
        parsed = _SEGMENT.match(segment)
        if parsed is None:
            # Not a well-formed path segment; it compares as literal text.
            segments.append((segment, ()))
            continue
        segments.append(
            (
                parsed.group("name"),
                tuple(int(index) for index in _INDEX.findall(parsed.group("indexes"))),
            )
        )
    return tuple(segments)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
