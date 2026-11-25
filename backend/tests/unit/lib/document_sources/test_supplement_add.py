"""Adding converted text supplements to an existing paper (P7c1), with fakes for storage.

Listing rows are ABC ``show_all`` shapes through the real Alliance adapter (as in
``test_file_listing``); converted texts are in the shape ABC's Office converter
writes (one top-level title, one second-level heading per sheet). The database,
the search index and the disk are fakes recording what the add asks of them; the
persistence tests cover the real rows. All values are made up.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

from src.lib.document_sources import file_listing_service, supplement_add
from src.lib.document_sources.access import DocumentSourceRequestContext
from src.lib.document_sources.file_reasons import FileReason
from src.lib.document_sources.models import ProviderBearerKind
from src.lib.document_sources.supplement_add import AddResult, ChosenFile, SupplementAddError
from src.lib.exceptions import PDFCancellationError
from src.lib.pipeline.store import store_added_file_pieces
from tests.unit.lib.document_sources.test_abc_literature_provider import (
    _abc_reference_record,
    _abc_show_all_main_pdf,
    _abc_show_all_row,
    provider_from_fake,
)
from tests.unit.lib.document_sources.test_file_listing import _Client, _limits, _listing, _pair, _Store

_REAL_DELETE_PIECES = supplement_add._delete_pieces
_REAL_DELETE_PIECES_OF_DELETED_PAPER = supplement_add._delete_pieces_of_deleted_paper

DOCUMENT_ID = uuid4()
JOB_ID = uuid4()
CONTEXT = DocumentSourceRequestContext(
    provider_groups=("WB",), authorized_group_ids=("WB",), bearer_kind=ProviderBearerKind.CURATOR,
    curator_token="curator-token",
)


def _workbook(title: str, *sheets: str) -> bytes:
    body = "".join(
        f"## {sheet}\n\nMade-up rows of {sheet}.\n\n| strain | score |\n| --- | --- |\n| s1 | 1 |\n\n" for sheet in sheets
    )
    return f"# {title}\n\n{body}".encode()


@dataclass
class _Storage:
    """What the add asked the database, the search index and the disk to do."""

    inserted: list[Any] = field(default_factory=list)
    stored: list[tuple[UUID, list[Any]]] = field(default_factory=list)
    ready: dict[UUID, int] = field(default_factory=dict)
    deleted_rows: list[UUID] = field(default_factory=list)
    deleted_ranges: list[tuple[int, int]] = field(default_factory=list)
    saved_json: dict[UUID, list[dict]] = field(default_factory=dict)
    removed_paths: list[str] = field(default_factory=list)
    after_add: int = 0
    next_index: int = 30
    fail_store_on: int | None = None
    paper_exists: bool = True
    deleted_whole_paper: int = 0
    after_add_fails: bool = False


def _paper(**overrides) -> supplement_add._PaperState:
    values = dict(
        owner_subject="owner-subject", status="completed", file_count=1, ready_text_bytes=1_000,
        next_tab_order=1, leftovers=(), source_provider="abc_literature",
        source_provider_reference_id="101", source_provider_reference_curie="AGRKB:101",
    )
    values.update(overrides)
    return supplement_add._PaperState(**values)


@pytest.fixture
def storage(monkeypatch):
    record = _Storage()
    store = _Store()
    monkeypatch.setattr(file_listing_service, "_stores", lambda _provider: (store.load, store.save))
    monkeypatch.setattr(file_listing_service, "current_file_check_limits", _limits)
    monkeypatch.setattr(supplement_add, "current_file_check_limits", _limits)
    monkeypatch.setattr(supplement_add, "_paper_state", lambda *_: _paper())

    async def save_markdown(*, markdown, file_id, user_id):
        return f"{user_id}/source_markdown/{file_id}.md"

    async def save_json(*, elements, file_id, user_id):
        record.saved_json[file_id] = elements
        return f"{user_id}/processed_json/{file_id}.json"

    from src.lib.document_sources import ingestion

    monkeypatch.setattr(ingestion, "_save_source_markdown", save_markdown)
    monkeypatch.setattr(ingestion, "_save_processed_json", save_json)
    def _fake_insert(_doc, rows, _copy_ids=None, _text_bytes=None):
        record.inserted.extend(rows)
        return {}

    monkeypatch.setattr(supplement_add, "_insert_rows", _fake_insert)

    def reserve(_document_id, file_id, count):
        start = record.next_index
        record.next_index += count
        row = next(row for row in record.inserted if row.id == file_id)
        row.chunk_index_start, row.chunk_index_end = start, start + count - 1
        return start

    async def store_to_weaviate(chunks, document_id, client, user_id, *, chunk_range):
        if record.fail_store_on is not None and len(record.stored) == record.fail_store_on:
            raise RuntimeError("made-up storage failure")
        record.stored.append((chunk_range, chunks))

    monkeypatch.setattr("src.lib.document_files.service.reserve_file_chunk_range", reserve)
    monkeypatch.setattr("src.lib.pipeline.store.store_added_file_pieces", store_to_weaviate)
    monkeypatch.setattr(
        "src.lib.document_files.service.mark_files_ready", lambda items: record.ready.update(items),
    )

    def rollback_files(_doc, ids):
        if not record.paper_exists:
            return None
        return [supplement_add._Leftover.from_row(row) for row in record.inserted if row.id in ids]

    monkeypatch.setattr(supplement_add, "_rollback_files", rollback_files)

    async def delete_pieces(_doc, _subject, found, *, caller_reports_failure=False):
        record.deleted_ranges.extend(found)

    monkeypatch.setattr(supplement_add, "_delete_pieces", delete_pieces)

    async def delete_all(_doc, _subject, *, caller_reports_failure=False):
        record.deleted_whole_paper += 1

    monkeypatch.setattr(supplement_add, "_delete_pieces_of_deleted_paper", delete_all)
    monkeypatch.setattr(supplement_add, "_delete_rows", lambda _doc, ids: record.deleted_rows.extend(ids))
    monkeypatch.setattr(supplement_add, "_remove_stored_files", lambda paths, subject: record.removed_paths.extend(paths))

    async def after_add(*_):
        if record.after_add_fails:
            raise RuntimeError("made-up failure updating the paper's index object")
        record.after_add += 1

    monkeypatch.setattr(supplement_add, "_after_add", after_add)
    hierarchy = AsyncMock(side_effect=AssertionError("the hierarchy model is never called for a supplement"))
    figures = AsyncMock(side_effect=AssertionError("no supplement piece reaches the figure locators"))
    monkeypatch.setattr("src.lib.pipeline.hierarchy_resolution.resolve_document_hierarchy", hierarchy)
    monkeypatch.setattr("src.lib.pipeline.figure_locator_resolution.resolve_figure_locators", figures)
    return record


def _provider(rows, payloads):
    client = _Client(payloads)
    client.show_reference_payload = _abc_reference_record()
    client.show_referencefiles_payload = rows  # type: ignore[assignment]
    return client, provider_from_fake(client)


def _paper_lookup(monkeypatch, file_ids=("10",)):
    monkeypatch.setattr(
        file_listing_service, "_paper_lookup",
        lambda *_: file_listing_service._PaperSource(
            provider="abc_literature", reference_id="101", reference_curie="AGRKB:101", file_ids=file_ids,
            provider_observations={},
        ),
    )


def _chosen(original_id: int, *, content: bytes | None = None, md5_suffix="") -> ChosenFile:
    if content is None:
        content = GOOD[str(original_id + 1)]
    return ChosenFile(
        provider_file_id=str(original_id), md5=f"md5-{original_id}{md5_suffix}",
        converted_md5=hashlib.md5(content).hexdigest(),
    )


async def _add(provider, files, checkpoint=None) -> AddResult:
    return await supplement_add.add_files_to_paper(
        DOCUMENT_ID, 7, CONTEXT, files, job_id=JOB_ID, provider=provider, weaviate_client=object(),
        stage_checkpoint=checkpoint or AsyncMock(),
    )


def _by_id(result: AddResult):
    return {item.provider_file_id: item for item in result.files}


GOOD = {
    "21": _workbook("Made_up_S20", "Sheet1"),
    "31": b"# One\n\n# Two\n",  # an Office file must have exactly one top-level title (R7)
    "41": _workbook("Made_up_S40", "Sheet1", "Sheet2"),
}


def _rows(payloads):
    rows = [_abc_show_all_main_pdf(10, "WB")]
    for converted_id, content in payloads.items():
        rows.extend(_pair(int(converted_id) - 1, "WB", content=content))
    return rows


ROWS = _rows(GOOD)


@pytest.mark.asyncio
@pytest.mark.parametrize("cached_pass", [False, True])
async def test_changed_download_is_left_out_without_writes(monkeypatch, storage, cached_pass):
    store = _Store()
    rows = _pair(20, "WB", content=GOOD["21"])
    if cached_pass:
        await _listing(rows, payloads=GOOD, store=store)
        assert len(store.saved) == 1
        assert not next(iter(store.saved.values())).refused

    def unexpected_save(*_args):
        pytest.fail("changed bytes must not create or replace an assessment")

    monkeypatch.setattr(supplement_add, "assess_converted_file", lambda *_: pytest.fail("assessed changed bytes"))
    monkeypatch.setattr(file_listing_service, "_stores", lambda _provider: (store.load, unexpected_save))
    _, provider = _provider(rows, {"21": GOOD["21"] + b"changed"})
    _paper_lookup(monkeypatch)
    result = await _add(provider, [_chosen(20)])
    assert _by_id(result)["20"].reason_code is FileReason.FILE_CHANGED_SINCE_LISTED
    assert storage.inserted == [] and storage.stored == [] and storage.saved_json == {}


@pytest.mark.asyncio
async def test_uppercase_md5_passes_add_and_reuses_cached_assessment(monkeypatch, storage):
    store = _Store()
    lower_md5 = hashlib.md5(GOOD["21"]).hexdigest()
    lower_rows = _pair(20, "WB", content=GOOD["21"])
    await _listing(lower_rows, payloads=GOOD, store=store)
    assert [key[1] for key in store.saved] == [lower_md5]

    upper_rows = [_abc_show_all_main_pdf(10, "WB"), *_pair(20, "WB", md5="  MD5-20  ", content=GOOD["21"])]
    upper_rows[2]["md5sum"] = f" {lower_md5.upper()} "

    monkeypatch.setattr(file_listing_service, "_stores", lambda _provider: (store.load, store.save))
    monkeypatch.setattr(supplement_add, "assess_converted_file", lambda *_: pytest.fail("cached check should be reused"))
    _, provider = _provider(upper_rows, {"21": GOOD["21"]})
    _paper_lookup(monkeypatch)
    chosen = ChosenFile(provider_file_id="20", md5="MD5-20", converted_md5=lower_md5.upper())

    result = await _add(provider, [chosen])
    assert _by_id(result)["20"].outcome == "added"
    assert len(storage.inserted) == 1
    assert storage.inserted[0].source_md5 == "md5-20"
    assert [key[1] for key in store.saved] == [lower_md5]


@pytest.mark.asyncio
async def test_a_refused_second_file_of_three_is_left_out_and_the_other_two_are_added(monkeypatch, storage):
    client, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    monkeypatch.setattr(supplement_add, "assess_converted_file", lambda *_: pytest.fail("listing already checked"))
    result = await _add(provider, [_chosen(40), _chosen(30), _chosen(20)])

    outcomes = _by_id(result)
    assert [item.provider_file_id for item in result.files] == ["20", "30", "40"]  # listing order
    assert outcomes["30"].outcome == "left_out" and outcomes["30"].reason_code is FileReason.TITLE_COUNT
    assert outcomes["20"].outcome == outcomes["40"].outcome == "added"
    assert [row.tab_order for row in storage.inserted] == [1, 2]
    assert [row.display_name for row in storage.inserted] == ["Made_up_S20.xlsx", "Made_up_S40.xlsx"]
    assert set(storage.ready) == {outcomes["20"].file_id, outcomes["40"].file_id}
    assert storage.after_add == 1 and storage.deleted_rows == []
    # An add sends no conversion request.
    assert not [name for name, _ in client.calls if name == "request_referencefile_conversion"]


@pytest.mark.asyncio
async def test_unnamed_supplement_keeps_public_display_name_null_on_add_and_refusal(monkeypatch, storage):
    """Unnamed supplements keep required SQL storage names internal and emit display_name=None publicly."""
    pair_20 = _pair(20, "WB", content=GOOD["21"])
    pair_20[0]["display_name"] = ""
    pair_30 = _pair(30, "WB", content=GOOD["31"])
    pair_30[0]["display_name"] = "   "
    rows = [_abc_show_all_main_pdf(10, "WB"), *pair_20, *pair_30]
    _, provider = _provider(rows, GOOD)
    _paper_lookup(monkeypatch)

    result = await _add(provider, [_chosen(20), _chosen(30)])
    outcomes = _by_id(result)

    # Added unnamed file: SQL row uses neutral label (never raw provider_file_id),
    # viewer file list never leaks "20" / "20.xlsx", and public FileOutcome has display_name=None.
    from types import SimpleNamespace
    from src.lib.pdf_viewer.files import viewer_file_refs

    assert outcomes["20"].outcome == "added"
    assert outcomes["20"].display_name is None
    assert [row.display_name for row in storage.inserted] == ["Supplement file"]
    viewer_refs = viewer_file_refs(SimpleNamespace(id=DOCUMENT_ID, files=storage.inserted))
    assert [ref.display_name for ref in viewer_refs] == ["Supplement file"]
    assert all("20" not in ref.display_name for ref in viewer_refs)

    # Refused unnamed file: public FileOutcome has display_name=None (never "30" or "30.xlsx").
    assert outcomes["30"].outcome == "left_out"
    assert outcomes["30"].reason_code is FileReason.TITLE_COUNT
    assert outcomes["30"].display_name is None

    # Serialized job metadata entries never substitute raw provider_file_id.
    entries = supplement_add._supplement_entries(result)
    assert [(e["provider_file_id"], e["display_name"], e["state"]) for e in entries] == [
        ("20", None, "added"),
        ("30", None, "left_out"),
    ]


@pytest.mark.asyncio
async def test_rows_have_the_full_supplement_shape(monkeypatch, storage):
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    await _add(provider, [_chosen(20)])

    row = storage.inserted[0]
    assert (row.role, row.status, row.display_mode, row.page_count) == ("supplement", "pending", "text", None)
    assert row.source_provider_source_file_id == "20" and row.source_provider_converted_artifact_id == "21"
    assert row.file_path == row.source_markdown_path == f"owner-subject/source_markdown/{row.id}.md"
    assert row.processed_json_path == f"owner-subject/processed_json/{row.id}.json"
    assert row.file_size == len(GOOD["21"]) and len(row.file_hash) == 64
    assert row.source_md5 == "md5-20" and len(row.source_converted_artifact_sha256) == 64
    assert row.source_access_group_ids == ["WB"] and row.source_import_status == "processing"


@pytest.mark.asyncio
async def test_two_sheets_give_two_sections_and_the_saved_json_matches_the_stored_pieces(monkeypatch, storage):
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    result = await _add(provider, [_chosen(40)])

    file_id = _by_id(result)["40"].file_id
    saved_sections = {element["parent_section"] for element in storage.saved_json[file_id]}
    # The file's own title heading is a section named after it; each sheet is its own section.
    assert saved_sections == {"Made_up_S40", "Sheet1", "Sheet2"}
    (_, chunks), = storage.stored
    stored_sections = {chunk.parent_section for chunk in chunks}
    assert stored_sections <= saved_sections and {"Sheet1", "Sheet2"} <= stored_sections


@pytest.mark.asyncio
async def test_an_abstract_heading_in_a_supplement_is_only_a_section_of_that_file(monkeypatch, storage):
    payload = b"# Made_up_S20\n\n## Abstract\n\nMade-up text under a heading called Abstract.\n"
    _, provider = _provider(_rows({"21": payload}), {"21": payload})
    _paper_lookup(monkeypatch)
    result = await _add(provider, [_chosen(20, content=payload)])

    file_id = _by_id(result)["20"].file_id
    assert {element["parent_section"] for element in storage.saved_json[file_id]} == {"Made_up_S20", "Abstract"}
    assert storage.after_add == 1  # the paper's piece count; nothing else of the paper changes


@pytest.mark.asyncio
async def test_unconverted_changed_unlisted_and_already_added_files_are_left_out(monkeypatch, storage):
    rows = [
        *ROWS,
        _abc_show_all_row(50, "WB", file_class="supplement", file_extension="xlsx", display_name="Made_up_S50"),
    ]
    _, provider = _provider(rows, GOOD)
    _paper_lookup(monkeypatch, file_ids=("10", "40"))
    result = await _add(provider, [
        _chosen(50, content=b""), _chosen(20, md5_suffix="-changed"), _chosen(99, content=b""),
        _chosen(40), _chosen(10, content=b""),
    ])

    reasons = {item.provider_file_id: item.reason_code for item in result.files}
    assert reasons == {
        "50": FileReason.FILE_NOT_TICKABLE,
        "20": FileReason.FILE_CHANGED_SINCE_LISTED,
        "99": FileReason.FILE_NOT_IN_REFERENCE,
        "40": FileReason.ALREADY_IN_PAPER,
        "10": FileReason.ALREADY_IN_PAPER,
    }
    assert storage.inserted == []


@pytest.mark.asyncio
async def test_the_paper_total_counts_the_main_file_and_later_smaller_files_can_still_fit(monkeypatch, storage):
    monkeypatch.setenv("DOCUMENT_PAPER_MAX_TEXT_BYTES", "1100")
    big = _workbook("Made_up_S20", *[f"Sheet{index}" for index in range(8)])
    small = _workbook("Made_up_S40", "Sheet1")
    payloads = {"21": big, "41": small}
    _, provider = _provider(_rows(payloads), payloads)
    _paper_lookup(monkeypatch)
    result = await _add(provider, [_chosen(20, content=big), _chosen(40, content=small)])

    outcomes = _by_id(result)
    assert outcomes["20"].reason_code is FileReason.PAPER_TEXT_LIMIT
    assert outcomes["20"].limit_value == 1100 and outcomes["20"].measured_value > 1100
    assert outcomes["40"].outcome == "added"


@pytest.mark.asyncio
async def test_the_file_count_includes_files_already_in_the_paper(monkeypatch, storage):
    monkeypatch.setenv("DOCUMENT_MAX_FILES_PER_PAPER", "2")
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    result = await _add(provider, [_chosen(20), _chosen(40)])

    outcomes = _by_id(result)
    assert outcomes["20"].outcome == "added"
    assert (outcomes["40"].reason_code, outcomes["40"].measured_value, outcomes["40"].limit_value) == (
        FileReason.PAPER_FILE_LIMIT, 3, 2,
    )


@pytest.mark.asyncio
async def test_a_download_over_the_cap_is_r1s_too_large(monkeypatch, storage):
    from tests.unit.lib.document_sources.test_file_listing import _http_error

    _, provider = _provider(ROWS, {**GOOD, "21": _http_error(413)})
    _paper_lookup(monkeypatch)
    result = await _add(provider, [_chosen(20)])
    assert _by_id(result)["20"].reason_code is FileReason.TOO_LARGE


@pytest.mark.asyncio
async def test_a_write_failure_removes_this_adds_pieces_files_and_rows_only(monkeypatch, storage):
    storage.fail_store_on = 1
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    with pytest.raises(RuntimeError, match="made-up storage failure"):
        await _add(provider, [_chosen(20), _chosen(40)])

    inserted_ids = [row.id for row in storage.inserted]
    assert storage.deleted_rows == inserted_ids
    # Only the first file's range was stored; both reserved ranges are deleted, nothing below 30.
    assert storage.deleted_ranges and min(start for start, _ in storage.deleted_ranges) == 30
    assert len(storage.removed_paths) == 4
    assert storage.ready == {} and storage.after_add == 0


@pytest.mark.asyncio
async def test_a_cancel_while_writing_removes_this_adds_rows(monkeypatch, storage):
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)

    async def checkpoint(stage):
        if stage == "finishing":
            raise RuntimeError("made-up cancel")

    with pytest.raises(RuntimeError, match="made-up cancel"):
        await _add(provider, [_chosen(20)], checkpoint=checkpoint)
    assert storage.deleted_rows == [row.id for row in storage.inserted] and storage.ready == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails, second_cancel", [(True, False), (False, True), (True, True)])
async def test_cancelled_add_drains_rollback_and_preserves_deadline(monkeypatch, storage, cleanup_fails, second_cancel):
    finishing = asyncio.Event()
    entered = asyncio.Event()
    release = asyncio.Event()
    report = Mock()
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)

    async def checkpoint(stage):
        if stage == "finishing":
            finishing.set()
            await asyncio.Future()

    undo = supplement_add._undo

    async def paused_undo(*args, **kwargs):
        entered.set()
        await release.wait()
        await undo(*args, **kwargs)

    monkeypatch.setattr(supplement_add, "_undo", paused_undo)
    if cleanup_fails:
        monkeypatch.setattr(supplement_add, "_remove_stored_files", Mock(side_effect=OSError("private-filename")))
    add = asyncio.create_task(_add(provider, [_chosen(20)], checkpoint))
    try:
        await asyncio.wait_for(finishing.wait(), timeout=10)
        add.cancel("deadline")
        await asyncio.wait_for(entered.wait(), timeout=10)
        if second_cancel:
            add.cancel("second cancellation")
            await asyncio.sleep(0)
            assert not add.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError, match="deadline"):
        await add
    assert storage.deleted_ranges
    if cleanup_fails:
        report.assert_called_once()
        error, = report.call_args.args
        assert error.__context__ is error.__cause__ is None
        assert "private-filename" not in str(report.call_args)
        assert report.call_args.kwargs["operation"] == "supplement_add_cancelled_rollback_failed"
        assert report.call_args.kwargs["context"] == {"exception_type": "OSError"}
        assert report.call_args.kwargs["include_request"] is False
    else:
        report.assert_not_called()
        assert storage.deleted_rows == [row.id for row in storage.inserted]


@pytest.mark.asyncio
async def test_cancelled_chunk_failure_is_reported_once_by_pipeline(monkeypatch, storage, caplog):
    from src.lib.pipeline import store

    entered = asyncio.Event()
    release = asyncio.Event()
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)

    async def fail_chunk_write(*args, **kwargs):
        entered.set()
        await release.wait()
        raise RuntimeError("private-chunk-content")

    report = Mock()
    monkeypatch.setattr(store, "report_runtime_exception", report)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)
    monkeypatch.setattr(store, "store_added_file_pieces", store_added_file_pieces)
    monkeypatch.setattr(store, "store_chunks_to_weaviate", fail_chunk_write)
    add = asyncio.create_task(_add(provider, [_chosen(20)]))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        add.cancel("deadline")
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError, match="deadline"):
        await add
    report.assert_called_once()
    assert report.call_args.kwargs["operation"] == "pipeline_cancelled_storage_write_failed"
    assert "private-chunk-content" not in str(report.call_args)
    for record in caplog.records:
        if record.name == store.__name__ and record.levelno >= logging.WARNING:
            assert getattr(record, "sentry_skip_event", False)
    assert storage.deleted_rows == [row.id for row in storage.inserted]


@pytest.mark.asyncio
async def test_failed_ready_commit_during_cancellation_reports_and_rolls_back(monkeypatch, storage):
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)

    def fail_commit(_sizes):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(10)
        raise RuntimeError("private-document-content")

    report = Mock()
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)
    monkeypatch.setattr("src.lib.document_files.service.mark_files_ready", fail_commit)
    add = asyncio.create_task(_add(provider, [_chosen(20)]))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        add.cancel("deadline")
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError, match="deadline"):
        await add
    assert storage.deleted_rows == [row.id for row in storage.inserted]
    assert storage.deleted_ranges and storage.removed_paths
    report.assert_called_once()
    error, = report.call_args.args
    assert error.__context__ is error.__cause__ is None
    assert "private-document-content" not in str(report.call_args)
    assert report.call_args.kwargs["operation"] == "supplement_add_cancelled_mutation_failed"
    assert report.call_args.kwargs["context"] == {"exception_type": "RuntimeError"}
    assert report.call_args.kwargs["include_request"] is False


@pytest.mark.asyncio
async def test_pdf_cancellation_drains_rollback_and_preserves_cancellation(monkeypatch, storage):
    report = Mock()
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)
    monkeypatch.setattr(supplement_add, "_remove_stored_files", Mock(side_effect=OSError("private-curator-filename")))
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)

    async def checkpoint(stage):
        if stage == "finishing":
            raise PDFCancellationError("Processing cancelled by user request")

    with pytest.raises(PDFCancellationError, match="cancelled by user request"):
        await _add(provider, [_chosen(20)], checkpoint=checkpoint)
    assert storage.deleted_ranges
    report.assert_called_once()
    error, = report.call_args.args
    assert error.__context__ is error.__cause__ is None
    assert "private-curator-filename" not in str(report.call_args)
    assert report.call_args.kwargs["component"] == "document_files"
    assert report.call_args.kwargs["operation"] == "supplement_add_cancelled_rollback_failed"
    assert report.call_args.kwargs["fingerprint"] == ["supplement_add_cancelled_rollback_failed"]
    assert report.call_args.kwargs["context"] == {"exception_type": "OSError"}
    assert report.call_args.kwargs["include_request"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback_fails", [False, True])
async def test_deadline_cancelling_failure_rollback_preserves_deadline_and_reports_displaced_failure(
    monkeypatch, storage, caplog, rollback_fails,
):
    from src.lib.pipeline import store

    entered = asyncio.Event()
    release = asyncio.Event()
    report = Mock()
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)
    monkeypatch.setattr(store, "store_added_file_pieces", store_added_file_pieces)
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)

    async def fail_chunk_write(*_args, **_kwargs):
        raise OSError("private-write-secret")

    monkeypatch.setattr(store, "store_chunks_to_weaviate", fail_chunk_write)
    undo = supplement_add._undo

    async def paused_undo(*args, **kwargs):
        entered.set()
        await release.wait()
        await undo(*args, **kwargs)

    monkeypatch.setattr(supplement_add, "_undo", paused_undo)
    if rollback_fails:
        monkeypatch.setattr(
            supplement_add, "_remove_stored_files", Mock(side_effect=OSError("private-rollback-secret")),
        )

    with caplog.at_level(logging.ERROR):
        add = asyncio.create_task(_add(provider, [_chosen(20)]))
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            add.cancel("deadline")
            await asyncio.sleep(0)
            assert not add.done()
        finally:
            release.set()

        with pytest.raises(asyncio.CancelledError, match="deadline"):
            await add

    promotable_errors = [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and not getattr(record, "sentry_skip_event", False)
    ]
    assert promotable_errors == []
    assert storage.deleted_ranges
    if not rollback_fails:
        assert storage.deleted_rows == [row.id for row in storage.inserted]
        report.assert_called_once()
        error, = report.call_args.args
        assert error.__context__ is error.__cause__ is None
        assert "private-write-secret" not in str(report.call_args)
        assert report.call_args.kwargs["component"] == "document_files"
        assert report.call_args.kwargs["operation"] == "supplement_add_displaced_write_failed"
        assert report.call_args.kwargs["fingerprint"] == ["supplement_add_displaced_write_failed"]
        assert report.call_args.kwargs["context"] == {"exception_type": "StorageError"}
        assert report.call_args.kwargs["include_request"] is False
    else:
        assert report.call_count == 2
        rollback_call, displaced_call = report.call_args_list
        for call in (rollback_call, displaced_call):
            err, = call.args
            assert err.__context__ is err.__cause__ is None
            assert call.kwargs["component"] == "document_files"
            assert call.kwargs["include_request"] is False
            assert "private-write-secret" not in str(call)
            assert "private-rollback-secret" not in str(call)
        assert rollback_call.kwargs["operation"] == "supplement_add_cancelled_rollback_failed"
        assert rollback_call.kwargs["fingerprint"] == ["supplement_add_cancelled_rollback_failed"]
        assert rollback_call.kwargs["context"] == {"exception_type": "OSError"}
        assert displaced_call.kwargs["operation"] == "supplement_add_displaced_write_failed"
        assert displaced_call.kwargs["fingerprint"] == ["supplement_add_displaced_write_failed"]
        assert displaced_call.kwargs["context"] == {"exception_type": "StorageError"}


@pytest.mark.asyncio
@pytest.mark.parametrize("paper_exists", [True, False])
async def test_cancelled_rollback_weaviate_reconnect_failure_reports_once_without_promotable_error_log(
    monkeypatch, storage, caplog, paper_exists,
):
    from src.lib.weaviate_client import chunks as weaviate_chunks
    from src.lib.weaviate_client import connection as wc

    finishing = asyncio.Event()
    report = Mock()
    storage.paper_exists = paper_exists
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)
    monkeypatch.setattr(supplement_add, "_delete_pieces", _REAL_DELETE_PIECES)
    monkeypatch.setattr(supplement_add, "_delete_pieces_of_deleted_paper", _REAL_DELETE_PIECES_OF_DELETED_PAPER)
    monkeypatch.setattr(wc.WeaviateConnection, "_instance", None)
    monkeypatch.setattr(wc, "_connection", None)
    conn = wc.WeaviateConnection(url="http://localhost:8080")
    monkeypatch.setattr(
        wc.weaviate,
        "connect_to_local",
        Mock(side_effect=ConnectionError("private-weaviate-token")),
    )
    monkeypatch.setattr(weaviate_chunks, "get_connection", lambda: conn)
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)

    async def checkpoint(stage):
        if stage == "finishing":
            finishing.set()
            await asyncio.Future()

    with caplog.at_level(logging.WARNING):
        add = asyncio.create_task(_add(provider, [_chosen(20)], checkpoint))
        await asyncio.wait_for(finishing.wait(), timeout=10)
        add.cancel("deadline")
        with pytest.raises(asyncio.CancelledError, match="deadline"):
            await add

    report.assert_called_once()
    error, = report.call_args.args
    assert error.__context__ is error.__cause__ is None
    assert "private-weaviate-token" not in str(report.call_args)
    assert "private-weaviate-token" not in caplog.text
    assert report.call_args.kwargs["component"] == "document_files"
    assert report.call_args.kwargs["operation"] == "supplement_add_cancelled_rollback_failed"
    assert report.call_args.kwargs["fingerprint"] == ["supplement_add_cancelled_rollback_failed"]
    assert report.call_args.kwargs["context"] == {"exception_type": "ConnectionError"}
    assert report.call_args.kwargs["include_request"] is False
    promotable_errors = [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and not getattr(record, "sentry_skip_event", False)
    ]
    assert promotable_errors == []


@pytest.mark.asyncio
async def test_rows_left_failed_by_a_dead_add_are_removed_first(monkeypatch, storage):
    leftover = supplement_add._Leftover(
        file_id=uuid4(), chunk_index_start=12, chunk_index_end=15, paths=("owner-subject/source_markdown/x.md",),
    )
    monkeypatch.setattr(supplement_add, "_paper_state", lambda *_: _paper(leftovers=(leftover,)))
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    await _add(provider, [_chosen(20)])

    assert storage.deleted_ranges == [(12, 15)]
    assert storage.deleted_rows == [leftover.file_id]
    assert storage.removed_paths == ["owner-subject/source_markdown/x.md"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["processing", "failed", "pending"])
async def test_files_are_added_only_to_a_finished_paper(monkeypatch, storage, status):
    monkeypatch.setattr(supplement_add, "_paper_state", lambda *_: _paper(status=status))
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    with pytest.raises(SupplementAddError) as raised:
        await _add(provider, [_chosen(20)])
    assert raised.value.code == "paper_not_completed"


@pytest.mark.asyncio
async def test_a_merged_reference_stops_the_add_before_any_download(monkeypatch, storage):
    client = _Client(GOOD)
    client.show_reference_payload = dict(_abc_reference_record(), reference_id=999)
    client.show_referencefiles_payload = ROWS  # type: ignore[assignment]
    _paper_lookup(monkeypatch)
    with pytest.raises(file_listing_service.FileListingUnavailable) as raised:
        await _add(provider_from_fake(client), [_chosen(20)])
    assert raised.value.code == "paper_reference_mismatch"
    assert not [name for name, _ in client.calls if name == "download_referencefile"]


@pytest.mark.asyncio
async def test_when_every_file_is_left_out_the_job_fails_and_one_warning_names_no_file(monkeypatch):
    left_out = AddResult(files=(
        supplement_add.FileOutcome("20", "left_out", FileReason.TOO_LARGE, limit_value=40, display_name="Secret_Name.xlsx"),
        supplement_add.FileOutcome("30", "left_out", FileReason.TITLE_COUNT, measured_value=2, limit_value=1),
    ))
    monkeypatch.setattr(supplement_add, "add_files_to_paper", AsyncMock(return_value=left_out))
    provider = AsyncMock()
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda: object())
    reports = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda exc, **kwargs: reports.append((exc, kwargs)),
    )
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))

    await supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
        job_id=JOB_ID, document_id=DOCUMENT_ID, user_id="owner-subject", owner_user_id=7, context=CONTEXT,
        files=(_chosen(20), _chosen(30)),
    ))

    (exc, kwargs), = reports
    assert kwargs["level"] == "warning" and kwargs["operation"] == "supplement_add_nothing_added"
    assert [item["reason_code"] for item in kwargs["context"]["refusals"]] == ["too_large", "title_count"]
    assert "Secret_Name" not in repr(kwargs) and "Secret_Name" not in str(exc)
    assert "20" not in [item["artifact_id_hash"] for item in kwargs["context"]["refusals"]]
    (job,), = [failed]
    assert job["metadata"]["supplements"][0]["reason_code"] == "too_large"
    provider.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_completed_add_records_each_files_outcome_on_its_job(monkeypatch):
    added_id = uuid4()
    result = AddResult(files=(
        supplement_add.FileOutcome("20", "added", file_id=added_id, display_name="Made_up_S20.xlsx"),
        supplement_add.FileOutcome("30", "left_out", FileReason.TITLE_COUNT, measured_value=2, limit_value=1),
    ))
    monkeypatch.setattr(supplement_add, "add_files_to_paper", AsyncMock(return_value=result))
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: AsyncMock())
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda: object())
    completed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_completed", lambda **kwargs: completed.append(kwargs))

    await supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
        job_id=JOB_ID, document_id=DOCUMENT_ID, user_id="owner-subject", owner_user_id=7, context=CONTEXT,
        files=(_chosen(20), _chosen(30)),
    ))

    (job,) = completed
    entries = job["metadata"]["supplements"]
    assert entries[0] == {
        "provider_file_id": "20", "display_name": "Made_up_S20.xlsx", "state": "added", "reason_code": None,
        "observed_at": None, "file_id": str(added_id), "measured_value": None, "limit_value": None,
    }
    assert entries[1]["state"] == "left_out" and entries[1]["reason_code"] == "title_count"
    # The paper's later listing reads these: an add's refusal is never a provider observation.
    assert file_listing_service.provider_observations_from_jobs([job["metadata"]]) == {}


@pytest.mark.asyncio
async def test_a_paper_deleted_while_its_add_writes_loses_every_piece_the_add_stored(monkeypatch, storage):
    """The paper's rows went with it, so the add deletes every piece still filed under its id."""

    storage.fail_store_on = 1
    storage.paper_exists = False
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    with pytest.raises(RuntimeError, match="made-up storage failure"):
        await _add(provider, [_chosen(20), _chosen(40)])
    assert storage.deleted_whole_paper == 1 and storage.deleted_ranges == []


