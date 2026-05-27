"""Shared value-presence predicates for domain-pack validator results."""

from __future__ import annotations

from typing import Any


def missing_resolved_value(value: Any) -> bool:
    """Return true when a resolved validator field has no materialized value."""

    return value is None or value == "" or value == [] or value == {}
