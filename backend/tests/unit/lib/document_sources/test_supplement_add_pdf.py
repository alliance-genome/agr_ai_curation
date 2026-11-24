"""Adding a converted PDF supplement as its PDF (ALL-1460, P7c2), with fakes for storage.

Listing rows are ABC ``show_all`` shapes through the real Alliance adapter: a
``supplement`` original with extension ``pdf`` and its ``converted_merged_supplement``
text, as ABC's PDF converter writes it (no single title). The PDF is a real one made
here with pypdf; its MD5 is the listing's, as ABC stores the original's MD5. The
database, the search index and the disk are the fakes of ``test_supplement_add``.
All values are made up.
"""

from __future__ import annotations

import hashlib
import io
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfWriter

from src.lib.document_sources import supplement_add
from src.lib.document_sources.file_reasons import FileReason
from src.lib.document_sources.supplement_add import ChosenFile
from tests.unit.lib.document_sources.test_abc_literature_provider import _abc_show_all_main_pdf
from tests.unit.lib.document_sources.test_file_listing import _http_error, _pair
from tests.unit.lib.document_sources.test_supplement_add import (  # noqa: F401  (fixtures)
    GOOD,
    _add,
    _by_id,
    _chosen,
    _paper,
    _paper_lookup,
    _provider,
    storage,
)


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


PDF = _pdf(3)
PDF_TEXT = b"## Supplementary methods\n\nMade-up methods of the supplement.\n\n## Table S5\n\n| strain | score |\n| --- | --- |\n| s1 | 1 |\n"
ROWS = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB", content=GOOD["21"]),
        *_pair(50, "WB", extension="pdf", content=PDF_TEXT, md5=hashlib.md5(PDF).hexdigest())]
PAYLOADS = {**GOOD, "50": PDF, "51": PDF_TEXT}


def _chosen_pdf(payload: bytes = PDF) -> ChosenFile:
    return ChosenFile(provider_file_id="50", md5=hashlib.md5(payload).hexdigest(), converted_md5=hashlib.md5(PDF_TEXT).hexdigest())


@pytest.fixture
def pdf_store(monkeypatch, storage, tmp_path):  # noqa: F811
    """Where the add puts a PDF: its spool on the storage volume, then the stored path."""

    saved: dict[str, bytes] = {}
    spool = tmp_path / ".add_spool"
    spool.mkdir()
    monkeypatch.setattr(supplement_add, "_spool_directory", lambda: spool)

    def save(spooled, relative):
        spooled.seek(0)
        saved[relative] = spooled.read()

    monkeypatch.setattr(supplement_add, "_save_supplement_pdf", save)
    parser = AsyncMock(side_effect=AssertionError("a supplement is never parsed by the extraction service"))
    monkeypatch.setattr("src.lib.pipeline.pdfx_parser.PDFXParser.parse_pdf_document", parser)
    return {"saved": saved, "spool": spool, "storage": storage}


@pytest.mark.asyncio
async def test_a_converted_pdf_supplement_is_added_as_its_pdf(monkeypatch, pdf_store):
    client, provider = _provider(ROWS, PAYLOADS)
    _paper_lookup(monkeypatch)

    result = await _add(provider, [_chosen_pdf(), _chosen(20)])

    outcomes = _by_id(result)
    assert outcomes["50"].outcome == outcomes["20"].outcome == "added"
    pdf_row, text_row = sorted(pdf_store["storage"].inserted, key=lambda row: row.source_provider_source_file_id)[::-1]
    assert pdf_row.source_provider_source_file_id == "50"
    assert (pdf_row.display_mode, pdf_row.page_count, pdf_row.display_name) == ("local_pdf", 3, "Made_up_S50.pdf")
    assert pdf_row.file_path == f"owner-subject/{supplement_add_document_id()}/supplement_pdf/{pdf_row.id}.pdf"
    assert pdf_store["saved"] == {pdf_row.file_path: PDF}
    assert pdf_row.file_size == len(PDF)
    assert pdf_row.file_hash == hashlib.sha256(f"7:{hashlib.sha256(PDF).hexdigest()}".encode()).hexdigest()
    assert pdf_row.source_provider_pdf_artifact_id == "50" and pdf_row.source_provider_converted_artifact_id == "51"
    assert pdf_row.source_converted_artifact_sha256 == hashlib.sha256(PDF_TEXT).hexdigest()
    assert pdf_row.source_markdown_path == f"owner-subject/source_markdown/{pdf_row.id}.md"
    assert pdf_row.processed_json_path == f"owner-subject/processed_json/{pdf_row.id}.json"
    assert pdf_store["storage"].ready[pdf_row.id] > 0
    # Text files are unchanged.
    assert (text_row.display_mode, text_row.page_count) == ("text", None)
    # Only reads of files ABC lists: no conversion request.
    assert not [name for name, _ in client.calls if name == "request_referencefile_conversion"]
    pdf_downloads = [call for name, call in client.calls if name == "download_referencefile" and call["artifact_id"] == "50"]
    assert pdf_downloads == [{"artifact_id": "50", "max_bytes": 20_000_000}]