# Every element of a supplement has a non-empty section, and its stored piece carries it
# (the guarantee evidence relies on; settled with b-p8b, option (a)).

SECTION_CASES = {
    "heading-less Word": b"# Made_up_S20\n\nMade-up legend text with no headings.\n\nA second made-up paragraph.\n",
    "single-sheet Excel": b"# Made_up_S20\n\n## Sheet1\n\n| strain | score |\n| --- | --- |\n| s1 | 1 |\n",
    "multi-sheet Excel": (
        b"# Made_up_S20\n\n## Sheet1\n\n| strain | score |\n| --- | --- |\n| s1 | 1 |\n\n"
        b"## Sheet2\n\n| gene | count |\n| --- | --- |\n| g1 | 3 |\n"
    ),
    "an empty sheet": (
        b"# Made_up_S20\n\n## Sheet1\n\n| strain | score |\n| --- | --- |\n| s1 | 1 |\n\n## Empty\n"
    ),
    "a title-only file": b"# Made_up_S20\n",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", list(SECTION_CASES))
async def test_every_element_and_every_stored_piece_has_a_section(monkeypatch, storage, case):
    content = SECTION_CASES[case]
    _, provider = _provider(_rows({"21": content}), {"21": content})
    _paper_lookup(monkeypatch)
    result = await _add(provider, [_chosen(20, content=content)])

    added = _by_id(result)["20"]
    assert added.outcome == "added", added
    elements = storage.saved_json[added.file_id]
    assert elements
    for element in elements:
        assert element["parent_section"] and element["metadata"]["parent_section"], element
        assert element["section_title"] and element["metadata"]["section_title"], element
    ((_, chunks),) = storage.stored
    assert chunks
    for chunk in chunks:
        assert chunk.parent_section and chunk.section_title, chunk


def test_an_empty_display_name_for_text_before_any_heading_fails_loudly():
    from src.lib.pipeline.pdfx_parser import markdown_to_pipeline_elements
    from src.lib.pipeline.structural_sections import apply_structural_sections

    with pytest.raises(ValueError, match="file's name"):
        apply_structural_sections(
            markdown_to_pipeline_elements("Made-up text.\n"), file_title_is_first_heading=True, fallback_section="",
        )


@pytest.mark.asyncio
async def test_a_failure_after_the_files_are_ready_still_counts_the_add_as_done(monkeypatch, storage):
    """The files are added and readable; only the paper's index count or cache may be stale (reported)."""

    storage.after_add_fails = True
    reports = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception", lambda exc, **kwargs: reports.append(kwargs),
    )
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    result = await _add(provider, [_chosen(20)])

    assert result.files[0].outcome == "added" and storage.ready and storage.deleted_rows == []
    assert [report["operation"] for report in reports] == ["supplement_add_finish_failed"]


