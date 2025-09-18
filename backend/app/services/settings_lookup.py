"""Helpers for reading persisted settings overrides from the database."""

from __future__ import annotations

import json
from typing import Any, Callable, TypeVar

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Settings as SettingsModel

T = TypeVar("T")


def get_setting_value(
    key: str,
    default: T,
    *,
    cast: Callable[[Any], T] | None = None,
) -> T:
    """Fetch a setting from the DB, falling back to the provided default."""

    with SessionLocal() as session:
        value = _load_raw_value(session, key)

    if value in (None, ""):
        return default

    if cast is None:
        try:
            return value  # type: ignore[return-value]
        except TypeError:
            return default

    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _load_raw_value(session: Session, key: str) -> Any:
    record = session.query(SettingsModel).filter(SettingsModel.key == key).first()
    if record is None or record.value in (None, ""):
        return None

    raw = record.value
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


__all__ = ["get_setting_value"]
