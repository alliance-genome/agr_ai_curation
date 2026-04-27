"""SQLAlchemy eager-load option groups for curation workspace sessions."""

from __future__ import annotations

from sqlalchemy.orm import selectinload

from src.lib.curation_workspace.models import (
    CurationCandidate,
    CurationReviewSession as ReviewSessionModel,
)

SUMMARY_LOAD_OPTIONS = (
    selectinload(ReviewSessionModel.candidates).selectinload(CurationCandidate.evidence_anchors),
    selectinload(ReviewSessionModel.validation_snapshots),
)


DETAIL_LOAD_OPTIONS = (
    *SUMMARY_LOAD_OPTIONS,
    selectinload(ReviewSessionModel.action_log_entries),
    selectinload(ReviewSessionModel.candidates).selectinload(CurationCandidate.draft),
    selectinload(ReviewSessionModel.candidates).selectinload(CurationCandidate.extraction_result),
    selectinload(ReviewSessionModel.candidates).selectinload(CurationCandidate.validation_snapshots),
    selectinload(ReviewSessionModel.submissions),
)

PREPARED_SESSION_LOAD_OPTIONS = (
    selectinload(ReviewSessionModel.candidates),
    selectinload(ReviewSessionModel.submissions),
    selectinload(ReviewSessionModel.action_log_entries),
)

CANDIDATE_DETAIL_LOAD_OPTIONS = (
    selectinload(CurationCandidate.draft),
    selectinload(CurationCandidate.evidence_anchors),
    selectinload(CurationCandidate.validation_snapshots),
)

__all__ = [
    "CANDIDATE_DETAIL_LOAD_OPTIONS",
    "DETAIL_LOAD_OPTIONS",
    "PREPARED_SESSION_LOAD_OPTIONS",
    "SUMMARY_LOAD_OPTIONS",
]
