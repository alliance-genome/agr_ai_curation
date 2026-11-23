"""What a paper delete removes from document storage, on real PostgreSQL (ALL-1460, P7c2 audit).

Deleting one paper must remove only that paper's stored files: never another paper's
PDF, nor the shared per-user folders that hold every paper's Markdown and processed
JSON. The real delete endpoint runs; only the search-index wipe is a stand-in. All
values are made up.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete

from src.api import documents as documents_api
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile
from tests.pdf_document_test_support import ensure_test_pdf_owner, new_paper

from .conftest import TEST_USER_ID

PREFIX = "p7c2_delete_test_"


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


@pytest.fixture
def storage(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.get_pdf_storage_path", lambda: tmp_path)
    monkeypatch.setattr(documents_api, "get_pdf_storage_path", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def owner_id(monkeypatch):
    with SessionLocal.begin() as db:
        owner = ensure_test_pdf_owner(db, auth_sub=TEST_USER_ID)

    async def provision(_principal):
        return type("User", (), {"id": owner})()

    async def present(_sub, _document_id):
        return {"document": {"processingStatus": "completed"}}

    async def wipe(_user_sub, _document_id):
        return {"success": True, "chunks_deleted": 0}

    monkeypatch.setattr(documents_api, "provision_user_async", provision)
    monkeypatch.setattr(documents_api, "get_document", present)
    monkeypatch.setattr(documents_api, "delete_document", wipe)
    yield owner
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


def _write(root: Path, relative: str, content: bytes = b"made-up") -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return relative


def _paper(root: Path, owner_id: int, *, text_supplement: bool = True) -> tuple[UUID, list[str]]:
    """A paper as main stores it today: its PDF in its own folder (upload and identifier
    import), its processed JSON in the shared folder, and a converted text supplement
    (#1105) whose Markdown and JSON sit in the shared per-user folders."""

    document_id = uuid4()
    sub = TEST_USER_ID
    main_pdf = _write(root, f"{sub}/{document_id}/made-up.pdf", b"%PDF-1.4 made-up")
    main_json = _write(root, f"{sub}/processed_json/{document_id}.json", b"[]")
    paths = [main_pdf, main_json]
    with SessionLocal.begin() as db:
        paper = new_paper(
            id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf", status="completed",
            file_path=main_pdf, processed_json_path=main_json, file_hash=document_id.hex * 2,
            file_size=16, page_count=1, chunk_index_start=0, chunk_index_end=1, chunk_count=2,
        )
        if text_supplement:
            file_id = uuid4()
            markdown = _write(root, f"{sub}/source_markdown/{file_id}.md", b"# Made_up_S1\n")
            processed = _write(root, f"{sub}/processed_json/{file_id}.json", b"[]")
            paths += [markdown, processed]
            paper.files.append(PDFDocumentFile(
                id=file_id, role="supplement", display_name="Made_up_S1.xlsx", status="ready", tab_order=1,
                display_mode="text", file_path=markdown, file_hash="0" * 64, file_size=13,
                source_markdown_path=markdown, processed_json_path=processed,
                chunk_index_start=2, chunk_index_end=2, text_bytes=13,
            ))
        db.add(paper)
    return document_id, paths


def _stored(root: Path) -> set[str]:
    return {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}


@pytest.mark.asyncio
async def test_deleting_one_paper_removes_only_its_own_stored_files(storage, owner_id):
    deleted, deleted_paths = _paper(storage, owner_id)
    kept, kept_paths = _paper(storage, owner_id)
    # Another curator's files under the same storage root.
    other = _write(storage, f"another-curator/{uuid4()}/made-up.pdf")

    result = await documents_api.delete_document_endpoint(document_id=str(deleted), user={"sub": TEST_USER_ID})

    assert result.success is True
    assert _stored(storage) == {*kept_paths, other}
    assert not set(deleted_paths) & _stored(storage)
    # The shared per-user folders stay for the other paper.
    assert (storage / TEST_USER_ID / "source_markdown").is_dir()
    assert (storage / TEST_USER_ID / "processed_json").is_dir()


@pytest.mark.asyncio
async def test_a_main_pdf_stored_at_the_user_root_is_removed_alone(storage, owner_id):
    # Older layouts kept a main PDF directly under the user's folder: only that file goes.
    deleted, _ = _paper(storage, owner_id, text_supplement=False)
    root_pdf = _write(storage, f"{TEST_USER_ID}/made-up-root.pdf")
    with SessionLocal.begin() as db:
        main = db.query(PDFDocumentFile).filter_by(document_id=deleted, role="main").one()
        main.file_path = root_pdf
    kept, kept_paths = _paper(storage, owner_id)

    await documents_api.delete_document_endpoint(document_id=str(deleted), user={"sub": TEST_USER_ID})

    assert root_pdf not in _stored(storage)
    assert set(kept_paths) <= _stored(storage)
    # The deleted paper's own folder (its original PDF) is not referenced by its row any more.
    assert f"{TEST_USER_ID}/{deleted}/made-up.pdf" in _stored(storage)


def _add_pdf_supplement(root: Path, document_id: UUID, *, shared_folder: bool) -> list[str]:
    file_id = uuid4()
    sub = TEST_USER_ID
    folder = f"{sub}/supplement_pdf" if shared_folder else f"{sub}/{document_id}/supplement_pdf"
    pdf = _write(root, f"{folder}/{file_id}.pdf", b"%PDF-1.4 made-up supplement")
    markdown = _write(root, f"{sub}/source_markdown/{file_id}.md", b"## Made-up\n")
    processed = _write(root, f"{sub}/processed_json/{file_id}.json", b"[]")
    with SessionLocal.begin() as db:
        db.add(PDFDocumentFile(
            id=file_id, document_id=document_id, role="supplement", display_name="Made_up_S9.pdf", status="ready",
            tab_order=5, display_mode="local_pdf", file_path=pdf, file_hash="1" * 64, file_size=27, page_count=2,
            source_markdown_path=markdown, processed_json_path=processed,
            chunk_index_start=3, chunk_index_end=3, text_bytes=11,
        ))
    return [pdf, markdown, processed]


@pytest.mark.asyncio
@pytest.mark.parametrize("shared_folder", [False, True], ids=["paper folder", "one shared folder"])
async def test_deleting_one_paper_keeps_another_papers_supplement_pdf(storage, owner_id, shared_folder):
    # A supplement PDF is removed as one file, so even PDFs of two papers kept in one
    # folder (the layout P7c2 rejected) never take each other with them.
    deleted, deleted_paths = _paper(storage, owner_id)
    deleted_paths += _add_pdf_supplement(storage, deleted, shared_folder=shared_folder)
    kept, kept_paths = _paper(storage, owner_id)
    kept_paths += _add_pdf_supplement(storage, kept, shared_folder=shared_folder)

    result = await documents_api.delete_document_endpoint(document_id=str(deleted), user={"sub": TEST_USER_ID})

    assert result.success is True
    assert _stored(storage) == set(kept_paths)


def _move_main_file(document_id: UUID, relative: str) -> None:
    with SessionLocal.begin() as db:
        db.query(PDFDocumentFile).filter_by(document_id=document_id, role="main").one().file_path = relative


@pytest.mark.asyncio
async def test_a_main_pdf_in_a_shared_subfolder_is_removed_alone(storage, owner_id):
    # One rule: a folder goes only when the path proves it is exactly this paper's.
    deleted, _ = _paper(storage, owner_id, text_supplement=False)
    shared = _write(storage, f"{TEST_USER_ID}/uploads/made-up-a.pdf")
    sibling = _write(storage, f"{TEST_USER_ID}/uploads/made-up-b.pdf")
    _move_main_file(deleted, shared)

    await documents_api.delete_document_endpoint(document_id=str(deleted), user={"sub": TEST_USER_ID})

    assert shared not in _stored(storage) and sibling in _stored(storage)


@pytest.mark.asyncio
async def test_a_paper_folder_that_is_a_link_is_never_removed(storage, owner_id):
    deleted, _ = _paper(storage, owner_id, text_supplement=False)
    target = storage / TEST_USER_ID / "elsewhere"
    target.mkdir(parents=True)
    _write(storage, f"{TEST_USER_ID}/elsewhere/made-up.pdf")
    kept = _write(storage, f"{TEST_USER_ID}/elsewhere/kept.pdf")
    real_folder = storage / TEST_USER_ID / str(deleted)
    for child in real_folder.iterdir():
        child.unlink()
    real_folder.rmdir()
    real_folder.symlink_to(target, target_is_directory=True)

    await documents_api.delete_document_endpoint(document_id=str(deleted), user={"sub": TEST_USER_ID})

    assert kept in _stored(storage)
    assert f"{TEST_USER_ID}/elsewhere/made-up.pdf" not in _stored(storage)


@pytest.mark.asyncio
async def test_a_main_file_that_is_a_link_loses_the_link_never_its_target(storage, owner_id, tmp_path_factory):
    # A link is judged by where it sits and only the link is removed, wherever it points:
    # another paper's PDF of the same curator, or a file outside storage.
    deleted, _ = _paper(storage, owner_id, text_supplement=False)
    other, other_paths = _paper(storage, owner_id, text_supplement=False)
    outside = tmp_path_factory.mktemp("outside") / "not-yours.pdf"
    outside.write_bytes(b"%PDF-1.4 not yours")
    link = storage / TEST_USER_ID / str(deleted) / "made-up.pdf"
    link.unlink()
    link.symlink_to(storage / other_paths[0])
    extra = storage / TEST_USER_ID / str(deleted) / "outside-link.pdf"
    extra.symlink_to(outside)
    _write(storage, f"{TEST_USER_ID}/{deleted}/supplement_pdf/kept-link-target-check.txt")

    result = await documents_api.delete_document_endpoint(document_id=str(deleted), user={"sub": TEST_USER_ID})

    assert result.success is True
    assert not link.is_symlink() and not link.exists()
    assert set(other_paths) <= _stored(storage)  # the other paper's PDF, the link's target, is kept
    assert outside.exists()


@pytest.mark.asyncio
async def test_a_paper_delete_removes_the_files_of_a_supplement_still_being_removed(storage, owner_id):
    # A removal whose clean-up did not finish leaves its row `removed` with its paths
    # (#1124): the paper delete finishes it, one file at a time.
    deleted, _ = _paper(storage, owner_id, text_supplement=False)
    leftover = _add_pdf_supplement(storage, deleted, shared_folder=False)
    with SessionLocal.begin() as db:
        row = db.query(PDFDocumentFile).filter_by(document_id=deleted, role="supplement").one()
        row.status = "removed"
    kept, kept_paths = _paper(storage, owner_id)

    result = await documents_api.delete_document_endpoint(document_id=str(deleted), user={"sub": TEST_USER_ID})

    assert result.success is True
    assert not set(leftover) & _stored(storage)
    assert set(kept_paths) <= _stored(storage)
