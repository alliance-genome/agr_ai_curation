"""A paper delete refuses before touching anything when a stored path is outside the curator's storage.

Real PostgreSQL, the test Weaviate and the REAL ``delete_document`` (pieces, index object
and the SQL row): the containment check must run before any of them, so a refused
delete leaves the paper exactly as it was (ALL-1460, P7c2 review R1). Also on the real
index: empty paper folders are removed with ``rmdir`` only (R4). All values are made up.
"""

# ruff: noqa: F811 - pytest fixtures are imported from the P7c1 module and used by name.

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from src.api import documents as documents_api
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile

from .conftest import TEST_USER_ID
from .test_supplement_add_search_index import (  # noqa: F401
    _indexes,
    _paper,
    _paper_object,
    _rows,
    index,
    migrated_database,
    owner_id,
)


@pytest.fixture
def real_delete(monkeypatch, owner_id):
    """The real delete endpoint and the real search-index delete; only the login is a stand-in."""

    async def provision(_principal):
        return SimpleNamespace(id=owner_id, auth_sub=TEST_USER_ID)

    monkeypatch.setattr(documents_api, "provision_user_async", provision)


def _stored(root: Path) -> set[str]:
    return {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file() or path.is_symlink()}


def _write(root: Path, relative: str, content: bytes = b"made-up") -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return relative


def _with_stored_files(paper: UUID, root: Path) -> None:
    with SessionLocal() as db:
        for row in db.query(PDFDocumentFile).filter_by(document_id=paper):
            for relative in (row.file_path, row.processed_json_path, row.source_markdown_path):
                if relative and ".." not in relative:
                    _write(root, relative)


def _set(paper: UUID, **columns) -> None:
    with SessionLocal.begin() as db:
        row = db.query(PDFDocumentFile).filter_by(document_id=paper, role="main").one()
        for key, value in columns.items():
            setattr(row, key, value)


@pytest.mark.asyncio
@pytest.mark.parametrize("column", ["file_path", "processed_json_path"])
async def test_a_path_outside_the_curators_storage_refuses_the_delete_before_anything_is_deleted(
    owner_id, index, real_delete, tmp_path, column,
):
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    _set(paper, processed_json_path=f"{TEST_USER_ID}/processed_json/{paper}.json")
    _with_stored_files(paper, tmp_path)
    outside = _write(tmp_path, "another-curator/not-yours.pdf")
    _set(paper, **{column: f"{TEST_USER_ID}/../another-curator/not-yours.pdf"})
    files_before, pieces_before = _stored(tmp_path), _indexes(index, str(paper))

    with pytest.raises(HTTPException) as refused:
        await documents_api.delete_document_endpoint(document_id=str(paper), user={"sub": TEST_USER_ID})

    assert refused.value.status_code == 403
    with SessionLocal() as db:
        assert db.get(PDFDocument, paper) is not None  # the paper is still in the Library
    assert _indexes(index, str(paper)) == pieces_before == [0, 1, 2]
    assert _paper_object(index, paper)["processingStatus"] == "completed"
    assert _stored(tmp_path) == files_before and outside in files_before


@pytest.mark.asyncio
@pytest.mark.parametrize("column", ["file_path", "processed_json_path"])
async def test_a_stored_path_that_names_a_folder_refuses_the_delete_before_anything_is_deleted(
    owner_id, index, real_delete, tmp_path, column,
):
    # Only a corrupted row names a folder where a file is expected: refused like a path
    # outside storage, so the delete stays all-or-nothing (review N1).
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    _set(paper, processed_json_path=f"{TEST_USER_ID}/processed_json/{paper}.json")
    _with_stored_files(paper, tmp_path)
    folder = tmp_path / TEST_USER_ID / "a-folder"
    folder.mkdir(parents=True)
    _write(tmp_path, f"{TEST_USER_ID}/a-folder/inside.txt")
    _set(paper, **{column: f"{TEST_USER_ID}/a-folder"})
    files_before, pieces_before = _stored(tmp_path), _indexes(index, str(paper))

    with pytest.raises(HTTPException) as refused:
        await documents_api.delete_document_endpoint(document_id=str(paper), user={"sub": TEST_USER_ID})

    assert refused.value.status_code == 403
    with SessionLocal() as db:
        assert db.get(PDFDocument, paper) is not None
    assert _indexes(index, str(paper)) == pieces_before == [0, 1, 2]
    assert _stored(tmp_path) == files_before


@pytest.mark.asyncio
async def test_empty_paper_folders_are_removed_and_a_folder_with_anything_left_is_not(
    owner_id, index, real_delete, tmp_path,
):
    # The main file lives at the curator's root (an older layout), so the paper folder
    # holds only its supplement PDF: once that is unlinked, the empty folders go too.
    paper = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    _set(paper, file_path=f"{TEST_USER_ID}/made-up-root-{paper}.pdf")
    _with_stored_files(paper, tmp_path)
    pdf = _write(tmp_path, f"{TEST_USER_ID}/{paper}/supplement_pdf/made-up.pdf")
    with SessionLocal.begin() as db:
        db.add(PDFDocumentFile(
            document_id=paper, role="supplement", display_name="Made_up_S9.pdf", status="ready", tab_order=1,
            display_mode="local_pdf", file_path=pdf, file_hash="2" * 64, file_size=7, page_count=1,
            chunk_index_start=3, chunk_index_end=3, text_bytes=7,
        ))
    other = await asyncio.to_thread(_paper, owner_id, index, main_end=2)
    _with_stored_files(other, tmp_path)

    result = await documents_api.delete_document_endpoint(document_id=str(paper), user={"sub": TEST_USER_ID})

    assert result.success is True
    assert not (tmp_path / TEST_USER_ID / str(paper)).exists()
    assert (tmp_path / TEST_USER_ID / str(other)).is_dir()  # another paper's folder is untouched
    assert _rows(paper) == []
