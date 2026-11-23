"""The paper files an extraction result read, on real PostgreSQL (KANBAN-1976 / ALL-1460, P8b2).

Reads write one history row per newly shown file per agent invocation; a saved
result stores the files of its producing invocation (or of its whole run), in
tab order, with role and converted artifact from the file row; a file removed
before the result was saved keeps the name captured when it was read; a result
saved before this release has NULL and its served record has no ``files_read``
key. All values are made up.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from src.lib.curation_workspace.extraction_results import (
    _record_to_schema,
    persist_extraction_result,
)
from src.lib.curation_workspace.models import CurationExtractionResultRecord as ExtractionResultModel
from src.lib.document_cleanup import cleanup_document_curation_dependencies
from src.lib.document_files.files_read import FILES_READ_KIND
from src.lib.openai_agents.run_history import HistoryScope, HistoryStore
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile
from src.schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionSourceKind,
)
from tests.pdf_document_test_support import ensure_test_pdf_owner, new_paper


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREFIX = "p8b2_files_read_test_"
PREVIOUS_HEAD = "d1460fa1b2c3"
REVISION = "c1460f9a0b1d"


def _config() -> Config:
    return Config(str(BACKEND_ROOT / "alembic.ini"))


@pytest.fixture
def paper():
    """A paper with a main PDF (tab 0), a sheet (tab 1) and a Word file (tab 2)."""

    command.upgrade(_config(), "head")
    with SessionLocal.begin() as db:
        owner_id = ensure_test_pdf_owner(db, auth_sub=f"p8b2-owner-{uuid4()}")
        document_id = uuid4()
        row = new_paper(
            id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf",
            status="completed", file_path=f"owner/{document_id}/made-up.pdf",
            file_hash=document_id.hex * 2, file_size=100, page_count=2,
            chunk_index_start=0, chunk_index_end=4, chunk_count=5, display_name="paper.pdf",
        )
        for tab, name in ((1, "Table S1.xlsx"), (2, "Data S2.docx")):
            row.files.append(PDFDocumentFile(
                role="supplement", display_name=name, status="ready", tab_order=tab,
                display_mode="text", file_path=f"owner/{document_id}/{tab}", file_hash="0" * 64,
                file_size=10, source_provider_converted_artifact_id=f"artifact-{tab}",
                source_converted_artifact_sha256=f"{tab}" * 64,
            ))
        db.add(row)
        db.flush()
        file_ids = {file.display_name: str(file.id) for file in row.files}
    yield str(document_id), file_ids
    with SessionLocal.begin() as db:
        for stored in db.scalars(select(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%"))):
            cleanup_document_curation_dependencies(db, stored.id)
            db.delete(stored)


def _read(document_id: str, scope: HistoryScope, files: list[tuple[str, str]]) -> None:
    payload = {"document_id": document_id, "generation": "generation-a",
               "files": [{"file_id": file_id, "file_name": name} for file_id, name in files]}
    HistoryStore(SessionLocal).append(scope, FILES_READ_KIND, json.dumps(payload, sort_keys=True))


def _request(document_id: str, owner: str, run_id: str, *, invocation_id: str | None, writers=()):
    return CurationExtractionPersistenceRequest(
        document_id=document_id, adapter_key="reference_adapter", agent_key="gene_extractor",
        source_kind=CurationExtractionSourceKind.CHAT, trace_id=run_id, user_id=owner,
        candidate_count=1, payload_json={"items": [{"gene": "made-up"}]},
        history_invocation_ids=None if invocation_id is None else [invocation_id, *writers],
    )


def test_a_result_stores_the_files_its_invocation_read_in_tab_order(paper):
    document_id, file_ids = paper
    owner, run_id = f"owner-{uuid4()}", str(uuid4())
    extractor = HistoryScope(owner, run_id, str(uuid4()), "extractor", "x/", "chat-a")
    other = HistoryScope(owner, run_id, str(uuid4()), "other", "y/", "chat-a")
    _read(document_id, extractor, [(file_ids["Data S2.docx"], "Data S2.docx")])
    _read(document_id, extractor, [(file_ids["paper.pdf"], "paper.pdf")])
    _read(document_id, other, [(file_ids["Table S1.xlsx"], "Table S1.xlsx")])

    with SessionLocal.begin() as db:
        saved = persist_extraction_result(
            _request(document_id, owner, run_id, invocation_id=extractor.invocation_id), db=db,
        ).extraction_result
        whole_run = persist_extraction_result(
            _request(document_id, owner, run_id, invocation_id=None), db=db,
        ).extraction_result
        with_writer = persist_extraction_result(
            _request(document_id, owner, run_id, invocation_id=extractor.invocation_id,
                     writers=[other.invocation_id]), db=db,
        ).extraction_result

    assert saved.files_read is not None
    assert [entry.model_dump() for entry in saved.files_read] == [
        {"file_id": file_ids["paper.pdf"], "file_name": "paper.pdf", "role": "main",
         "converted_artifact_id": None, "converted_artifact_sha256": None},
        {"file_id": file_ids["Data S2.docx"], "file_name": "Data S2.docx", "role": "supplement",
         "converted_artifact_id": "artifact-2", "converted_artifact_sha256": "2" * 64},
    ]
    assert [entry.file_name for entry in whole_run.files_read] == ["paper.pdf", "Table S1.xlsx", "Data S2.docx"]
    # A fan-out writer's reads count toward the extraction agent's result.
    assert [entry.file_name for entry in with_writer.files_read] == ["paper.pdf", "Table S1.xlsx", "Data S2.docx"]


def test_a_result_whose_invocation_read_nothing_records_an_empty_list(paper):
    document_id, _ = paper

    with SessionLocal.begin() as db:
        saved = persist_extraction_result(
            _request(document_id, f"owner-{uuid4()}", str(uuid4()), invocation_id=str(uuid4())), db=db,
        ).extraction_result

    assert saved.files_read == []
    assert saved.model_dump(mode="json")["files_read"] == []


def test_a_file_removed_before_saving_keeps_the_name_read(paper):
    document_id, _ = paper
    owner, run_id = f"owner-{uuid4()}", str(uuid4())
    scope = HistoryScope(owner, run_id, str(uuid4()), "extractor", "x/", "chat-a")
    removed = str(uuid4())
    _read(document_id, scope, [(removed, "Removed S3.csv")])

    with SessionLocal.begin() as db:
        saved = persist_extraction_result(
            _request(document_id, owner, run_id, invocation_id=scope.invocation_id), db=db,
        ).extraction_result

    assert [entry.model_dump() for entry in saved.files_read] == [
        {"file_id": removed, "file_name": "Removed S3.csv", "role": None,
         "converted_artifact_id": None, "converted_artifact_sha256": None},
    ]


def test_a_result_saved_before_files_were_recorded_is_served_unchanged(paper):
    document_id, _ = paper
    with SessionLocal.begin() as db:
        saved = persist_extraction_result(
            _request(document_id, f"owner-{uuid4()}", str(uuid4()), invocation_id=None), db=db,
        ).extraction_result
        # As every row saved before this release: the column is NULL.
        db.execute(text("UPDATE extraction_results SET files_read = NULL WHERE id = :id"),
                   {"id": saved.extraction_result_id})
    with SessionLocal() as db:
        served = _record_to_schema(db.get(ExtractionResultModel, saved.extraction_result_id))

    assert served.files_read is None
    assert "files_read" not in served.model_dump(mode="json")
    assert "files_read" not in json.loads(served.model_dump_json())


def _entry(file_id, name, role="supplement"):
    return {"file_id": file_id, "file_name": name, "role": role,
            "converted_artifact_id": None, "converted_artifact_sha256": None}


def _derived(document_id, inputs):
    return CurationExtractionPersistenceRequest(
        document_id=document_id, adapter_key="reference_adapter", agent_key="curation_prep",
        source_kind=CurationExtractionSourceKind.CHAT, trace_id=str(uuid4()), user_id=f"owner-{uuid4()}",
        candidate_count=1, payload_json={"items": [{"gene": "made-up"}]}, input_files_read=inputs,
    )


def test_a_prepared_result_reads_the_union_of_its_inputs_in_tab_order(paper):
    # ALL-1460: a Prepare for Curation result takes its inputs' files, never a run's reads.
    document_id, file_ids = paper
    removed = str(uuid4())
    word = _entry(file_ids["Data S2.docx"], "Data S2.docx")
    inputs = [
        [word, _entry(removed, "Removed S3.csv")],
        [_entry(file_ids["paper.pdf"], "paper.pdf", "main"), dict(word, file_name="renamed later")],
    ]
    owner, run_id = f"owner-{uuid4()}", str(uuid4())
    # Reads in the prep request's own run must not be used.
    _read(document_id, HistoryScope(owner, run_id, str(uuid4()), "supervisor", "s/", "chat-a"),
          [(file_ids["Table S1.xlsx"], "Table S1.xlsx")])
    request = _derived(document_id, inputs).model_copy(update={"user_id": owner, "trace_id": run_id})

    with SessionLocal.begin() as db:
        saved = persist_extraction_result(request, db=db).extraction_result

    assert [entry.model_dump() for entry in saved.files_read] == [
        _entry(file_ids["paper.pdf"], "paper.pdf", "main"), word, _entry(removed, "Removed S3.csv"),
    ]


def test_a_prepared_result_with_an_unrecorded_input_is_unrecorded(paper):
    document_id, file_ids = paper
    inputs = [[_entry(file_ids["paper.pdf"], "paper.pdf", "main")], None]

    with SessionLocal.begin() as db:
        saved = persist_extraction_result(_derived(document_id, inputs), db=db).extraction_result
        empty = persist_extraction_result(_derived(document_id, [[], []]), db=db).extraction_result

    assert saved.files_read is None
    assert empty.files_read == []


def test_the_migration_adds_a_nullable_column_and_refuses_downgrade(historical_migration_database):
    command.upgrade(_config(), PREVIOUS_HEAD)
    column = ("SELECT is_nullable, data_type FROM information_schema.columns "
              "WHERE table_name = 'extraction_results' AND column_name = 'files_read'")
    with SessionLocal() as db:
        assert db.execute(text(column)).first() is None

    command.upgrade(_config(), REVISION)
    with SessionLocal() as db:
        assert tuple(db.execute(text(column)).first()) == ("YES", "jsonb")

    with pytest.raises(RuntimeError, match="forward-only"):
        command.downgrade(_config(), PREVIOUS_HEAD)
