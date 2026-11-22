"""Per-file viewer routes: the files list, a file's PDF bytes and a text file's blocks (ALL-1460, P9a).

Rows are real ``PDFDocument`` / ``PDFDocumentFile`` objects in the shapes writers store
(main PDF, text supplement, PDF supplement per I7, a pending supplement). Made-up names.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import pdf_viewer
from src.api.auth import _get_user_from_cookie_impl
from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements
from src.models.sql.database import get_db
from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile


OWNER_SUB = "owner"
OWNER_ID = 7
DOCUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
OTHER_DOCUMENT_ID = UUID("44444444-4444-4444-4444-444444444444")
MAIN_ID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
TEXT_ID = UUID("aaaaaaaa-0000-0000-0000-000000000002")
PDF_SUPPLEMENT_ID = UUID("aaaaaaaa-0000-0000-0000-000000000003")
PENDING_ID = UUID("aaaaaaaa-0000-0000-0000-000000000004")
FOREIGN_FILE_ID = UUID("bbbbbbbb-0000-0000-0000-000000000001")
_TIMESTAMP = datetime(2026, 11, 22, tzinfo=timezone.utc)
_PDF_BYTES = b"%PDF-1.7\n" + bytes(range(256)) * 8


def _file(file_id, *, role, display_name, tab_order, display_mode, status="ready", **columns):
    return PDFDocumentFile(
        id=file_id,
        role=role,
        display_name=display_name,
        status=status,
        tab_order=tab_order,
        display_mode=display_mode,
        added_at=_TIMESTAMP,
        file_hash=f"{tab_order:064x}",
        file_size=4096,
        **columns,
    )


def _paper(*, document_id=DOCUMENT_ID, owner_id=OWNER_ID, document_kind="library", files=None):
    paper = PDFDocument(
        id=document_id,
        filename="made-up-paper.pdf",
        upload_timestamp=_TIMESTAMP,
        last_accessed=_TIMESTAMP,
        status="completed",
        document_kind=document_kind,
        user_id=owner_id,
        next_chunk_index=40,
    )
    for file_row in files if files is not None else _four_files():
        paper.files.append(file_row)
    return paper


def _four_files():
    return [
        _file(
            MAIN_ID, role="main", display_name="made-up-paper.pdf", tab_order=0,
            display_mode="local_pdf", page_count=12, file_path=f"{OWNER_SUB}/pdf/main.pdf",
            processed_json_path=f"{OWNER_SUB}/processed_json/{MAIN_ID}.json",
            chunk_index_start=0, chunk_index_end=29, chunk_count=30, text_bytes=50000,
        ),
        _file(
            TEXT_ID, role="supplement", display_name="Table_S1.xlsx", tab_order=1,
            display_mode="text", page_count=None,
            file_path=f"{OWNER_SUB}/source_markdown/{TEXT_ID}.md",
            source_markdown_path=f"{OWNER_SUB}/source_markdown/{TEXT_ID}.md",
            processed_json_path=f"{OWNER_SUB}/processed_json/{TEXT_ID}.json",
            chunk_index_start=30, chunk_index_end=34, chunk_count=5, text_bytes=3000,
        ),
        _file(
            PDF_SUPPLEMENT_ID, role="supplement", display_name="Figure_S2.pdf", tab_order=2,
            display_mode="local_pdf", page_count=4,
            file_path=f"{OWNER_SUB}/supplement_pdf/{PDF_SUPPLEMENT_ID}.pdf",
            source_markdown_path=f"{OWNER_SUB}/source_markdown/{PDF_SUPPLEMENT_ID}.md",
            processed_json_path=f"{OWNER_SUB}/processed_json/{PDF_SUPPLEMENT_ID}.json",
            chunk_index_start=35, chunk_index_end=39, chunk_count=5, text_bytes=4000,
        ),
        _file(
            PENDING_ID, role="supplement", display_name="Table_S3.docx", tab_order=3,
            display_mode="text", page_count=None, status="pending",
            file_path=f"{OWNER_SUB}/source_markdown/{PENDING_ID}.md",
        ),
    ]


class _Result:
    def __init__(self, record):
        self._record = record

    def scalar_one_or_none(self):
        return self._record


class _Db:
    """Answers ``require_owned_document``'s one lookup by paper id."""

    def __init__(self, *papers):
        self.papers = {paper.id: paper for paper in papers}

    def execute(self, statement):
        document_id = statement.compile().params["id_1"]
        return _Result(self.papers.get(document_id))

    def commit(self):
        return None


