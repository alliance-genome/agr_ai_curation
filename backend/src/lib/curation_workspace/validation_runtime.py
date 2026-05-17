"""Shared validation helpers for pipeline and workspace mutation flows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Sequence

from src.schemas.curation_workspace import (
    CurationValidationCounts,
    DomainEnvelopeValidationFindingProjection,
    DomainEnvelopeValidationStatus,
    DomainEnvelopeValidationSummaryProjection,
    FieldValidationStatus,
    FieldValidationResult,
    ValidationCandidateMatch,
)
from src.schemas.domain_envelope import DomainEnvelope


_FIELD_STATUS_RANK: dict[FieldValidationStatus, int] = {
    FieldValidationStatus.VALIDATED: 0,
    FieldValidationStatus.SKIPPED: 0,
    FieldValidationStatus.OVERRIDDEN: 1,
    FieldValidationStatus.NOT_FOUND: 2,
    FieldValidationStatus.AMBIGUOUS: 3,
    FieldValidationStatus.INVALID_FORMAT: 4,
    FieldValidationStatus.CONFLICT: 5,
}


def dedupe(values: Sequence[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


def field_validation_status(value: Any) -> tuple[FieldValidationStatus, list[str]]:
    if value is None:
        return (
            FieldValidationStatus.INVALID_FORMAT,
            ["Extracted field is empty and needs curator review."],
        )
    if isinstance(value, str) and not value.strip():
        return (
            FieldValidationStatus.INVALID_FORMAT,
            ["Extracted field is blank and needs curator review."],
        )
    if isinstance(value, (list, dict)) and not value:
        return (
            FieldValidationStatus.INVALID_FORMAT,
            ["Extracted field is empty and needs curator review."],
        )
    return (FieldValidationStatus.SKIPPED, [])


def domain_envelope_field_validation_results(
    envelope: DomainEnvelope,
    *,
    envelope_revision: int,
    object_id: str,
    field_keys: Sequence[str],
    resolver: str = "domain_envelope_validation_findings",
) -> tuple[dict[str, FieldValidationResult], list[str]]:
    """Map envelope validation findings into curator-facing field results."""

    from src.lib.domain_packs.materialization import project_validation_summary_projections

    summaries = project_validation_summary_projections(
        envelope,
        envelope_revision=envelope_revision,
    )
    object_summaries = [
        summary
        for summary in summaries
        if summary.object_id == object_id and summary.field_path is None
    ]
    field_summaries_by_path: dict[str, list[DomainEnvelopeValidationSummaryProjection]] = {}
    global_summaries: list[DomainEnvelopeValidationSummaryProjection] = []
    for summary in summaries:
        if summary.object_id is None:
            global_summaries.append(summary)
            continue
        if summary.object_id == object_id and summary.field_path is not None:
            field_summaries_by_path.setdefault(summary.field_path, []).append(summary)

    results: dict[str, FieldValidationResult] = {}
    all_warnings: list[str] = []
    for field_key in field_keys:
        relevant_summaries = [
            *field_summaries_by_path.get(field_key, []),
            *object_summaries,
        ]
        if relevant_summaries:
            result = _field_result_from_validation_summaries(
                relevant_summaries,
                resolver=resolver,
            )
            results[field_key] = result
            all_warnings.extend(result.warnings)
            continue

        warnings = _global_validation_warnings(global_summaries)
        if not warnings:
            warnings = [
                (
                    "No envelope validation findings targeted this field; "
                    "validation status was not inferred from the populated draft value."
                )
            ]
        results[field_key] = FieldValidationResult(
            status=FieldValidationStatus.SKIPPED,
            resolver=resolver,
            warnings=warnings,
        )
        all_warnings.extend(warnings)

    return results, dedupe(all_warnings)


def increment_validation_count(
    counts: CurationValidationCounts,
    status: FieldValidationStatus,
) -> None:
    if status == FieldValidationStatus.VALIDATED:
        counts.validated += 1
    elif status == FieldValidationStatus.AMBIGUOUS:
        counts.ambiguous += 1
    elif status == FieldValidationStatus.NOT_FOUND:
        counts.not_found += 1
    elif status == FieldValidationStatus.INVALID_FORMAT:
        counts.invalid_format += 1
    elif status == FieldValidationStatus.CONFLICT:
        counts.conflict += 1
    elif status == FieldValidationStatus.SKIPPED:
        counts.skipped += 1
    elif status == FieldValidationStatus.OVERRIDDEN:
        counts.overridden += 1


def _field_result_from_validation_summaries(
    summaries: Sequence[DomainEnvelopeValidationSummaryProjection],
    *,
    resolver: str,
) -> FieldValidationResult:
    finding_projections = [
        finding
        for summary in summaries
        for finding in summary.findings
    ]
    status = _highest_field_status(
        _field_status_for_finding(finding) for finding in finding_projections
    )
    warnings = [
        finding.message
        for finding in finding_projections
        if _field_status_for_finding(finding) is not FieldValidationStatus.VALIDATED
    ]
    return FieldValidationResult(
        status=status,
        resolver=resolver,
        candidate_matches=_candidate_matches_for_findings(finding_projections),
        warnings=dedupe(warnings),
    )


def _field_status_for_finding(
    finding: DomainEnvelopeValidationFindingProjection,
) -> FieldValidationStatus:
    if finding.summary_status is DomainEnvelopeValidationStatus.RESOLVED:
        return FieldValidationStatus.VALIDATED
    if finding.summary_status is DomainEnvelopeValidationStatus.WAIVED:
        return FieldValidationStatus.OVERRIDDEN

    code = (finding.code or "").lower()
    details = dict(finding.details)
    lookup_status = _lookup_status_from_details(details)
    failure_classification = _optional_string(details.get("failure_classification"))

    if lookup_status == "not_found" or failure_classification == "not_found":
        return FieldValidationStatus.NOT_FOUND
    if lookup_status == "ambiguous" or failure_classification == "ambiguous":
        return FieldValidationStatus.AMBIGUOUS
    if (
        lookup_status in {"transient", "blocked", "under_development"}
        or failure_classification
        in {
            "transient",
            "blocked",
            "under_development",
            "conflict",
            "missing_expected_result_field",
        }
    ):
        return FieldValidationStatus.CONFLICT
    if lookup_status == "success":
        return FieldValidationStatus.VALIDATED
    if (
        "invalid_format" in code
        or "curie_prefix" in code
        or "invalid_curie" in code
        or "prefix_mismatch" in code
    ):
        return FieldValidationStatus.INVALID_FORMAT
    if finding.summary_status in {
        DomainEnvelopeValidationStatus.PLANNED,
        DomainEnvelopeValidationStatus.BLOCKED,
        DomainEnvelopeValidationStatus.UNRESOLVED,
    }:
        return FieldValidationStatus.CONFLICT
    return FieldValidationStatus.CONFLICT


def _highest_field_status(
    statuses: Sequence[FieldValidationStatus] | Any,
) -> FieldValidationStatus:
    status_list = list(statuses)
    if not status_list:
        return FieldValidationStatus.SKIPPED
    return max(status_list, key=lambda status: _FIELD_STATUS_RANK[status])


def _lookup_status_from_details(details: Mapping[str, Any]) -> str | None:
    lookup_status = _optional_string(details.get("lookup_status"))
    if lookup_status is not None:
        return lookup_status
    lookup_attempts = details.get("lookup_attempts")
    if isinstance(lookup_attempts, list):
        statuses = [
            _optional_string(attempt.get("lookup_status"))
            for attempt in lookup_attempts
            if isinstance(attempt, Mapping)
        ]
        for status in ("blocked", "under_development", "transient", "ambiguous", "not_found", "success"):
            if status in statuses:
                return status
    return None


def _candidate_matches_for_findings(
    findings: Sequence[DomainEnvelopeValidationFindingProjection],
) -> list[ValidationCandidateMatch]:
    matches: list[ValidationCandidateMatch] = []
    seen: set[tuple[str | None, str]] = set()
    for finding in findings:
        raw_matches = finding.details.get("candidate_matches")
        if not isinstance(raw_matches, list):
            continue
        for raw_match in raw_matches:
            if not isinstance(raw_match, Mapping):
                continue
            match = _candidate_match_from_mapping(raw_match)
            if match is None:
                continue
            key = (match.identifier, match.label)
            if key in seen:
                continue
            seen.add(key)
            matches.append(match)
    return matches


def _candidate_match_from_mapping(
    raw_match: Mapping[str, Any],
) -> ValidationCandidateMatch | None:
    identifier = _optional_string(
        raw_match.get("identifier")
        or raw_match.get("candidate_id")
        or raw_match.get("resolved_id")
    )
    label = _optional_string(
        raw_match.get("label")
        or raw_match.get("candidate_label")
        or raw_match.get("resolved_label")
        or raw_match.get("name")
        or raw_match.get("symbol")
        or identifier
    )
    if label is None:
        return None
    score = raw_match.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        score = None
    return ValidationCandidateMatch(
        label=label,
        identifier=identifier,
        matched_value=_optional_string(
            raw_match.get("matched_value")
            or raw_match.get("match_type")
            or raw_match.get("matched_variant")
        ),
        score=score,
    )


def _global_validation_warnings(
    summaries: Sequence[DomainEnvelopeValidationSummaryProjection],
) -> list[str]:
    warnings: list[str] = []
    for summary in summaries:
        status_label = summary.status.value.replace("_", " ")
        for message in summary.messages:
            warnings.append(f"Envelope-level validation is {status_label}: {message}")
    return dedupe(warnings)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


__all__ = [
    "dedupe",
    "domain_envelope_field_validation_results",
    "field_validation_status",
    "increment_validation_count",
]
