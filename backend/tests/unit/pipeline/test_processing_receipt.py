"""Tests for the canonical PDF processing observability receipt."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

import pytest

from src.lib.pipeline import processing_receipt as receipt_module


@pytest.fixture(autouse=True)
def _disable_real_sentry(monkeypatch):
    monkeypatch.setattr(
        receipt_module,
        "pdf_processing_stage_span",
        lambda **_kwargs: nullcontext(None),
    )


def test_receipt_records_successful_stages_and_explicit_cache_state():
    receipt = receipt_module.PDFProcessingReceipt(document_id="doc-1")
    started_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    completed_at = datetime.now(timezone.utc)
    receipt.record_external_observation(
        {
            "status": "completed",
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_ms": 2000.04,
            "extraction_methods": ["grobid", "marker"],
            "merge_enabled": True,
            "download_variant": "merged",
            "cache_hit": True,
        }
    )

    with receipt.observe_stage("chunking"):
        pass

    stored = receipt.finalize("completed")

    assert stored["schema_version"] == 1
    assert stored["outcome"] == "completed"
    assert stored["selection"] == {
        "extraction_methods": ["grobid", "marker"],
        "merge_enabled": True,
        "download_variant": "merged",
        "cache_hit": True,
    }
    assert stored["stages"]["external_request"]["status"] == "completed"
    assert stored["stages"]["external_request"]["duration_ms"] == 2000.0
    assert stored["stages"]["chunking"]["status"] == "completed"
    assert stored["stages"]["hierarchy"] == {"status": "not_started"}
    assert stored["stages"]["total"]["status"] == "completed"


def test_receipt_retains_failed_stage_and_terminal_outcome():
    receipt = receipt_module.PDFProcessingReceipt(document_id="doc-failed")

    with pytest.raises(RuntimeError, match="chunk failed"):
        with receipt.observe_stage("chunking"):
            raise RuntimeError("chunk failed")

    stored = receipt.finalize("failed")

    assert stored["outcome"] == "failed"
    assert stored["stages"]["chunking"]["status"] == "failed"
    assert stored["stages"]["total"]["status"] == "failed"
    assert "chunk failed" not in str(stored)


def test_minimal_cancelled_receipt_uses_observed_job_timestamps():
    started_at = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    completed_at = started_at + timedelta(seconds=4.25)

    stored = receipt_module.minimal_terminal_receipt(
        started_at=started_at,
        completed_at=completed_at,
        outcome="cancelled",
    )

    assert stored["outcome"] == "cancelled"
    assert stored["stages"]["external_request"] == {"status": "not_started"}
    assert stored["stages"]["total"]["duration_ms"] == 4250.0