@pytest.mark.asyncio
async def test_supplement_pieces_are_stored_without_writing_the_papers_own_object(monkeypatch, storage):
    """The add stores through ``store_added_file_pieces``; ``store_to_weaviate`` (paper status) is never used."""

    used = []

    async def paper_store(*_args, **_kwargs):
        used.append("store_to_weaviate")

    monkeypatch.setattr("src.lib.pipeline.store.store_to_weaviate", paper_store)
    monkeypatch.setattr("src.lib.pipeline.store.store_added_file_pieces", store_added_file_pieces)
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    statuses = []
    monkeypatch.setattr(
        "src.lib.pipeline.store.update_document_status_detailed",
        AsyncMock(side_effect=lambda *a, **k: statuses.append(k)),
    )
    monkeypatch.setattr("src.lib.pipeline.store.store_chunks_to_weaviate", AsyncMock(side_effect=RuntimeError("made-up")))
    with pytest.raises(Exception):
        await _add(provider, [_chosen(20)])
    assert used == [] and statuses == []


@pytest.mark.asyncio
async def test_rows_naming_the_files_are_written_before_the_files(monkeypatch, storage):
    order = []
    from src.lib.document_sources import ingestion

    original_insert = supplement_add._insert_rows

    def insert(document_id, rows, copy_ids_by_file=None, text_bytes_by_file=None):
        order.append("rows")
        return original_insert(document_id, rows, copy_ids_by_file, text_bytes_by_file)

    async def save_markdown(*, markdown, file_id, user_id):
        order.append("markdown")
        return f"{user_id}/source_markdown/{file_id}.md"

    monkeypatch.setattr(supplement_add, "_insert_rows", insert)
    monkeypatch.setattr(ingestion, "_save_source_markdown", save_markdown)
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    await _add(provider, [_chosen(20)])
    assert order == ["rows", "markdown"]
    row = storage.inserted[0]
    assert row.source_markdown_path == f"owner-subject/source_markdown/{row.id}.md"


