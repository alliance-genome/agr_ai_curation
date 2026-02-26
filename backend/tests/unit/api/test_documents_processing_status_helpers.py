"""Unit tests for document processing status helper functions."""

from types import SimpleNamespace

from src.api import documents
from src.models.pipeline import ProcessingStage


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
