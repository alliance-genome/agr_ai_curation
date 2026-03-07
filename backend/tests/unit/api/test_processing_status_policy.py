"""Parity tests for shared processing status policy usage across APIs."""

from types import SimpleNamespace

import pytest

from src.api import documents, processing
from src.models.pipeline import ProcessingStage
from src.services import processing_status_policy


@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("processing", "processing"),
        ("COMPLETED", "completed"),
        ("mystery-state", "pending"),
        (None, "pending"),
    ],
)
def test_documents_and_processing_share_normalization_policy(raw_status, expected):
    assert documents._normalize_processing_status(raw_status) == expected
    assert processing._normalize_processing_status(raw_status) == expected
    assert processing_status_policy.normalize_processing_status(raw_status) == expected


@pytest.mark.parametrize(
    ("current_stage", "expected"),
    [
        (ProcessingStage.CHUNKING, True),
        ("EMBEDDING", True),
        ("custom-stage", False),
        (None, False),
    ],
)
def test_documents_and_processing_share_pipeline_activity_policy(current_stage, expected):
    pipeline_status = SimpleNamespace(current_stage=current_stage)

    assert documents._is_pipeline_status_active(pipeline_status) is expected
    assert processing._is_pipeline_status_active(pipeline_status) is expected
    assert processing_status_policy.is_pipeline_status_active(pipeline_status) is expected


def test_documents_and_processing_share_status_sets():
    assert documents._ACTIVE_PROCESSING_STATUSES == processing._ACTIVE_PROCESSING_STATUSES
    assert documents._ACTIVE_PROCESSING_STATUSES == processing_status_policy.ACTIVE_PROCESSING_STATUSES
    assert documents._ACTIVE_PDF_JOB_STATUSES == processing._ACTIVE_PDF_JOB_STATUSES
    assert documents._ACTIVE_PDF_JOB_STATUSES == processing_status_policy.ACTIVE_PDF_JOB_STATUSES
    assert documents._TERMINAL_PDF_JOB_STATUSES == processing._TERMINAL_PDF_JOB_STATUSES
    assert documents._TERMINAL_PDF_JOB_STATUSES == processing_status_policy.TERMINAL_PDF_JOB_STATUSES