@pytest.fixture(autouse=True)
def _principal(monkeypatch):
    monkeypatch.setattr(pdf_viewer, "principal_from_claims", lambda claims: claims)
    monkeypatch.setattr(
        pdf_viewer,
        "provision_user",
        lambda _db, principal: SimpleNamespace(id=OWNER_ID, auth_sub=principal["sub"]),
    )


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_viewer, "get_pdf_storage_path", lambda: tmp_path)
    for relative in (f"{OWNER_SUB}/pdf/main.pdf", f"{OWNER_SUB}/supplement_pdf/{PDF_SUPPLEMENT_ID}.pdf"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_PDF_BYTES)
    elements = markdown_to_pipeline_elements(
        "# Sheet 1\n\n| Strain | Temp |\n|---|---|\n| XS0401 | 25 C |\n\nKept at two temperatures.\n"
    )
    processed = tmp_path / OWNER_SUB / "processed_json" / f"{TEXT_ID}.json"
    processed.parent.mkdir(parents=True, exist_ok=True)
    processed.write_text(json.dumps(elements), encoding="utf-8")
    return tmp_path


def _client(*papers, authenticated=True):
    app = FastAPI()
    app.include_router(pdf_viewer.router)
    database = _Db(*papers)
    app.dependency_overrides[get_db] = lambda: database
    if authenticated:
        app.dependency_overrides[_get_user_from_cookie_impl] = lambda: {"sub": OWNER_SUB}
    return TestClient(app)


def _files_url(document_id=DOCUMENT_ID):
    return f"/api/pdf-viewer/documents/{document_id}/files"


def _content_url(file_id, document_id=DOCUMENT_ID):
    return f"/api/pdf-viewer/documents/{document_id}/files/{file_id}/content"


def _text_url(file_id, document_id=DOCUMENT_ID):
    return f"/api/pdf-viewer/documents/{document_id}/files/{file_id}/text"


# --- the files list ---------------------------------------------------------------


def test_files_list_gives_every_file_in_tab_order_with_urls_only_for_ready_files():
    response = _client(_paper()).get(_files_url())

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "document_id": str(DOCUMENT_ID),
        "files": [
            {
                "file_id": str(MAIN_ID),
                "role": "main",
                "display_name": "made-up-paper.pdf",
                "display_mode": "local_pdf",
                "status": "ready",
                "tab_order": 0,
                "page_count": 12,
                "content_url": _content_url(MAIN_ID),
                "text_url": None,
            },
            {
                "file_id": str(TEXT_ID),
                "role": "supplement",
                "display_name": "Table_S1.xlsx",
                "display_mode": "text",
                "status": "ready",
                "tab_order": 1,
                "page_count": None,
                "content_url": None,
                "text_url": _text_url(TEXT_ID),
            },
            {
                "file_id": str(PDF_SUPPLEMENT_ID),
                "role": "supplement",
                "display_name": "Figure_S2.pdf",
                "display_mode": "local_pdf",
                "status": "ready",
                "tab_order": 2,
                "page_count": 4,
                "content_url": _content_url(PDF_SUPPLEMENT_ID),
                "text_url": None,
            },
            {
                "file_id": str(PENDING_ID),
                "role": "supplement",
                "display_name": "Table_S3.docx",
                "display_mode": "text",
                "status": "pending",
                "tab_order": 3,
                "page_count": None,
                "content_url": None,
                "text_url": None,
            },
        ],
    }


