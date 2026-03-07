"""Parity tests for shared processing status policy usage across APIs."""

from types import SimpleNamespace

import pytest

from src.api import documents, processing
from src.models.document import ProcessingStatus
from src.models.pipeline import ProcessingStage
from src.models.sql.pdf_processing_job import PdfJobStatus
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


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (ProcessingStage.UPLOAD, ProcessingStatus.PROCESSING.value),
        (ProcessingStage.PARSING, ProcessingStatus.PARSING.value),
        (" EMBEDDING ", ProcessingStatus.EMBEDDING.value),
        (ProcessingStage.COMPLETED.value, ProcessingStatus.COMPLETED.value),
        ("custom-stage", ProcessingStatus.PENDING.value),
        (None, ProcessingStatus.PENDING.value),
    ],
)
def test_processing_status_policy_maps_pipeline_stages(stage, expected):
    assert processing_status_policy.processing_status_for_pipeline_stage(stage) == expected


def test_processing_status_policy_stage_helpers_normalize_current_stage_values():
    pipeline_status = SimpleNamespace(current_stage=" CHUNKING ")

    assert processing_status_policy.stage_value(" EMBEDDING ") == ProcessingStage.EMBEDDING.value
    assert processing_status_policy.pipeline_stage_value(pipeline_status) == ProcessingStage.CHUNKING.value
    assert processing_status_policy.pipeline_stage_value(None) == ProcessingStage.PENDING.value


@pytest.mark.parametrize(
    ("current_stage", "expected"),
    [
        (ProcessingStage.COMPLETED, True),
        ("FAILED", True),
        (ProcessingStage.EMBEDDING, False),
        ("custom-stage", False),
        (None, False),
    ],
)
def test_processing_status_policy_terminal_detection(current_stage, expected):
    pipeline_status = SimpleNamespace(current_stage=current_stage)

    assert processing_status_policy.is_pipeline_status_terminal(pipeline_status) is expected


@pytest.mark.parametrize(
    ("job_status", "expected"),
    [
        (PdfJobStatus.PENDING.value, ProcessingStatus.PENDING.value),
        (PdfJobStatus.RUNNING.value, ProcessingStatus.PROCESSING.value),
        (PdfJobStatus.CANCEL_REQUESTED.value, ProcessingStatus.PROCESSING.value),
        (PdfJobStatus.COMPLETED.value, ProcessingStatus.COMPLETED.value),
        (PdfJobStatus.CANCELLED.value, ProcessingStatus.FAILED.value),
        (PdfJobStatus.FAILED.value, ProcessingStatus.FAILED.value),
    ],
)
def test_processing_status_policy_job_status_mapping_covers_public_surface(job_status, expected):
    assert processing_status_policy.PDF_JOB_STATUS_TO_PROCESSING_STATUS[job_status] == expected
