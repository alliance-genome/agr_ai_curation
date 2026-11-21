"""The paper pinned on a validation observation (KANBAN-1976 / ALL-1460, P7a)."""

from uuid import uuid4

from src.lib.validation_history.store import _source_document_pin
from tests.pdf_document_test_support import new_paper, new_supplements_only_paper


def test_a_paper_pins_its_main_file_hash_and_display_mode():
    paper = new_paper(id=uuid4(), user_id=1, filename="made-up.pdf", file_hash="a" * 64)

    assert _source_document_pin(paper) == {
        "id": str(paper.id), "file_hash": "a" * 64, "viewer_mode": "local_pdf",
    }


def test_a_paper_without_a_main_file_pins_no_file():
    paper = new_supplements_only_paper(id=uuid4(), user_id=1, filename="supplements-only-paper")

    assert _source_document_pin(paper) == {"id": str(paper.id), "file_hash": None, "viewer_mode": None}
