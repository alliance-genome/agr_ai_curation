"""Deterministic evidence-quality scoring for resolved curation evidence anchors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from src.schemas.curation_workspace import (
    CurationEvidenceQualityCounts,
    CurationEvidenceSummary,
    EvidenceAnchor,
    EvidenceLocatorQuality,
)


QUOTE_QUALITIES = {
    EvidenceLocatorQuality.EXACT_QUOTE,
    EvidenceLocatorQuality.NORMALIZED_QUOTE,
}

PAGE_ONLY_DEGRADED_RATIO_THRESHOLD = 0.5


class EvidenceRecordLike(Protocol):
    """Minimal evidence-record shape needed for summary aggregation."""

    anchor: Any
    warnings: Sequence[str] | None


@dataclass(frozen=True)
class _EvidenceAnchorSnapshot:
    locator_quality: EvidenceLocatorQuality
    viewer_highlightable: bool


def compute_viewer_highlightable(anchor: EvidenceAnchor) -> bool:
    """Return whether an anchor has enough quote text for viewer-layer highlighting."""

    if anchor.locator_quality not in QUOTE_QUALITIES:
        return False

    return bool(_normalized_viewer_search_text(anchor.viewer_search_text))


def enrich_evidence_anchor(anchor: EvidenceAnchor | Mapping[str, Any]) -> EvidenceAnchor:
    """Return an anchor with deterministic evidence-quality metadata applied."""

    validated_anchor = (
        anchor
        if isinstance(anchor, EvidenceAnchor)
        else EvidenceAnchor.model_validate(anchor)
    )
    return validated_anchor.model_copy(
        update={"viewer_highlightable": compute_viewer_highlightable(validated_anchor)}
    )


def evidence_anchor_payload_with_quality(
    anchor: EvidenceAnchor | Mapping[str, Any],
) -> dict[str, Any]:
    """Return a JSON-serializable anchor payload with computed quality metadata."""

    return enrich_evidence_anchor(anchor).model_dump(mode="json")


def summarize_evidence_anchors(
    anchors: Sequence[EvidenceAnchor | Mapping[str, Any]],
    *,
    extra_warnings: Sequence[str] = (),
) -> CurationEvidenceSummary:
    """Aggregate evidence-quality metrics across resolved anchors."""

    snapshots = [_snapshot_from_anchor(anchor) for anchor in anchors]
    return _summary_from_snapshots(snapshots, extra_warnings=extra_warnings)


def summarize_evidence_records(
    records: Sequence[EvidenceRecordLike],
) -> CurationEvidenceSummary | None:
    """Aggregate evidence quality across persisted or prepared evidence records."""

    if not records:
        return None

    snapshots = [_snapshot_from_anchor(record.anchor) for record in records]
    record_warnings = [
        warning
        for record in records
        for warning in (record.warnings or [])
    ]
    return _summary_from_snapshots(snapshots, extra_warnings=record_warnings)


def _summary_from_snapshots(
    snapshots: Sequence[_EvidenceAnchorSnapshot],
    *,
    extra_warnings: Sequence[str],
) -> CurationEvidenceSummary:
    quality_counts = CurationEvidenceQualityCounts()

    for snapshot in snapshots:
        if snapshot.locator_quality is EvidenceLocatorQuality.EXACT_QUOTE:
            quality_counts.exact_quote += 1
        elif snapshot.locator_quality is EvidenceLocatorQuality.NORMALIZED_QUOTE:
            quality_counts.normalized_quote += 1
        elif snapshot.locator_quality is EvidenceLocatorQuality.SECTION_ONLY:
            quality_counts.section_only += 1
        elif snapshot.locator_quality is EvidenceLocatorQuality.PAGE_ONLY:
            quality_counts.page_only += 1
        elif snapshot.locator_quality is EvidenceLocatorQuality.DOCUMENT_ONLY:
            quality_counts.document_only += 1
        else:
            quality_counts.unresolved += 1

    total_anchor_count = len(snapshots)
    viewer_highlightable_anchor_count = sum(
        1 for snapshot in snapshots if snapshot.viewer_highlightable
    )
    resolved_anchor_count = (
        quality_counts.exact_quote
        + quality_counts.normalized_quote
        + quality_counts.section_only
        + quality_counts.page_only
    )

    computed_warnings = _computed_warnings(
        total_anchor_count=total_anchor_count,
        viewer_highlightable_anchor_count=viewer_highlightable_anchor_count,
        quality_counts=quality_counts,
    )

    degraded = _is_degraded(
        total_anchor_count=total_anchor_count,
        quality_counts=quality_counts,
        extra_warnings=extra_warnings,
    )

    return CurationEvidenceSummary(
        total_anchor_count=total_anchor_count,
        resolved_anchor_count=resolved_anchor_count,
        viewer_highlightable_anchor_count=viewer_highlightable_anchor_count,
        quality_counts=quality_counts,
        degraded=degraded,
        warnings=_dedupe_strings([*computed_warnings, *extra_warnings]),
    )


def _computed_warnings(
    *,
    total_anchor_count: int,
    viewer_highlightable_anchor_count: int,
    quality_counts: CurationEvidenceQualityCounts,
) -> list[str]:
    warnings: list[str] = []
    if quality_counts.unresolved:
        warnings.append(
            _count_message(
                quality_counts.unresolved,
                singular="evidence anchor could not be localized to PDF text or metadata",
                plural="evidence anchors could not be localized to PDF text or metadata",
            )
        )
    if quality_counts.document_only:
        warnings.append(
            _count_message(
                quality_counts.document_only,
                singular="evidence anchor resolved only at the document level",
                plural="evidence anchors resolved only at the document level",
            )
        )
    if quality_counts.page_only:
        warnings.append(
            _count_message(
                quality_counts.page_only,
                singular="evidence anchor resolved only to a page-level location",
                plural="evidence anchors resolved only to page-level locations",
            )
        )

    quote_anchor_count = quality_counts.exact_quote + quality_counts.normalized_quote
    if quote_anchor_count > viewer_highlightable_anchor_count:
        missing_highlight_count = quote_anchor_count - viewer_highlightable_anchor_count
        warnings.append(
            _count_message(
                missing_highlight_count,
                singular="quote-based evidence anchor is missing viewer search text for PDF highlighting",
                plural="quote-based evidence anchors are missing viewer search text for PDF highlighting",
            )
        )
    elif total_anchor_count > 0 and viewer_highlightable_anchor_count == 0:
        warnings.append("No evidence anchors can be highlighted in the PDF viewer text layer.")

    return warnings


def _is_degraded(
    *,
    total_anchor_count: int,
    quality_counts: CurationEvidenceQualityCounts,
    extra_warnings: Sequence[str],
) -> bool:
    if extra_warnings:
        return True
    if quality_counts.document_only or quality_counts.unresolved:
        return True
    if total_anchor_count == 0:
        return False

    return (
        quality_counts.page_only / total_anchor_count
    ) >= PAGE_ONLY_DEGRADED_RATIO_THRESHOLD


def _snapshot_from_anchor(
    anchor: EvidenceAnchor | Mapping[str, Any] | Any,
) -> _EvidenceAnchorSnapshot:
    if isinstance(anchor, EvidenceAnchor):
        enriched_anchor = enrich_evidence_anchor(anchor)
        return _EvidenceAnchorSnapshot(
            locator_quality=enriched_anchor.locator_quality,
            viewer_highlightable=enriched_anchor.viewer_highlightable,
        )

    if isinstance(anchor, Mapping):
        locator_quality = _coerce_locator_quality(anchor.get("locator_quality"))
        return _EvidenceAnchorSnapshot(
            locator_quality=locator_quality,
            viewer_highlightable=(
                locator_quality in QUOTE_QUALITIES
                and bool(_normalized_viewer_search_text(anchor.get("viewer_search_text")))
            ),
        )

    return _EvidenceAnchorSnapshot(
        locator_quality=EvidenceLocatorQuality.UNRESOLVED,
        viewer_highlightable=False,
    )


def _coerce_locator_quality(value: Any) -> EvidenceLocatorQuality:
    try:
        return EvidenceLocatorQuality(str(value))
    except ValueError:
        return EvidenceLocatorQuality.UNRESOLVED


def _normalized_viewer_search_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _count_message(count: int, *, singular: str, plural: str) -> str:
    label = singular if count == 1 else plural
    return f"{count} {label}."


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
    return output


__all__ = [
    "compute_viewer_highlightable",
    "enrich_evidence_anchor",
    "evidence_anchor_payload_with_quality",
    "summarize_evidence_anchors",
    "summarize_evidence_records",
]