def test_files_list_of_a_supplements_only_paper_needs_no_main_file():
    from tests.pdf_document_test_support import new_supplements_only_paper

    paper = new_supplements_only_paper(
        id=DOCUMENT_ID, user_id=OWNER_ID, filename="supplements-only-paper",
        upload_timestamp=_TIMESTAMP, last_accessed=_TIMESTAMP, document_kind="library",
    )
    paper.files[0].id = TEXT_ID

    response = _client(paper).get(_files_url())

    assert response.status_code == 200
    [only_file] = response.json()["files"]
    assert (only_file["role"], only_file["display_mode"], only_file["page_count"]) == (
        "supplement", "text", None,
    )
    assert only_file["text_url"].endswith("/text")


# --- access -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [_files_url(), _content_url(MAIN_ID), _text_url(TEXT_ID)],
)
def test_every_file_route_requires_a_login(monkeypatch, path):
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("AUTH_PROVIDER", raising=False)

    response = _client(_paper(), authenticated=False).get(path)

    assert response.status_code == 401


@pytest.mark.parametrize("owner_id", [8, None])
@pytest.mark.parametrize(
    "path",
    [_files_url(), _content_url(MAIN_ID), _text_url(TEXT_ID)],
)
def test_another_curators_paper_is_forbidden_on_every_file_route(storage, owner_id, path):
    response = _client(_paper(owner_id=owner_id)).get(path)

    assert response.status_code == 403


@pytest.mark.parametrize(
    "path",
    [_files_url(), _content_url(MAIN_ID), _text_url(TEXT_ID)],
)
def test_a_frozen_benchmark_paper_is_not_found_on_every_file_route(storage, path):
    response = _client(_paper(document_kind="benchmark_frozen")).get(path)

    assert response.status_code == 404


@pytest.mark.parametrize("path", [_files_url(), _content_url(MAIN_ID), _text_url(TEXT_ID)])
def test_an_unknown_paper_is_not_found_on_every_file_route(storage, path):
    response = _client().get(path)

    assert response.status_code == 404


@pytest.mark.parametrize("other_owner_id", [OWNER_ID, 8])
@pytest.mark.parametrize("route", [_content_url, _text_url])
def test_a_file_of_another_paper_is_refused_under_a_paper_the_curator_owns(
    storage, other_owner_id, route
):
    """A file id from another paper (own or another curator's) never resolves under this one."""

    foreign_pdf = _file(
        FOREIGN_FILE_ID, role="main", display_name="other.pdf", tab_order=0,
        display_mode="local_pdf", page_count=2, file_path=f"{OWNER_SUB}/pdf/main.pdf",
    )
    other_paper = _paper(document_id=OTHER_DOCUMENT_ID, owner_id=other_owner_id, files=[foreign_pdf])

    client = _client(_paper(), other_paper)

    assert client.get(route(FOREIGN_FILE_ID)).status_code == 404
    if other_owner_id == OWNER_ID and route is _content_url:
        # The same file id resolves under its own paper, so the refusal above is the pairing.
        assert client.get(route(FOREIGN_FILE_ID, OTHER_DOCUMENT_ID)).status_code == 200


# --- PDF bytes --------------------------------------------------------------------


@pytest.mark.parametrize("file_id", [MAIN_ID, PDF_SUPPLEMENT_ID])
def test_content_serves_that_files_pdf_inline(storage, file_id):
    response = _client(_paper()).get(_content_url(file_id))

    assert response.status_code == 200
    assert response.content == _PDF_BYTES
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["accept-ranges"] == "bytes"


def test_content_names_the_supplement_by_its_display_name(storage):
    response = _client(_paper()).get(_content_url(PDF_SUPPLEMENT_ID))

    assert "Figure_S2.pdf" in response.headers["content-disposition"]


