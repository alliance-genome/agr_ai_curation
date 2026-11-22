"""Validation-review packets cite new evidence that names its file by handle (ALL-1460, P8b3)."""

from __future__ import annotations

from src.lib.validation_review.record_source import is_packet_evidence, packet_evidence

FILE_ID = "6f1c2a9e-5b4d-4c3e-9a8b-7d6e5f4a3b2c"


def _record(**extra):
    return {
        "evidence_record_id": "evidence-1",
        "entity": "abc-1",
        "verified_quote": "| abc-1 | 3 | 5 |",
        "section": "Sheet1",
        "chunk_id": "chunk-1",
        "document_id": "doc-1",
        "source_span_ids": ["chunk-1:s0000:c0000-c0017:1a2b3c4d"],
        **extra,
    }


def test_a_record_that_names_its_file_is_packet_evidence():
    record = _record(file_id=FILE_ID, file_name="Table_S1.xlsx", page=None)

    assert is_packet_evidence(record)
    view = packet_evidence(record)
    assert view["file_name"] == "Table_S1.xlsx"
    assert "file_id" not in view
    assert "page" not in view


def test_a_record_saved_before_files_were_recorded_is_still_packet_evidence_unchanged():
    record = _record(page=3)

    assert is_packet_evidence(record)
    assert packet_evidence(record) == {
        "verified_quote": "| abc-1 | 3 | 5 |", "section": "Sheet1", "page": 3, "entity": "abc-1",
    }
