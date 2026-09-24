"""Shared classification helpers for package-scoped validator results."""

from __future__ import annotations

from typing import Any, Literal, get_args

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
    "blocked": LOOKUP_STATUS_BLOCKED,
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


ValidatorFailureClassification = Literal[
    "invalid_schema",
    "transient",
    "missing_expected_result_field",
    "ambiguous",
    "not_found",
    "conflict",
    "blocked",
    "rejected_candidates",
]
VALIDATOR_FAILURE_CLASSIFICATIONS: tuple[str, ...] = get_args(ValidatorFailureClassification)


def validator_failure_classification(
    result: DomainValidatorResultBase,
    *,
    error_type: type[Exception] = ValueError,
) -> ValidatorFailureClassification:
    """Classify unresolved validator results for validation finding details.

    Every value maps to a resolvable value's lookup outcome through
    ``resolvable_values.lookup_outcome_for_failure``. What the lookups found
    decides first: an unresolved result names the fields it could not fill
    (``missing_expected_fields``) whatever the reason, so that alone means
    the result is incomplete only when no lookup outcome says why.
    """

    methods = {attempt.method for attempt in result.lookup_attempts}
    if "invalid_schema" in methods:
        return "invalid_schema"
    if "validator_agent_error" in methods:
        return "transient"
    outcomes = {attempt.outcome for attempt in result.lookup_attempts}
    if "ambiguous" in outcomes:
        return "ambiguous"
    if "not_found" in outcomes:
        return "not_found"
    if "conflict" in outcomes:
        return "conflict"
    if "blocked" in outcomes:
        return "blocked"
    if "error" in outcomes:
        return "transient"
    if result.missing_expected_fields:
        return "missing_expected_result_field"
    if outcomes == {"success"}:
        # Every lookup succeeded, and the validator judged no candidate fits.
        return "rejected_candidates"
    raise error_type(
        "Unable to classify unresolved validator result "
        f"{result.request_id!r} with lookup outcomes {sorted(outcomes)!r} "
        f"and methods {sorted(methods)!r}"
    )