def supplement_add_document_id():
    from tests.unit.lib.document_sources.test_supplement_add import DOCUMENT_ID
    return DOCUMENT_ID


@pytest.mark.asyncio
async def test_its_pieces_come_from_abcs_text_without_pages(monkeypatch, pdf_store):
    from src.lib.document_sources.ingestion import provider_markdown_to_pipeline_elements

    _, provider = _provider(ROWS, PAYLOADS)
    _paper_lookup(monkeypatch)
    await _add(provider, [_chosen_pdf()])

    (row,) = pdf_store["storage"].inserted
    saved = pdf_store["storage"].saved_json[row.id]
    expected, _ = provider_markdown_to_pipeline_elements(PDF_TEXT.decode())
    assert [element["text"] for element in saved] == [element["text"] for element in expected]
    ((_, chunks),) = pdf_store["storage"].stored
    assert chunks and all(chunk.page_number is None for chunk in chunks)
    assert all(chunk.parent_section and chunk.section_title for chunk in chunks)


@pytest.mark.asyncio
async def test_a_pdf_over_the_cap_is_left_out_and_nothing_is_stored(monkeypatch, pdf_store):
    monkeypatch.setenv("DOCUMENT_FILE_MAX_PDF_BYTES", "1000")
    client, provider = _provider(ROWS, {**PAYLOADS, "50": _http_error(413)})
    _paper_lookup(monkeypatch)

    result = await _add(provider, [_chosen_pdf(), _chosen(20)])

    pdf = _by_id(result)["50"]
    assert (pdf.outcome, pdf.reason_code, pdf.limit_value) == ("left_out", FileReason.PDF_TOO_LARGE, 1000)
    assert [call for name, call in client.calls if call.get("artifact_id") == "50"] == [
        {"artifact_id": "50", "max_bytes": 1000}]
    assert [row.source_provider_source_file_id for row in pdf_store["storage"].inserted] == ["20"]
    assert pdf_store["saved"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"not a pdf at all", b"%PDF-1.4\n%made-up broken file"])
async def test_bytes_that_are_not_a_readable_pdf_are_pdf_invalid(monkeypatch, pdf_store, payload):
    rows = [_abc_show_all_main_pdf(10, "WB"),
            *_pair(50, "WB", extension="pdf", content=PDF_TEXT, md5=hashlib.md5(payload).hexdigest())]
    _, provider = _provider(rows, {**PAYLOADS, "50": payload})
    _paper_lookup(monkeypatch)

    result = await _add(provider, [_chosen_pdf(payload)])

    assert _by_id(result)["50"].reason_code is FileReason.PDF_INVALID
    assert pdf_store["storage"].inserted == []


@pytest.mark.asyncio
async def test_a_pdf_with_no_page_is_pdf_invalid(monkeypatch, pdf_store):
    empty = _pdf(0)
    rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(50, "WB", extension="pdf", content=PDF_TEXT, md5=hashlib.md5(empty).hexdigest())]
    _, provider = _provider(rows, {**PAYLOADS, "50": empty})
    _paper_lookup(monkeypatch)

    result = await _add(provider, [_chosen_pdf(empty)])

    assert _by_id(result)["50"].reason_code is FileReason.PDF_INVALID


@pytest.mark.asyncio
async def test_a_refused_pdf_leaves_its_readable_text_out_too(monkeypatch, pdf_store):
    _, provider = _provider(ROWS, {**PAYLOADS, "50": _http_error(403)})
    _paper_lookup(monkeypatch)

    result = await _add(provider, [_chosen_pdf(), _chosen(20)])

    assert _by_id(result)["50"].reason_code is FileReason.PDF_ACCESS_DENIED
    assert [row.source_provider_source_file_id for row in pdf_store["storage"].inserted] == ["20"]
    assert len(pdf_store["storage"].stored) == 1  # only the Excel file's pieces