def test_content_delivers_a_requested_byte_range(storage):
    response = _client(_paper()).get(
        _content_url(PDF_SUPPLEMENT_ID), headers={"Range": "bytes=100-199"}
    )

    assert response.status_code == 206
    assert response.content == _PDF_BYTES[100:200]
    assert response.headers["content-range"] == f"bytes 100-199/{len(_PDF_BYTES)}"
    assert response.headers["content-type"] == "application/pdf"


def test_content_answers_head_without_a_body(storage):
    response = _client(_paper()).head(_content_url(MAIN_ID))

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(_PDF_BYTES))
    assert response.headers["content-type"] == "application/pdf"


def test_content_of_a_text_file_is_not_found(storage):
    response = _client(_paper()).get(_content_url(TEXT_ID))

    assert response.status_code == 404


def test_content_of_a_file_that_is_not_ready_is_not_found(storage):
    files = _four_files()
    files[2].status = "pending"

    response = _client(_paper(files=files)).get(_content_url(PDF_SUPPLEMENT_ID))

    assert response.status_code == 404


def test_content_with_missing_bytes_is_not_found(storage):
    (storage / OWNER_SUB / "supplement_pdf" / f"{PDF_SUPPLEMENT_ID}.pdf").unlink()

    response = _client(_paper()).get(_content_url(PDF_SUPPLEMENT_ID))

    assert response.status_code == 404


def test_content_outside_the_owners_folder_is_forbidden(storage):
    files = _four_files()
    files[2].file_path = "someone-else/supplement_pdf/x.pdf"

    response = _client(_paper(files=files)).get(_content_url(PDF_SUPPLEMENT_ID))

    assert response.status_code == 403


# --- text blocks ------------------------------------------------------------------


def test_text_returns_the_files_display_blocks(storage):
    response = _client(_paper()).get(_text_url(TEXT_ID))

    assert response.status_code == 200
    assert response.json() == {
        "document_id": str(DOCUMENT_ID),
        "file_id": str(TEXT_ID),
        "display_name": "Table_S1.xlsx",
        "blocks": [
            {
                "kind": "heading",
                "text": "Sheet 1",
                "level": 1,
                **_OWN_SECTIONS,
                "rows": None,
            },
            {
                "kind": "table",
                "text": "| Strain | Temp |\n|---|---|\n| XS0401 | 25 C |",
                "level": None,
                **_OWN_SECTIONS,
                "rows": [
                    {"line": "| Strain | Temp |", "cells": ["Strain", "Temp"], "is_header": True},
                    {"line": "| XS0401 | 25 C |", "cells": ["XS0401", "25 C"], "is_header": False},
                ],
            },
            {
                "kind": "paragraph",
                "text": "Kept at two temperatures.",
                "level": None,
                **_OWN_SECTIONS,
                "rows": None,
            },
        ],
    }


# A file with no stored section map keeps its elements' own section fields.
_OWN_SECTIONS = {
    "section_title": "Sheet 1",
    "parent_section": None,
    "subsection": None,
    "section_path": ["Sheet 1"],
}


def test_text_applies_the_files_stored_section_map(storage):
    files = _four_files()
    files[1].hierarchy_metadata = {
        "sections": [
            {"header": "Sheet 1", "parent_section": "Table S1", "subsection": "Sheet 1", "is_top_level": False}
        ],
        "top_level_sections": ["Table S1"],
        "created_at": "2026-11-22T00:00:00+00:00",
        "model_used": "made-up-model",
    }

    response = _client(_paper(files=files)).get(_text_url(TEXT_ID))

    paragraph = response.json()["blocks"][2]
    assert (
        paragraph["section_title"], paragraph["parent_section"], paragraph["subsection"], paragraph["section_path"]
    ) == ("Table S1 > Sheet 1", "Table S1", "Sheet 1", ["Table S1", "Sheet 1"])