@pytest.mark.asyncio
async def test_no_warning_when_every_chosen_file_was_already_in_the_paper(monkeypatch):
    already = AddResult(files=(supplement_add.FileOutcome("20", "left_out", FileReason.ALREADY_IN_PAPER),))
    monkeypatch.setattr(supplement_add, "add_files_to_paper", AsyncMock(return_value=already))
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: AsyncMock())
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda: object())
    reports = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception", lambda exc, **kwargs: reports.append(kwargs),
    )
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))

    await supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
        job_id=JOB_ID, document_id=DOCUMENT_ID, user_id="owner-subject", owner_user_id=7, context=CONTEXT,
        files=(_chosen(20),),
    ))
    assert reports == [] and len(failed) == 1


@pytest.mark.asyncio
async def test_a_converted_office_file_stores_no_page(monkeypatch, storage):
    # ALL-1460: converted text has no page marker, so its elements and pieces claim no page.
    _, provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    result = await _add(provider, [_chosen(20)])

    added = _by_id(result)["20"]
    assert [element["metadata"]["page_number"] for element in storage.saved_json[added.file_id]] == [None] * len(
        storage.saved_json[added.file_id])
    ((_, chunks),) = storage.stored
    assert chunks and [chunk.page_number for chunk in chunks] == [None] * len(chunks)


