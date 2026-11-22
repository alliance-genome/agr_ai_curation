"""Every read keeps to the run's readable files (KANBAN-1976 / ALL-1460, P8a).

A paper here holds three files in the shapes the backend writes: a ready main
PDF (pieces 0..2), a ready text supplement (3..4, no page count) and a
supplement still being added (pending, 5..6). Its seven pieces are stored in
the real test Weaviate, so the range filters are checked by the search index
itself, not by a mock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete

from src.lib.document_files.readable import (
    ReadableFilesError,
    ReadingScope,
    reading_scope,
    resolve_readable_files,
)
from src.lib.openai_agents import document_references as refs
from src.lib.openai_agents.run_history import HistoryScope, HistoryStore
from src.lib.pipeline.store import store_chunks_to_weaviate
from src.lib.weaviate_client import chunks as chunk_reads
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile
from tests.pdf_document_test_support import ensure_test_pdf_owner

from .conftest import TEST_USER_ID


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREFIX = "readable_files_test_"


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


@pytest.fixture
def owner_id():
    with SessionLocal.begin() as db:
        owner = ensure_test_pdf_owner(db, auth_sub=TEST_USER_ID)
    yield owner
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


def _file(document_id: UUID, **values) -> PDFDocumentFile:
    return PDFDocumentFile(document_id=document_id, file_hash=uuid4().hex * 2, file_size=100, **values)


def _paper(owner_id: int) -> dict[str, str]:
    """A completed paper: ready main 0..2, ready text supplement 3..4, pending supplement 5..6."""

    document_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(PDFDocument(
            id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf",
            status="completed", processing_completed_at=datetime.now(timezone.utc),
            next_chunk_index=7,
        ))
        db.flush()
        main = _file(
            document_id, role="main", display_name="main.pdf", status="ready", tab_order=0,
            display_mode="local_pdf", file_path=f"o/{document_id}/main.pdf", page_count=3,
            chunk_index_start=0, chunk_index_end=2, chunk_count=3, text_bytes=300,
        )
        sheet = _file(
            document_id, role="supplement", display_name="Table S1.xlsx", status="ready", tab_order=1,
            display_mode="text", file_path=f"o/{document_id}/s1.md", page_count=None,
            chunk_index_start=3, chunk_index_end=4, chunk_count=2, text_bytes=200,
        )
        adding = _file(
            document_id, role="supplement", display_name="Data S2.docx", status="pending", tab_order=2,
            display_mode="text", file_path=f"o/{document_id}/s2.md", page_count=None,
            chunk_index_start=5, chunk_index_end=6, chunk_count=2,
        )
        db.add_all([main, sheet, adding])
        db.flush()
        return {"document_id": str(document_id), "main": str(main.id), "sheet": str(sheet.id),
                "adding": str(adding.id)}


SECTIONS = {0: "Methods", 1: "Methods", 2: "Results", 3: "Sheet1", 4: "Sheet1", 5: "Methods", 6: "Methods"}


async def _store_pieces(connection, document_id: str) -> None:
    for start, end in ((0, 2), (3, 4), (5, 6)):
        await store_chunks_to_weaviate(
            [
                {
                    "chunk_index": index,
                    "content": f"Piece {index} text.",
                    "element_type": "NarrativeText",
                    "page_number": index + 1 if index <= 2 else None,
                    "section_title": SECTIONS[index],
                    "parent_section": SECTIONS[index],
                    "subsection": "Fly strains" if index in (1, 5) else None,
                    "metadata": {},
                }
                for index in range(start, end + 1)
            ],
            document_id, connection, TEST_USER_ID, chunk_range=(start, end),
        )


@pytest.fixture
def stored_paper(owner_id, weaviate_connection, setup_collections, clean_chunks, monkeypatch):
    import asyncio

    paper = _paper(owner_id)
    asyncio.run(_store_pieces(weaviate_connection, paper["document_id"]))
    monkeypatch.setattr(chunk_reads, "get_connection", lambda: weaviate_connection)
    return paper


def test_readable_files_are_the_ready_files_in_tab_order(owner_id):
    paper = _paper(owner_id)

    readable = resolve_readable_files(paper["document_id"], TEST_USER_ID)

    assert [(file.file_id, file.chunk_index_start, file.chunk_index_end) for file in readable.files] == [
        (paper["main"], 0, 2), (paper["sheet"], 3, 4),
    ]
    assert readable.ranges_text() == "0 to 2 (main.pdf), 3 to 4 (Table S1.xlsx)"
    with reading_scope(ReadingScope.MAIN_FILE_ONLY):
        main_only = resolve_readable_files(paper["document_id"], TEST_USER_ID)
    assert [file.file_id for file in main_only.files] == [paper["main"]]
    assert main_only.ranges_text() == "0 to 2"


def test_another_users_paper_has_no_readable_files(owner_id):
    paper = _paper(owner_id)

    with pytest.raises(ReadableFilesError):
        resolve_readable_files(paper["document_id"], "someone-else")


@pytest.mark.asyncio
async def test_index_reads_and_reference_checks_skip_a_pending_file(stored_paper):
    document_id = stored_paper["document_id"]
    readable = resolve_readable_files(document_id, TEST_USER_ID)

    read = await chunk_reads.get_chunk_by_index(
        chunk_index=3, user_id=TEST_USER_ID, document_id=document_id, readable=readable)
    assert read["chunk_index"] == 3
    assert await chunk_reads.get_chunk_by_index(
        chunk_index=5, user_id=TEST_USER_ID, document_id=document_id, readable=readable) is None
    assert await chunk_reads.get_existing_chunk_indexes(
        chunk_indexes=[0, 4, 5, 6, 9], user_id=TEST_USER_ID, document_id=document_id,
        readable=readable) == {0, 4}


@pytest.mark.asyncio
async def test_neighbors_stop_at_the_edges_of_the_pieces_file(stored_paper):
    document_id = stored_paper["document_id"]
    readable = resolve_readable_files(document_id, TEST_USER_ID)

    last_of_main = await chunk_reads.get_chunk_neighbor_ids(
        document_id=document_id, user_id=TEST_USER_ID, chunk_index=2, readable=readable)
    first_of_sheet = await chunk_reads.get_chunk_neighbor_ids(
        document_id=document_id, user_id=TEST_USER_ID, chunk_index=3, readable=readable)
    last_of_sheet = await chunk_reads.get_chunk_neighbor_ids(
        document_id=document_id, user_id=TEST_USER_ID, chunk_index=4, readable=readable)

    assert last_of_main["previous_chunk_id"] is not None and last_of_main["next_chunk_id"] is None
    assert first_of_sheet["previous_chunk_id"] is None and first_of_sheet["next_chunk_id"] is not None
    assert last_of_sheet["next_chunk_id"] is None


@pytest.mark.asyncio
async def test_section_reads_hierarchy_and_chunk_list_skip_a_pending_file(stored_paper):
    document_id = stored_paper["document_id"]
    readable = resolve_readable_files(document_id, TEST_USER_ID)

    methods = await chunk_reads.get_chunks_by_parent_section(
        document_id, "Methods", TEST_USER_ID, readable=readable)
    strains = await chunk_reads.get_chunks_by_subsection(
        document_id, "Methods", "Fly strains", TEST_USER_ID, readable=readable)
    hierarchy = await chunk_reads.get_document_sections_hierarchical(
        document_id, TEST_USER_ID, readable=readable)
    listed = await chunk_reads.get_chunks(
        document_id, {"page": 1, "page_size": 50}, TEST_USER_ID, readable=readable)

    assert [chunk["chunk_index"] for chunk in methods] == [0, 1]
    assert [chunk["chunk_index"] for chunk in strains] == [1]
    assert {section["name"]: section["chunk_count"] for section in hierarchy["sections"]} == {
        "Methods": 2, "Results": 1, "Sheet1": 2,
    }
    assert [chunk["chunk_index"] for chunk in listed["chunks"]] == [0, 1, 2, 3, 4]
    assert listed["total"] == 5


@pytest.mark.asyncio
async def test_a_batch_reads_the_main_file_only(stored_paper):
    document_id = stored_paper["document_id"]
    with reading_scope(ReadingScope.MAIN_FILE_ONLY):
        readable = resolve_readable_files(document_id, TEST_USER_ID)

    hierarchy = await chunk_reads.get_document_sections_hierarchical(
        document_id, TEST_USER_ID, readable=readable)
    assert [section["name"] for section in hierarchy["sections"]] == ["Methods", "Results"]
    assert await chunk_reads.get_chunk_by_index(
        chunk_index=3, user_id=TEST_USER_ID, document_id=document_id, readable=readable) is None


def test_the_readable_filter_is_the_one_every_search_uses(stored_paper, weaviate_connection):
    """hybrid_search_chunks passes readable_pieces_filter to Weaviate; check its meaning here."""
    from src.lib.weaviate_helpers import get_user_collections

    document_id = stored_paper["document_id"]
    readable = resolve_readable_files(document_id, TEST_USER_ID)
    with weaviate_connection.session() as client:
        collection, _ = get_user_collections(client, TEST_USER_ID)
        found = collection.query.fetch_objects(
            filters=chunk_reads.readable_pieces_filter(document_id, readable), limit=50,
        ).objects

    assert sorted(obj.properties["chunkIndex"] for obj in found) == [0, 1, 2, 3, 4]


def _bind(document_id: str) -> refs.SourceBinding:
    invocation = str(uuid4())
    scope = HistoryScope(TEST_USER_ID, f"run-{invocation}", invocation, "reader", invocation + "/", None)
    return refs._load_source_binding(document_id, TEST_USER_ID, scope, HistoryStore())


def test_a_tool_call_binding_carries_the_readable_files(owner_id):
    paper = _paper(owner_id)

    binding = _bind(paper["document_id"])

    assert [file.file_id for file in binding.readable.files] == [paper["main"], paper["sheet"]]
    refs._verify_source_generation(binding)


def test_a_file_that_becomes_ready_during_a_call_is_harmless(owner_id):
    paper = _paper(owner_id)
    binding = _bind(paper["document_id"])
    with SessionLocal.begin() as db:
        db.get(PDFDocumentFile, UUID(paper["adding"])).status = "ready"

    refs._verify_source_generation(binding)


def test_a_call_whose_readable_file_was_removed_fails(owner_id):
    paper = _paper(owner_id)
    binding = _bind(paper["document_id"])
    with SessionLocal.begin() as db:
        db.delete(db.get(PDFDocumentFile, UUID(paper["sheet"])))

    with pytest.raises(refs.DocumentReferenceError, match="readable file changed"):
        refs._verify_source_generation(binding)


def test_a_file_that_became_ready_during_a_run_is_readable_from_the_next_call(owner_id):
    paper = _paper(owner_id)
    first_call = _bind(paper["document_id"])
    with SessionLocal.begin() as db:
        db.get(PDFDocumentFile, UUID(paper["adding"])).status = "ready"

    next_call = _bind(paper["document_id"])

    assert paper["adding"] not in [file.file_id for file in first_call.readable.files]
    assert [file.file_id for file in next_call.readable.files] == [paper["main"], paper["sheet"], paper["adding"]]


def test_a_file_made_unready_during_a_call_fails_it_by_name(owner_id):
    paper = _paper(owner_id)
    binding = _bind(paper["document_id"])
    with SessionLocal.begin() as db:
        db.get(PDFDocumentFile, UUID(paper["sheet"])).status = "pending"

    with pytest.raises(refs.DocumentReferenceError, match="readable file changed"):
        refs._verify_source_generation(binding)


def test_the_evidence_resolution_fetch_skips_a_pending_file(stored_paper):
    pieces = chunk_reads.fetch_document_chunks_for_resolution(stored_paper["document_id"], TEST_USER_ID)

    assert [piece["chunk_index"] for piece in pieces] == [0, 1, 2, 3, 4]
