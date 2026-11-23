"""Pages already in the index count only for a file with real pages, from the stored row (ALL-1460).

Real PostgreSQL rows and the test Weaviate: the file row's ``source_markdown_path`` and
``display_mode`` decide (through ``resolve_readable_files``) whether the pieces' stored
pages are shown, so the rule applies to papers indexed before it. Also: a main paper
imported from converted text and a converted Office file store no page at all. All
values are made up.
"""

# ruff: noqa: F811 - pytest fixtures are imported from the P7c1 module and used by name.

from __future__ import annotations

import asyncio
import hashlib
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from src.lib.document_files.readable import resolve_readable_files
from src.lib.pipeline.store import store_chunks_to_weaviate
from src.lib.weaviate_client import chunks as chunk_reads
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocumentFile

from .conftest import TEST_TENANT_NAME, TEST_USER_ID
from .test_supplement_add_search_index import (  # noqa: F401
    PREFIX,
    _paper,
    index,
    migrated_database,
    owner_id,
)


async def _store_with_page(connection, document_id: str, start: int, end: int, page: int) -> None:
    await store_chunks_to_weaviate(
        [
            {
                "chunk_index": index, "content": f"Piece {index} text.", "element_type": "NarrativeText",
                "page_number": page, "section_title": "Results", "parent_section": "Results", "subsection": None,
                "metadata": {},
            }
            for index in range(start, end + 1)
        ],
        document_id, connection, TEST_USER_ID, chunk_range=(start, end),
    )


def _set_main(paper: UUID, **columns) -> None:
    with SessionLocal.begin() as db:
        row = db.query(PDFDocumentFile).filter_by(document_id=paper, role="main").one()
        for key, value in columns.items():
            setattr(row, key, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("display_mode, source_markdown_path, shown", [
    ("local_pdf", None, [3]),                                   # an upload read by the extraction service
    ("local_pdf", "sub/source_markdown/abc.md", []),             # a main paper imported from ABC's text
    ("local_pdf", "", []),                                       # an empty path is not "no provider text"
    ("text", None, []),                                          # a text file
])
async def test_a_stored_row_decides_whether_stored_pages_are_shown(
    owner_id, index, display_mode, source_markdown_path, shown,
):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    _set_main(paper, display_mode=display_mode, source_markdown_path=source_markdown_path)
    # Pieces stored with page 3, as pieces indexed before the no-marker rule can be.
    await _store_with_page(index, str(paper), 3, 3, page=3)
    _set_main(paper, chunk_index_end=3, chunk_count=4)

    readable = await asyncio.to_thread(resolve_readable_files, str(paper), TEST_USER_ID)
    outline = await chunk_reads.get_document_sections_hierarchical(str(paper), TEST_USER_ID, readable=readable)

    (main,) = readable.files
    assert main.pages_trusted is (shown == [3])
    assert outline["files"][0]["sections"][0]["page_numbers"] == shown
    assert outline["sections"][0]["page_numbers"] == shown


@pytest.mark.asyncio
async def test_a_main_paper_imported_from_converted_text_stores_no_page(owner_id, index, monkeypatch):
    # Guards commit 1 against a later change bringing page 1 back (for example a merge).
    from src.lib.document_sources import ingestion
    from src.models.sql.pdf_document import PDFDocument
    from tests.pdf_document_test_support import new_paper

    monkeypatch.setattr("src.lib.pipeline.hierarchy_resolution.resolve_document_hierarchy",
                        AsyncMock(side_effect=RuntimeError("no model in this test")))

    async def no_locators(chunks, **_kwargs):
        return chunks

    monkeypatch.setattr("src.lib.pipeline.figure_locator_resolution.resolve_figure_locators", no_locators)
    document_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(new_paper(
            id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf", status="processing",
            file_path=f"{TEST_USER_ID}/{document_id}/made-up.pdf", file_hash=document_id.hex * 2,
            file_size=100, page_count=1, next_chunk_index=0,
        ))
    with index.session() as client:
        client.collections.get("PDFDocument").with_tenant(TEST_TENANT_NAME).data.insert(
            uuid=document_id, properties={"filename": "made-up.pdf", "processingStatus": "processing",
                                          "embeddingStatus": "pending", "chunkCount": 0, "vectorCount": 0})
    markdown = "# Made-up title\n\n## Abstract\n\nMade-up abstract.\n\n## Results\n\nMade-up result.\n"
    await ingestion.ingest_provider_markdown_document(
        ingestion.ProviderMarkdownIngestionRequest(
            document_id=str(document_id), user_id=TEST_USER_ID, document_owner_user_id=owner_id,
            markdown=markdown,
            source_provenance={"provider": "abc_literature", "converted_artifact_id": "made-up-1",
                               "converted_artifact_sha256": hashlib.sha256(markdown.encode()).hexdigest(),
                               "access_scope": "global"},
        ),
        weaviate_client=index,
    )

    readable = await asyncio.to_thread(resolve_readable_files, str(document_id), TEST_USER_ID)
    (main,) = readable.files
    pieces = [
        await chunk_reads.get_chunk_by_index(
            chunk_index=index_, user_id=TEST_USER_ID, document_id=str(document_id), readable=readable,
        )
        for index_ in range(main.chunk_index_start, main.chunk_index_end + 1)
    ]
    assert pieces and [piece.get("page_number") for piece in pieces] == [None] * len(pieces)
    assert main.pages_trusted is False
    with SessionLocal() as db:
        assert db.get(PDFDocument, document_id) is not None