@pytest.mark.asyncio
async def test_add_with_prebuilt_listing_sends_only_downloads_and_reuses_stored_checks(monkeypatch, storage):
    """ALL-1751: add_files_to_paper_with_listing sends no resolve/show_all and still checks
    every file accurately (unlisted, already in paper, unconverted, changed, refused)."""
    rows = [
        *ROWS,
        _abc_show_all_row(50, "WB", file_class="supplement", file_extension="xlsx", display_name="Made_up_S50"),
    ]
    store = _Store()
    monkeypatch.setattr(file_listing_service, "_stores", lambda _provider: (store.load, store.save))
    client, provider = _provider(rows, GOOD)
    _paper_lookup(monkeypatch)
    listing = await file_listing_service.list_files_for_paper(
        DOCUMENT_ID, owner_user_id=7, provider=provider, context=CONTEXT,
    )
    client.calls.clear()

    # The route's list_files_for_paper must never be called by add_files_to_paper_with_listing.
    monkeypatch.setattr(
        file_listing_service, "list_files_for_paper",
        AsyncMock(side_effect=AssertionError("prebuilt listing must not fall back to list_files_for_paper")),
    )
    # Mark file 40 as already in the paper in live Postgres state even though the snapshot
    # listing had in_this_paper=False, verifying the live DB guard too.
    monkeypatch.setattr(
        supplement_add, "_paper_state",
        lambda *_: _paper(
            source_provider="abc_literature",
            source_provider_reference_id="101",
            live_provider_file_ids=frozenset({"10", "40"}),
        ),
    )

    result = await supplement_add.add_files_to_paper_with_listing(
        DOCUMENT_ID, 7, CONTEXT,
        [
            _chosen(20),                         # ready & passed -> downloaded & added
            _chosen(30),                         # refused in listing -> stored refusal reused, not downloaded
            _chosen(40),                         # already in paper (live DB) -> left out, not downloaded
            _chosen(50, content=b""),            # unconverted -> left out
            _chosen(99, content=b""),            # unlisted -> left out
            _chosen(10, content=b""),            # main file -> left out
        ],
        listing=listing, job_id=JOB_ID, provider=provider, weaviate_client=object(),
        stage_checkpoint=AsyncMock(),
    )

    outcomes = _by_id(result)
    assert outcomes["20"].outcome == "added"
    assert outcomes["30"].reason_code is FileReason.TITLE_COUNT
    assert outcomes["40"].reason_code is FileReason.ALREADY_IN_PAPER
    assert outcomes["50"].reason_code is FileReason.FILE_NOT_TICKABLE
    assert outcomes["99"].reason_code is FileReason.FILE_NOT_IN_REFERENCE
    assert outcomes["10"].reason_code is FileReason.ALREADY_IN_PAPER
    assert client.calls == [("download_referencefile", {"artifact_id": "21", "max_bytes": 1_000_000})]


