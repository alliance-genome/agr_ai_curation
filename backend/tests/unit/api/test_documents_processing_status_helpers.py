"""Unit tests for document processing status helper functions."""

from types import SimpleNamespace

from src.api import documents
from src.models.pipeline import ProcessingStage
from src.models.sql.pdf_processing_job import PdfJobStatus


def test_normalize_processing_status_accepts_known_values():
    assert documents._normalize_processing_status("processing") == "processing"
    assert documents._normalize_processing_status("COMPLETED") == "completed"


def test_normalize_processing_status_defaults_unknown_to_pending():
    assert documents._normalize_processing_status("mystery-state") == "pending"
    assert documents._normalize_processing_status(None) == "pending"


def test_effective_processing_status_uses_raw_status_without_pipeline_state():
    assert documents._effective_processing_status("failed", None) == "failed"
    assert documents._effective_processing_status("unknown-status", None) == "pending"


def test_effective_processing_status_maps_pipeline_stage_enum():
    pipeline_status = SimpleNamespace(current_stage=ProcessingStage.CHUNKING)
    assert documents._effective_processing_status("pending", pipeline_status) == "chunking"


def test_effective_processing_status_maps_pipeline_stage_string():
    pipeline_status = SimpleNamespace(current_stage="EMBEDDING")
    assert documents._effective_processing_status("pending", pipeline_status) == "embedding"


def test_effective_processing_status_falls_back_when_pipeline_stage_unmapped():
    pipeline_status = SimpleNamespace(current_stage="custom_stage")
    assert documents._effective_processing_status("processing", pipeline_status) == "processing"


def test_canonical_processing_status_prefers_terminal_durable_job_over_pipeline():
    pipeline_status = SimpleNamespace(current_stage=ProcessingStage.COMPLETED)
    job = SimpleNamespace(status=PdfJobStatus.FAILED.value)

    status = documents._canonical_processing_status(
        sql_processing_status="processing",
        weaviate_processing_status="processing",
        pipeline_status=pipeline_status,
        job=job,
    )
    assert status == "failed"


def test_canonical_processing_status_uses_active_pipeline_when_no_durable_job():
    pipeline_status = SimpleNamespace(current_stage=ProcessingStage.CHUNKING)

    status = documents._canonical_processing_status(
        sql_processing_status="processing",
        weaviate_processing_status="processing",
        pipeline_status=pipeline_status,
        job=None,
    )
    assert status == "chunking"


def test_select_progress_snapshot_prefers_terminal_job_over_active_pipeline():
    pipeline_status = SimpleNamespace(current_stage=ProcessingStage.EMBEDDING)
    job = SimpleNamespace(
        status=PdfJobStatus.COMPLETED.value,
        current_stage="embedding",
        progress_percentage=62,
        message="job completed",
        error_message=None,
        updated_at=None,
    )

    snapshot = documents._select_progress_snapshot(pipeline_status=pipeline_status, job=job)
    assert snapshot is not None
    assert snapshot["source"] == "job"
    assert snapshot["stage"] == "completed"
    assert snapshot["is_terminal"] is True


def test_status_snapshot_from_pipeline_uses_terminal_default_message_and_utc_timestamp():
    payload = {
        "current_stage": ProcessingStage.COMPLETED.value,
        "progress_percentage": 100,
        "message": "",
        "updated_at": None,
    }
    pipeline_status = SimpleNamespace(
        current_stage=ProcessingStage.COMPLETED.value,
        model_dump=lambda: payload,
    )

    snapshot = documents._status_snapshot_from_pipeline(pipeline_status)

    assert snapshot["message"] == "Processing completed successfully"
    assert snapshot["status"] == "completed"
    assert snapshot["is_terminal"] is True
    assert snapshot["updated_at"].endswith("+00:00")


def test_is_pipeline_status_terminal_returns_false_for_none():
    assert documents._is_pipeline_status_terminal(None) is False
