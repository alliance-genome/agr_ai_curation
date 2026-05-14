"""Shared classification helpers for package-scoped validator results."""

from __future__ import annotations

from typing import Any

from src.lib.lookup_status import (
    LOOKUP_STATUS_AMBIGUOUS,
    LOOKUP_STATUS_BLOCKED,
    LOOKUP_STATUS_NOT_FOUND,
    LOOKUP_STATUS_SUCCESS,
    LOOKUP_STATUS_TRANSIENT,
)
from src.schemas.domain_validator import DomainValidatorResultBase


LOOKUP_OUTCOME_TO_STATUS = {
    "success": LOOKUP_STATUS_SUCCESS,
    "not_found": LOOKUP_STATUS_NOT_FOUND,
    "ambiguous": LOOKUP_STATUS_AMBIGUOUS,
    "conflict": LOOKUP_STATUS_BLOCKED,
    "error": LOOKUP_STATUS_TRANSIENT,
}


def lookup_status_for_validator_outcome(
    outcome: Any,
    *,
    error_type: type[Exception] = ValueError,
) -> str:
    """Map validator lookup outcomes to the shared envelope lookup statuses."""

    try:
        return LOOKUP_OUTCOME_TO_STATUS[outcome]
    except KeyError as exc:
        raise error_type(f"Unrecognized lookup attempt outcome: {outcome!r}") from exc


def validator_failure_classification(
    result: DomainValidatorResultBase,
    *,
    error_type: type[Exception] = ValueError,
) -> str:
    """Classify unresolved validator results for validation finding details."""

    methods = {attempt.method for attempt in result.lookup_attempts}
    if "invalid_schema" in methods:
        return "invalid_schema"
    if "validator_agent_error" in methods:
        return "transient"
    if result.missing_expected_fields:
        return "missing_expected_result_field"
    outcomes = {attempt.outcome for attempt in result.lookup_attempts}
    if "ambiguous" in outcomes:
        return "ambiguous"
    if "not_found" in outcomes:
        return "not_found"
    if "conflict" in outcomes:
        return "conflict"
    if "error" in outcomes:
        return "transient"
    raise error_type(
        "Unable to classify unresolved validator result "
        f"{result.request_id!r} with lookup outcomes {sorted(outcomes)!r} "
        f"and methods {sorted(methods)!r}"
    )