@pytest.mark.asyncio
async def test_add_with_prebuilt_listing_rejects_changed_chosen_or_bytes_and_bad_listings(monkeypatch, storage):
    from src.lib.document_sources.file_listing import FileListing
    from src.lib.document_sources.file_listing_service import FileListingUnavailable

    client, provider = _provider(ROWS, {"21": GOOD["21"] + b"changed"})
    _paper_lookup(monkeypatch)
    # Build listing against original bytes so listing's converted_md5 matches GOOD["21"],
    # then the add's download returns mutated bytes.
    listing_client, listing_provider = _provider(ROWS, GOOD)
    listing = await file_listing_service.list_files_for_paper(
        DOCUMENT_ID, owner_user_id=7, provider=listing_provider, context=CONTEXT,
    )
    monkeypatch.setattr(
        file_listing_service, "list_files_for_paper",
        AsyncMock(side_effect=AssertionError("must not fall back to list_files_for_paper")),
    )
    monkeypatch.setattr(
        supplement_add, "_paper_state",
        lambda *_: _paper(source_provider="abc_literature", source_provider_reference_id="101"),
    )

    result = await supplement_add.add_files_to_paper_with_listing(
        DOCUMENT_ID, 7, CONTEXT,
        [_chosen(20), _chosen(40, md5_suffix="-stale")],
        listing=listing, job_id=JOB_ID, provider=provider, weaviate_client=object(),
        stage_checkpoint=AsyncMock(),
    )
    assert _by_id(result)["20"].reason_code is FileReason.FILE_CHANGED_SINCE_LISTED
    assert _by_id(result)["40"].reason_code is FileReason.FILE_CHANGED_SINCE_LISTED
    assert storage.inserted == []
    assert [name for name, _ in client.calls] == ["download_referencefile"]

    # Invalid listing / provider mismatch / paper reference mismatch / uploaded paper fail cleanly.
    with pytest.raises(SupplementAddError) as bad_type:
        await supplement_add.add_files_to_paper_with_listing(
            DOCUMENT_ID, 7, CONTEXT, [_chosen(20)], listing=None,  # type: ignore[arg-type]
            job_id=JOB_ID, provider=provider, weaviate_client=object(), stage_checkpoint=AsyncMock(),
        )
    assert bad_type.value.code == "listing_invalid"

    other_provider_listing = FileListing(
        provider="other_provider", reference_id="101", reference_curie="OTHER:101", main_status="ready",
        files=(), not_shown_count=0, duplicate_copies_count=0, check_version="v", existing_document_id=None,
    )
    with pytest.raises(SupplementAddError) as bad_provider:
        await supplement_add.add_files_to_paper_with_listing(
            DOCUMENT_ID, 7, CONTEXT, [_chosen(20)], listing=other_provider_listing,
            job_id=JOB_ID, provider=provider, weaviate_client=object(), stage_checkpoint=AsyncMock(),
        )
    assert bad_provider.value.code == "listing_provider_mismatch"

    other_ref_listing = FileListing(
        provider="abc_literature", reference_id="999", reference_curie="AGRKB:999", main_status="ready",
        files=(), not_shown_count=0, duplicate_copies_count=0, check_version="v", existing_document_id=None,
    )
    with pytest.raises(FileListingUnavailable) as bad_ref:
        await supplement_add.add_files_to_paper_with_listing(
            DOCUMENT_ID, 7, CONTEXT, [_chosen(20)], listing=other_ref_listing,
            job_id=JOB_ID, provider=provider, weaviate_client=object(), stage_checkpoint=AsyncMock(),
        )
    assert bad_ref.value.code == "paper_reference_mismatch"

    monkeypatch.setattr(
        supplement_add, "_paper_state",
        lambda *_: _paper(source_provider=None, source_provider_reference_id=None, source_provider_reference_curie=None),
    )
    with pytest.raises(FileListingUnavailable) as uploaded:
        await supplement_add.add_files_to_paper_with_listing(
            DOCUMENT_ID, 7, CONTEXT, [_chosen(20)], listing=listing,
            job_id=JOB_ID, provider=provider, weaviate_client=object(), stage_checkpoint=AsyncMock(),
        )
    assert uploaded.value.code == "not_imported_from_provider"

    monkeypatch.setattr(
        supplement_add, "_paper_state",
        lambda *_: _paper(source_provider="abc_literature", source_provider_reference_id="101", source_provider_reference_curie=None),
    )
    with pytest.raises(FileListingUnavailable) as missing_curie:
        await supplement_add.add_files_to_paper_with_listing(
            DOCUMENT_ID, 7, CONTEXT, [_chosen(20)], listing=listing,
            job_id=JOB_ID, provider=provider, weaviate_client=object(), stage_checkpoint=AsyncMock(),
        )
    assert missing_curie.value.code == "paper_reference_incomplete"