def test_text_with_a_stored_section_map_of_an_unknown_shape_is_a_named_error(storage):
    files = _four_files()
    files[1].hierarchy_metadata = {"sections": [{"header": "Sheet 1"}]}

    response = _client(_paper(files=files)).get(_text_url(TEXT_ID))

    assert response.status_code == 500
    assert response.json()["detail"] == f"File text could not be shown for file {TEXT_ID}"


@pytest.mark.parametrize("file_id", [MAIN_ID, PDF_SUPPLEMENT_ID])
def test_text_of_a_pdf_file_is_not_found(storage, file_id):
    response = _client(_paper()).get(_text_url(file_id))

    assert response.status_code == 404


def test_text_of_a_file_that_is_not_ready_is_not_found(storage):
    response = _client(_paper()).get(_text_url(PENDING_ID))

    assert response.status_code == 404


def test_text_with_missing_processed_elements_is_not_found(storage):
    (storage / OWNER_SUB / "processed_json" / f"{TEXT_ID}.json").unlink()

    response = _client(_paper()).get(_text_url(TEXT_ID))

    assert response.status_code == 404


def test_text_of_a_ready_file_without_processed_elements_is_not_found(storage):
    files = _four_files()
    files[1].processed_json_path = None

    response = _client(_paper(files=files)).get(_text_url(TEXT_ID))

    assert response.status_code == 404


def test_text_outside_the_owners_folder_is_forbidden(storage):
    files = _four_files()
    files[1].processed_json_path = "someone-else/processed_json/x.json"

    response = _client(_paper(files=files)).get(_text_url(TEXT_ID))

    assert response.status_code == 403


# --- the old single-file routes are unchanged --------------------------------------


def test_the_per_paper_routes_still_answer_as_before(storage):
    client = _client(_paper())

    url = client.get(f"/api/pdf-viewer/documents/{DOCUMENT_ID}/url")
    content = client.get(f"/api/pdf-viewer/documents/{DOCUMENT_ID}/content")

    assert url.json() == {
        "viewer_url": f"/api/pdf-viewer/documents/{DOCUMENT_ID}/content",
        "viewer_mode": "local_pdf",
        "has_main_file": True,
    }
    assert content.status_code == 200
    assert content.content == _PDF_BYTES


@pytest.mark.parametrize("main_status", ["pending", "failed"])
def test_the_old_content_route_still_serves_a_main_pdf_that_is_not_ready(storage, main_status):
    """Today's per-paper route ignores the file's status; P9a leaves it so until P9a2.

    Production has papers whose main file is pending with all their data; they keep
    opening through the old route. Only the new per-file route requires ready.
    """

    files = _four_files()
    files[0].status = main_status
    client = _client(_paper(files=files))

    old_route = client.get(f"/api/pdf-viewer/documents/{DOCUMENT_ID}/content")
    old_url = client.get(f"/api/pdf-viewer/documents/{DOCUMENT_ID}/url")
    new_route = client.get(_content_url(MAIN_ID))

    assert old_route.status_code == 200
    assert old_route.content == _PDF_BYTES
    assert old_url.json()["viewer_url"] == f"/api/pdf-viewer/documents/{DOCUMENT_ID}/content"
    assert new_route.status_code == 404


def test_a_pending_pdf_files_placeholder_page_count_is_not_shown():
    """An identifier import writes its main file pending with page_count 1 as a placeholder."""

    files = _four_files()
    files[0].status = "pending"
    files[0].page_count = 1

    response = _client(_paper(files=files)).get(_files_url())

    main_entry = response.json()["files"][0]
    assert (main_entry["status"], main_entry["page_count"], main_entry["content_url"]) == (
        "pending", None, None,
    )


def test_an_empty_display_name_does_not_break_the_files_list():
    files = _four_files()
    files[1].display_name = ""

    response = _client(_paper(files=files)).get(_files_url())

    assert response.status_code == 200
    assert response.json()["files"][1]["display_name"] == ""
