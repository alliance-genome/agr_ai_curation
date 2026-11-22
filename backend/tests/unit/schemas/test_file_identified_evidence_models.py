"""Evidence models accept the file an evidence names, and its page rule (ALL-1460, P8b3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.lib.openai_agents.models import PdfEvidenceRecord
from src.schemas.evidence_workspace import (
    evidence_record_page,
    evidence_record_section,
    project_to_provenance,
)
from src.schemas.models.base import EvidenceRecord

FILE_ID = "6f1c2a9e-5b4d-4c3e-9a8b-7d6e5f4a3b2c"


def test_the_canonical_record_and_fragment_carry_the_file():
    record = EvidenceRecord.model_validate(
        {
            "evidence_record_id": "evidence-1",
            "verified_quote": "| abc-1 | 3 | 5 |",
            "file_id": f" {FILE_ID} ",
            "file_name": "Table_S1.xlsx",
            "source_fragments": [{"text": "| abc-1 | 3 | 5 |", "file_id": FILE_ID, "file_name": "Table_S1.xlsx"}],
        }
    )

    assert record.file_id == FILE_ID
    assert record.source_fragments[0].file_name == "Table_S1.xlsx"


def test_strict_projection_keeps_the_file_fields():
    projected = project_to_provenance(
        {"evidence_record_id": "evidence-1", "file_id": FILE_ID, "file_name": "Table_S1.xlsx",
         "source_fragments": [{"text": "x", "file_id": FILE_ID}], "status": "verified"},
    )

    assert projected.file_id == FILE_ID


def test_a_record_saved_before_files_were_recorded_dumps_as_before():
    stored = {"evidence_record_id": "evidence-1", "verified_quote": "Quote.", "page": 3}

    assert EvidenceRecord.model_validate(stored).model_dump(mode="json", exclude_none=True) == stored


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"page": 3}, (True, 3)),
        ({"page": 3, "file_id": FILE_ID}, (True, 3)),
        ({"page": None, "file_id": FILE_ID}, (True, None)),
        ({"file_id": FILE_ID}, (True, None)),
        ({}, (False, None)),
        ({"page": None}, (False, None)),
        ({"page": None, "file_id": "  "}, (False, None)),
        ({"page": 0, "file_id": FILE_ID}, (False, None)),
        ({"page": True, "file_id": FILE_ID}, (False, None)),
        ({"page": "3", "file_id": FILE_ID}, (False, None)),
    ],
)
def test_the_one_page_rule(record, expected):
    assert evidence_record_page(record) == expected


def _pdf_record(**overrides):
    record = {"entity": "abc-1", "verified_quote": "Quote.", "page": 3, "section": "Results", "chunk_id": "c-1"}
    record.update(overrides)
    return record


def test_the_generic_pdf_record_needs_a_page_unless_it_names_a_file():
    assert PdfEvidenceRecord.model_validate(_pdf_record(page=None, file_id=FILE_ID)).page is None
    assert PdfEvidenceRecord.model_validate(_pdf_record()).page == 3
    with pytest.raises(ValidationError, match="page is required unless the evidence names a file"):
        PdfEvidenceRecord.model_validate(_pdf_record(page=None))



@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"section": " Results "}, (True, "Results")),
        ({"section": None, "file_id": FILE_ID}, (True, None)),
        ({"file_id": FILE_ID}, (True, None)),
        ({"section": "", "file_id": FILE_ID}, (True, None)),
        ({}, (False, None)),
        ({"section": "   "}, (False, None)),
        ({"section": 5, "file_id": FILE_ID}, (False, None)),
    ],
)
def test_the_one_section_rule(record, expected):
    assert evidence_record_section(record) == expected


def test_the_generic_pdf_record_needs_a_section_unless_it_names_a_file():
    assert PdfEvidenceRecord.model_validate(_pdf_record(section=None, file_id=FILE_ID)).section is None
    with pytest.raises(ValidationError, match="section is required unless the evidence names a file"):
        PdfEvidenceRecord.model_validate(_pdf_record(section=None))