@pytest.mark.asyncio
async def test_add_with_prebuilt_listing_enforces_caller_authorization_and_artifact_binding(monkeypatch, storage):
    """ALL-1751 review: a prebuilt listing for FB must not grant access to a WB caller (even
    under SERVICE bearer), and per-artifact provider/reference/parent mismatches fail closed."""
    from dataclasses import replace

    fb_rows = [_abc_show_all_main_pdf(10, "FB"), *_pair(20, "FB", content=GOOD["21"])]
    fb_context = DocumentSourceRequestContext(
        provider_groups=("FB",), authorized_group_ids=("FB",),
        bearer_kind=ProviderBearerKind.CURATOR, curator_token="fb-token",
    )
    listing_client, listing_provider = _provider(fb_rows, GOOD)
    _paper_lookup(monkeypatch)
    fb_listing = await file_listing_service.list_files_for_paper(
        DOCUMENT_ID, owner_user_id=7, provider=listing_provider, context=fb_context,
    )

    wb_service_context = DocumentSourceRequestContext(
        provider_groups=("WB",), authorized_group_ids=("WB",),
        bearer_kind=ProviderBearerKind.SERVICE, curator_token="service-token",
    )
    client, provider = _provider(fb_rows, GOOD)
    result = await supplement_add.add_files_to_paper_with_listing(
        DOCUMENT_ID, 7, wb_service_context, [_chosen(20)],
        listing=fb_listing, job_id=JOB_ID, provider=provider, weaviate_client=object(),
        stage_checkpoint=AsyncMock(),
    )

    outcome = _by_id(result)["20"]
    assert outcome.outcome == "left_out"
    assert outcome.reason_code is FileReason.FILE_NOT_IN_REFERENCE
    assert outcome.display_name is None
    assert client.calls == []
    assert storage.inserted == []

    # Per-artifact binding mismatch on source or converted artifact raises listing_invalid.
    supplement_entry = next(item for item in fb_listing.files if item.provider_file_id == "20")
    assert supplement_entry.converted_artifact is not None
    bad_listings = [
        replace(
            fb_listing,
            files=(replace(supplement_entry, source_artifact=replace(supplement_entry.source_artifact, reference_id="999")),),
        ),
        replace(
            fb_listing,
            files=(replace(supplement_entry, converted_artifact=replace(supplement_entry.converted_artifact, reference_id="999")),),
        ),
        replace(
            fb_listing,
            files=(replace(supplement_entry, converted_artifact=replace(supplement_entry.converted_artifact, parent_artifact_id="99")),),
        ),
    ]
    for bad_listing in bad_listings:
        with pytest.raises(SupplementAddError) as raised:
            await supplement_add.add_files_to_paper_with_listing(
                DOCUMENT_ID, 7, fb_context, [_chosen(20)],
                listing=bad_listing, job_id=JOB_ID, provider=provider, weaviate_client=object(),
                stage_checkpoint=AsyncMock(),
            )
        assert raised.value.code == "listing_invalid"
    assert client.calls == []


