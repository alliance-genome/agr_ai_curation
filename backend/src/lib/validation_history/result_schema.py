"""Trusted concrete validator contracts retained with immutable recipes."""

from collections.abc import Mapping

from pydantic import ValidationError

from src.lib.config.schema_discovery import resolve_output_schema
from src.schemas.domain_validator import (
    DomainValidatorResultBase,
    is_domain_validator_result_schema,
)
from . import store


def _registered_schema(key):
    # Platform-produced base results are an explicit contract, never a fallback
    # for an unknown specialized result. No persisted module names are imported.
    schema = (
        DomainValidatorResultBase
        if key == "DomainValidatorResultBase"
        else resolve_output_schema(key)
    )
    if schema is None or not is_domain_validator_result_schema(schema):
        raise store.HistoryConflict(
            "Validator result schema is unavailable; "
            "a new authenticated validation and review round is required"
        )
    return schema


def capture_result_schema(result):
    """Record the actual executed result type, including flow-selected agents."""
    schema = type(result)
    key = schema.__name__
    if _registered_schema(key) is not schema:
        raise store.HistoryConflict("Validator result type is not its registered schema")
    return {"key": key, "sha256": store.canonical(schema.model_json_schema())[0]}


def restore_result_schema(payload, descriptor, *, request):
    """Reconstruct only the server-recorded, unchanged registered contract."""
    if not isinstance(descriptor, Mapping):
        raise store.HistoryConflict(
            "Selected recipe lacks its validator result schema; "
            "a new authenticated validation and review round is required"
        )
    if (set(descriptor) != {"key", "sha256"}
            or not isinstance(descriptor["key"], str)
            or not isinstance(descriptor["sha256"], str)):
        raise store.HistoryConflict("Selected validator result schema descriptor is invalid")
    schema = _registered_schema(descriptor["key"])
    if store.canonical(schema.model_json_schema())[0] != descriptor["sha256"]:
        raise store.HistoryConflict(
            "Selected validator result schema changed; "
            "a new authenticated validation and review round is required"
        )
    try:
        return schema.model_validate(payload, context={"domain_validation_request": request})
    except ValidationError as exc:
        raise store.HistoryConflict(
            "Selected validator result no longer satisfies its registered schema; "
            "a new authenticated validation and review round is required"
        ) from exc