@pytest.mark.asyncio
@pytest.mark.parametrize("status, reason", [(404, FileReason.SOURCE_FILE_MISSING), (502, FileReason.SOURCE_UNAVAILABLE)])
async def test_a_missing_or_unreachable_pdf_is_left_out(monkeypatch, pdf_store, status, reason):
    _, provider = _provider(ROWS, {**PAYLOADS, "50": _http_error(status)})
    _paper_lookup(monkeypatch)

    result = await _add(provider, [_chosen_pdf()])

    assert _by_id(result)["50"].reason_code is reason


@pytest.mark.asyncio
async def test_a_pdf_whose_bytes_differ_from_the_listing_is_changed_since_listed(monkeypatch, pdf_store):
    other = _pdf(2)
    _, provider = _provider(ROWS, {**PAYLOADS, "50": other})  # the listing's MD5 is of PDF
    _paper_lookup(monkeypatch)

    result = await _add(provider, [_chosen_pdf()])

    assert _by_id(result)["50"].reason_code is FileReason.FILE_CHANGED_SINCE_LISTED
    assert pdf_store["storage"].inserted == []


@pytest.mark.asyncio
async def test_a_pdf_left_out_uses_up_neither_the_file_nor_the_text_limit(monkeypatch, pdf_store):
    # The PDF supplement comes first in listing order (lower provider file id); the
    # paper can take one more file. Its PDF is refused, so the Excel file still fits.
    monkeypatch.setattr(supplement_add, "_paper_state", lambda *_: _paper(file_count=1))
    monkeypatch.setenv("DOCUMENT_MAX_FILES_PER_PAPER", "2")
    rows = [_abc_show_all_main_pdf(10, "WB"),
            *_pair(12, "WB", extension="pdf", content=PDF_TEXT, md5=hashlib.md5(PDF).hexdigest()), *_pair(20, "WB", content=GOOD["21"])]
    _, provider = _provider(rows, {**PAYLOADS, "12": _http_error(403), "13": PDF_TEXT})
    _paper_lookup(monkeypatch)
    chosen_pdf = ChosenFile(provider_file_id="12", md5=hashlib.md5(PDF).hexdigest(), converted_md5=hashlib.md5(PDF_TEXT).hexdigest())

    result = await _add(provider, [chosen_pdf, _chosen(20)])

    outcomes = _by_id(result)
    assert [item.provider_file_id for item in result.files] == ["12", "20"]
    assert outcomes["12"].reason_code is FileReason.PDF_ACCESS_DENIED
    assert outcomes["20"].outcome == "added"


@pytest.mark.asyncio
async def test_a_write_failure_removes_the_stored_pdf_with_the_rest(monkeypatch, pdf_store):
    pdf_store["storage"].fail_store_on = 0
    _, provider = _provider(ROWS, PAYLOADS)
    _paper_lookup(monkeypatch)

    with pytest.raises(RuntimeError, match="made-up storage failure"):
        await _add(provider, [_chosen_pdf()])

    (row,) = pdf_store["storage"].inserted
    assert row.file_path in pdf_store["storage"].removed_paths
    assert row.source_markdown_path in pdf_store["storage"].removed_paths
    assert pdf_store["storage"].deleted_rows == [row.id]


@pytest.mark.asyncio
async def test_no_pdf_bytes_stay_in_memory_and_no_spooled_file_is_left(monkeypatch, pdf_store):
    import gc

    spooled = []
    real = supplement_add._spool_pdf

    def watch(*args, **kwargs):
        result = real(*args, **kwargs)
        if not isinstance(result, FileReason):
            spooled.append(result)
        return result

    monkeypatch.setattr(supplement_add, "_spool_pdf", watch)
    _, provider = _provider(ROWS, PAYLOADS)
    _paper_lookup(monkeypatch)
    await _add(provider, [_chosen_pdf()])
    gc.collect()

    (item,) = spooled
    assert not hasattr(item, "content")
    assert item.file.closed
    # The spool file is unnamed: nothing is ever left in the spool directory.
    assert list(pdf_store["spool"].iterdir()) == []


@pytest.mark.asyncio
async def test_spooled_pdfs_are_closed_when_the_add_is_cancelled_before_writing(monkeypatch, pdf_store):
    spooled = []
    real = supplement_add._spool_pdf

    def watch(*args, **kwargs):
        result = real(*args, **kwargs)
        spooled.append(result)
        return result

    monkeypatch.setattr(supplement_add, "_spool_pdf", watch)

    async def checkpoint(stage):
        if stage == "saving_files":
            raise RuntimeError("made-up cancel")

    _, provider = _provider(ROWS, PAYLOADS)
    _paper_lookup(monkeypatch)
    with pytest.raises(RuntimeError, match="made-up cancel"):
        await _add(provider, [_chosen_pdf()], checkpoint=checkpoint)

    assert [item.file.closed for item in spooled] == [True]
