"""Pack-declared exception to the rule that an extraction result must cite evidence.

An object definition may set ``metadata.evidence_optional_when`` to exact field values.
This is a structural check of a declared record shape. It never judges whether a record
is scientifically justified.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _matches(payload: Any, rule: Any) -> bool:
    if not isinstance(payload, Mapping) or not isinstance(rule, Mapping) or not rule:
        return False
    return all(
        key in payload and type(payload[key]) is type(value) and payload[key] == value
        for key, value in rule.items()
    )


def records_may_omit_evidence(payload: Mapping[str, Any], metadata: Any) -> bool:
    """Return whether every record in this result matches its pack's declared exception."""

    objects = payload.get("extracted_objects") or payload.get("curatable_objects")
    if not isinstance(objects, list) or not objects:
        return False
    rules = {
        definition.object_type: definition.metadata.get("evidence_optional_when")
        for definition in metadata.object_definitions
    }
    return all(
        isinstance(record, Mapping)
        and _matches(record.get("payload"), rules.get(record.get("object_type")))
        for record in objects
    )


__all__ = ["records_may_omit_evidence"]
