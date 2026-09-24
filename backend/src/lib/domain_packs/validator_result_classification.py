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
from src.lib.domain_packs.value_presence import missing_resolved_value
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
    ``resolvable_values.lookup_outcome_for_failure``. The order matters,
    because a decisive outcome (not_found, ambiguous, conflict,
    rejected_candidates) overrules a value that reads as resolved:

    1. A lookup that could not run (an ``error`` or ``blocked`` outcome)
       makes the whole result non-decisive (transient, blocked): an outage
       never overrules a value, whatever the other lookups found.
    2. A result that filled some expected fields but not others is
       incomplete (missing_expected_result_field). An unresolved result that
       filled none lists every expected field as missing whatever the
       reason, so that list alone does not decide.
    3. Otherwise what the lookups found decides: several matches
       (ambiguous), a conflict, nothing anywhere (not_found), or lookups that
       found something the validator judged does not fit
       (rejected_candidates).
    """

    methods = {attempt.method for attempt in result.lookup_attempts}
    if "invalid_schema" in methods:
        return "invalid_schema"
    if "validator_agent_error" in methods:
        return "transient"
    outcomes = {attempt.outcome for attempt in result.lookup_attempts}
    if "error" in outcomes:
        return "transient"
    if "blocked" in outcomes:
        return "blocked"
    filled = any(not missing_resolved_value(value) for value in result.resolved_values.values())
    if result.missing_expected_fields and (filled or not outcomes):
        # Some expected fields filled and others not, or nothing looked up: incomplete.
        return "missing_expected_result_field"
    if "ambiguous" in outcomes:
        return "ambiguous"
    if "conflict" in outcomes:
        return "conflict"
    if outcomes == {"not_found"}:
        return "not_found"
    if "success" in outcomes:
        # A lookup found something, and the validator judged no candidate fits.
        return "rejected_candidates"
    raise error_type(
        "Unable to classify unresolved validator result "
        f"{result.request_id!r} with lookup outcomes {sorted(outcomes)!r} "
        f"and methods {sorted(methods)!r}"
    )