@pytest.mark.asyncio
async def test_401_on_converted_text_or_pdf_download_propagates_login_expired_without_further_calls(
    monkeypatch, storage, tmp_path,
):
    """ALL-1751 review: HTTP 401 during add downloads must propagate DocumentSourceLoginExpired
    immediately instead of turning into source_download_failed and retrying subsequent files."""
    from src.lib.document_sources.models import DocumentSourceLoginExpired
    from tests.unit.lib.document_sources.test_file_listing import _http_error
    from tests.unit.lib.document_sources.test_supplement_add_pdf import PDF, PDF_TEXT

    # Case 1: 401 on converted text download of first file stops before downloading second file.
    listing_client, listing_provider = _provider(ROWS, GOOD)
    _paper_lookup(monkeypatch)
    listing = await file_listing_service.list_files_for_paper(
        DOCUMENT_ID, owner_user_id=7, provider=listing_provider, context=CONTEXT,
    )
    client, provider = _provider(ROWS, {"21": _http_error(401), "41": GOOD["41"]})
    with pytest.raises(DocumentSourceLoginExpired):
        await supplement_add.add_files_to_paper_with_listing(
            DOCUMENT_ID, 7, CONTEXT, [_chosen(20), _chosen(40)],
            listing=listing, job_id=JOB_ID, provider=provider, weaviate_client=object(),
            stage_checkpoint=AsyncMock(),
        )
    assert [kwargs["artifact_id"] for name, kwargs in client.calls if name == "download_referencefile"] == ["21"]

    # Case 2: 401 on original PDF download stops before downloading second file.
    pdf_md5 = hashlib.md5(PDF).hexdigest()
    pdf_rows = [
        _abc_show_all_main_pdf(10, "WB"),
        *_pair(20, "WB", extension="pdf", content=PDF_TEXT, md5=pdf_md5),
        *_pair(40, "WB", content=GOOD["41"]),
    ]
    _, pdf_listing_provider = _provider(pdf_rows, {"20": PDF, "21": PDF_TEXT, "41": GOOD["41"]})
    pdf_listing = await file_listing_service.list_files_for_paper(
        DOCUMENT_ID, owner_user_id=7, provider=pdf_listing_provider, context=CONTEXT,
    )
    spool = tmp_path / ".add_spool"
    spool.mkdir()
    monkeypatch.setattr(supplement_add, "_spool_directory", lambda: spool)
    pdf_client, pdf_provider = _provider(pdf_rows, {"20": _http_error(401), "21": PDF_TEXT, "41": GOOD["41"]})
    chosen_pdf = ChosenFile("20", md5=pdf_md5, converted_md5=hashlib.md5(PDF_TEXT).hexdigest())
    with pytest.raises(DocumentSourceLoginExpired):
        await supplement_add.add_files_to_paper_with_listing(
            DOCUMENT_ID, 7, CONTEXT, [chosen_pdf, _chosen(40)],
            listing=pdf_listing, job_id=JOB_ID, provider=pdf_provider, weaviate_client=object(),
            stage_checkpoint=AsyncMock(),
        )
    assert [kwargs["artifact_id"] for name, kwargs in pdf_client.calls if name == "download_referencefile"] == [
        "21", "20",
    ]


@pytest.mark.asyncio
async def test_manual_add_job_catches_login_expired_and_records_per_file_outcomes(monkeypatch, storage):
    """ALL-1751 review: a 401 during a manual run_supplement_add_job records login_expired
    cleanly for every chosen file without generic add_failed or re-raising."""
    from src.lib.document_sources.file_listing_service import provider_observations_from_jobs
    from tests.unit.lib.document_sources.test_file_listing import _http_error

    _paper_lookup(monkeypatch)
    # Warm stored checks (as the curator's listing already did) so the 401 fires during add download.
    _, warm_provider = _provider(ROWS, GOOD)
    await file_listing_service.list_files_for_paper(
        DOCUMENT_ID, owner_user_id=7, provider=warm_provider, context=CONTEXT,
    )

    client, provider = _provider(ROWS, {"21": _http_error(401), "31": GOOD["31"], "41": GOOD["41"]})
    monkeypatch.setattr(
        "src.lib.document_sources.registry.get_configured_document_source_provider",
        lambda: provider,
    )
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda: object())
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_kwargs: None)
    reports = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda exc, **kwargs: reports.append(kwargs),
    )
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))

    await supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
        job_id=JOB_ID, document_id=DOCUMENT_ID, user_id="owner-subject", owner_user_id=7,
        context=CONTEXT, files=(_chosen(20), _chosen(40)),
    ))

    assert reports == []
    assert len(failed) == 1
    meta = failed[0]["metadata"]
    assert meta["error_code"] == FileReason.LOGIN_EXPIRED.value
    assert [(entry["provider_file_id"], entry["state"], entry["reason_code"]) for entry in meta["supplements"]] == [
        ("20", "left_out", "login_expired"),
        ("40", "left_out", "login_expired"),
    ]
    assert provider_observations_from_jobs([meta]) == {}
    assert [kwargs["artifact_id"] for name, kwargs in client.calls if name == "download_referencefile"] == ["21"]


@pytest.mark.asyncio
async def test_manual_add_job_preserves_settled_refusal_and_display_name_when_later_file_hits_401(
    monkeypatch, storage,
):
    """ALL-1751 review: when file A settles as file_changed_since_listed before file B's
    download returns 401, run_supplement_add_job preserves A's refusal and display_name and
    marks only unfinished file B as login_expired."""
    from src.lib.document_sources.file_listing_service import provider_observations_from_jobs
    from tests.unit.lib.document_sources.test_file_listing import _http_error

    _paper_lookup(monkeypatch)
    # Warm stored checks (as the curator's listing already did) so list_files_for_paper succeeds
    # and the 401 occurs on file 40's add download after file 20 has already settled.
    _, warm_provider = _provider(ROWS, GOOD)
    await file_listing_service.list_files_for_paper(
        DOCUMENT_ID, owner_user_id=7, provider=warm_provider, context=CONTEXT,
    )

    client, provider = _provider(ROWS, {"21": GOOD["21"], "31": GOOD["31"], "41": _http_error(401)})
    monkeypatch.setattr(
        "src.lib.document_sources.registry.get_configured_document_source_provider",
        lambda: provider,
    )
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda: object())
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_kwargs: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_kwargs: None)
    reports = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda exc, **kwargs: reports.append(kwargs),
    )
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))

    stale_file_20 = ChosenFile("20", md5="md5-20", converted_md5="0" * 32)
    await supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
        job_id=JOB_ID, document_id=DOCUMENT_ID, user_id="owner-subject", owner_user_id=7,
        context=CONTEXT, files=(stale_file_20, _chosen(40)),
    ))

    assert reports == []
    assert len(failed) == 1
    meta = failed[0]["metadata"]
    assert meta["error_code"] == FileReason.LOGIN_EXPIRED.value
    assert [
        (entry["provider_file_id"], entry["display_name"], entry["state"], entry["reason_code"])
        for entry in meta["supplements"]
    ] == [
        ("20", "Made_up_S20.xlsx", "left_out", FileReason.FILE_CHANGED_SINCE_LISTED.value),
        ("40", "Made_up_S40.xlsx", "left_out", FileReason.LOGIN_EXPIRED.value),
    ]
    assert provider_observations_from_jobs([meta]) == {}
    assert [kwargs["artifact_id"] for name, kwargs in client.calls if name == "download_referencefile"] == ["41"]
