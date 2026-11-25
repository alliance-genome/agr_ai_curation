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

from src.lib.document_files import service as document_file_service
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
_REAL_MARK_FILES_READY = document_file_service.mark_files_ready
_REAL_AFTER_ADD = supplement_add._after_add

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
    ready_session: Any = None
    completed_in_session: int = 0


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
        row.chunk_index_start, row.chunk_index_end, row.chunk_count = start, start + count - 1, count
        return start

    async def store_to_weaviate(chunks, document_id, client, user_id, *, chunk_range):
        if record.fail_store_on is not None and len(record.stored) == record.fail_store_on:
            raise RuntimeError("made-up storage failure")
        record.stored.append((chunk_range, chunks))

    monkeypatch.setattr("src.lib.document_files.service.reserve_file_chunk_range", reserve)
    monkeypatch.setattr("src.lib.pipeline.store.store_added_file_pieces", store_to_weaviate)
    def mark_ready(items, *, on_ready_in_session=None):
        if on_ready_in_session is not None:
            if record.ready_session is not None:
                on_ready_in_session(record.ready_session)
            record.completed_in_session += 1
        record.ready.update(items)

    monkeypatch.setattr("src.lib.document_files.service.mark_files_ready", mark_ready)

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

    def mark_pending_failed(_doc, ids):
        for row in record.inserted:
            if row.id in ids and row.status == "pending":
                row.status = "failed"

    monkeypatch.setattr(supplement_add, "_mark_pending_rows_failed", mark_pending_failed)
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
    monkeypatch.setattr(weaviate_chunks, "get_connection", lambda **_kwargs: conn)
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
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
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
async def test_a_completed_add_records_each_files_outcome_on_its_job(monkeypatch, storage):
    from src.models.sql.pdf_processing_job import PdfJobStatus
    from tests.unit.lib.pdf_jobs.test_pdf_job_service import _FakeSession

    job = _pending_supplement_job(status=PdfJobStatus.RUNNING.value)
    storage.ready_session = _FakeSession([job])
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_: None)

    await supplement_add.run_supplement_add_job(supplement_add.SupplementAddJobRequest(
        job_id=JOB_ID, document_id=DOCUMENT_ID, user_id="owner-subject", owner_user_id=7, context=CONTEXT,
        files=(_chosen(20), _chosen(30)),
    ))

    assert job.status == PdfJobStatus.COMPLETED.value
    entries = job.metadata_json["supplements"]
    added_id = storage.inserted[0].id
    assert entries[0] == {
        "provider_file_id": "20", "display_name": "Made_up_S20.xlsx", "state": "added", "reason_code": None,
        "observed_at": None, "file_id": str(added_id), "measured_value": None, "limit_value": None,
    }
    assert entries[1]["state"] == "left_out" and entries[1]["reason_code"] == "title_count"
    # The paper's later listing reads these: an add's refusal is never a provider observation.
    assert file_listing_service.provider_observations_from_jobs([job.metadata_json]) == {}


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
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
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
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
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
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
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


def _job_request(
    files: tuple[ChosenFile, ...] | None = None,
    *,
    document_id: UUID = DOCUMENT_ID,
    job_id: UUID = JOB_ID,
    user_id: str = "owner-subject",
) -> supplement_add.SupplementAddJobRequest:
    return supplement_add.SupplementAddJobRequest(
        job_id=job_id,
        document_id=document_id,
        user_id=user_id,
        owner_user_id=7,
        context=CONTEXT,
        files=files if files is not None else (_chosen(20),),
    )


def _pending_supplement_job(
    *,
    job_id: UUID = JOB_ID,
    document_id: UUID = DOCUMENT_ID,
    status: str = "pending",
    filename: str = "Confidential_Paper.pdf",
):
    from datetime import datetime, timezone

    from src.models.sql.pdf_processing_job import PdfProcessingJob

    now = datetime.now(timezone.utc)
    return PdfProcessingJob(
        id=job_id,
        document_id=document_id,
        user_id=7,
        filename=filename,
        status=status,
        current_stage="pending" if status == "pending" else "finishing",
        progress_percentage=0,
        message="Queued for background processing",
        process_id=None,
        cancel_requested=False,
        error_message=None,
        metadata_json={"job_kind": "supplement_add"},
        created_at=now,
        started_at=None if status == "pending" else now,
        updated_at=now,
        completed_at=None,
    )


class _TransactionalReadySession:
    """In-memory transactional session supporting ``with SessionLocal() as s, s.begin():``."""

    def __init__(self, job, file_rows, *, commit_error: Exception | None = None):
        self.job = job
        self.files = {row.id: row for row in file_rows}
        self.commit_error = commit_error
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._snapshot: dict[str, Any] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    def begin(self):
        session = self

        class _Tx:
            def __enter__(self_tx):
                snapshot: dict[str, Any] = {
                    "files": {
                        fid: (row.status, row.source_import_status, row.text_bytes)
                        for fid, row in session.files.items()
                    },
                }
                if session.job is not None:
                    snapshot.update({
                        "job_status": session.job.status,
                        "job_stage": session.job.current_stage,
                        "job_message": session.job.message,
                        "job_progress": session.job.progress_percentage,
                        "job_completed_at": session.job.completed_at,
                        "job_metadata": dict(session.job.metadata_json or {}),
                    })
                session._snapshot = snapshot
                return self_tx

            def __exit__(self_tx, exc_type, exc, tb):
                if exc_type is not None:
                    session._restore()
                    session.rollbacks += 1
                    return False
                if session.commit_error is not None:
                    session._restore()
                    session.rollbacks += 1
                    raise session.commit_error
                session.commits += 1
                return False

        return _Tx()

    def _restore(self):
        snap = self._snapshot
        if self.job is not None:
            self.job.status = snap["job_status"]
            self.job.current_stage = snap["job_stage"]
            self.job.message = snap["job_message"]
            self.job.progress_percentage = snap["job_progress"]
            self.job.completed_at = snap["job_completed_at"]
            self.job.metadata_json = snap["job_metadata"]
        for fid, (status, import_status, text_bytes) in snap["files"].items():
            row = self.files[fid]
            row.status = status
            row.source_import_status = import_status
            row.text_bytes = text_bytes

    def execute(self, *_args, **_kwargs):
        class _Scalar:
            def __init__(self_s, row):
                self_s._row = row

            def scalar_one_or_none(self_s):
                return self_s._row

        return _Scalar(self.job)

    def get(self, model, entity_id, **_kwargs):
        from src.models.sql.pdf_document import PDFDocument

        if model is PDFDocument:
            return object()
        return self.files.get(entity_id)


@pytest.mark.asyncio
async def test_provider_factory_failure_marks_pending_job_failed_and_reports_once(monkeypatch, storage):
    from src.lib.document_sources.models import DocumentSourceConfigError
    from src.lib.observability import background_tasks as bg_observability
    from src.lib.pdf_jobs import service as pdf_job_service
    from src.models.sql.pdf_processing_job import PdfJobStatus
    from tests.unit.lib.pdf_jobs.test_pdf_job_service import _FakeSession, _build_document

    job = _pending_supplement_job(status=PdfJobStatus.PENDING.value)
    document = _build_document(job, status="completed")
    session = _FakeSession([job, document, job])
    monkeypatch.setattr(pdf_job_service, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        "src.lib.document_sources.registry.get_configured_document_source_provider",
        Mock(side_effect=DocumentSourceConfigError("bad secret provider url for curator@example.org")),
    )
    runtime_report = Mock(return_value=True)
    bg_report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", runtime_report)
    monkeypatch.setattr(bg_observability, "report_background_task_exception", bg_report)

    wrapped = bg_observability.observed_background_task(
        supplement_add.run_supplement_add_job,
        task_name="supplement_add",
        tags={"document_id": str(DOCUMENT_ID), "job_id": str(JOB_ID)},
    )
    await wrapped(_job_request())

    assert job.status == PdfJobStatus.FAILED.value
    assert job.current_stage == "failed"
    assert job.metadata_json["error_code"] == "add_failed"
    assert job.completed_at is not None
    assert session.commit_calls == 1
    assert document.status == "completed"
    assert storage.inserted == [] and storage.ready == {}

    runtime_report.assert_called_once()
    bg_report.assert_not_called()
    error, = runtime_report.call_args.args
    assert error.__context__ is error.__cause__ is None
    assert "curator@example.org" not in str(runtime_report.call_args)
    assert runtime_report.call_args.kwargs["component"] == "document_files"
    assert runtime_report.call_args.kwargs["operation"] == "supplement_add_failed"
    assert runtime_report.call_args.kwargs["fingerprint"] == ["supplement_add_failed"]
    assert runtime_report.call_args.kwargs["context"] == {"exception_type": "DocumentSourceConfigError"}
    assert runtime_report.call_args.kwargs["include_request"] is False


@pytest.mark.asyncio
async def test_ready_transaction_commits_files_and_completed_job_atomically_and_rolls_back_both_on_failure(
    monkeypatch, storage,
):
    from sqlalchemy.exc import OperationalError

    from src.lib.document_files import service as file_service
    from src.lib.observability import cost_context
    from src.lib.pdf_jobs import service as pdf_job_service
    from src.models.sql.pdf_processing_job import PdfJobStatus

    monkeypatch.setattr(
        cost_context,
        "execution_context",
        lambda **kwargs: {"activity": "background", "run_id": str(JOB_ID)},
    )
    # Exercise the real document_files.service.mark_files_ready + pdf_jobs.service.mark_completed_in_session.
    monkeypatch.setattr(file_service, "mark_files_ready", _REAL_MARK_FILES_READY)

    # 1) Happy path: single transaction commits both file row ("ready") and job row ("completed").
    job = _pending_supplement_job(status=PdfJobStatus.RUNNING.value)
    tx_session_holder: list[_TransactionalReadySession] = []

    def open_commit_session():
        tx = _TransactionalReadySession(job, storage.inserted)
        tx_session_holder.append(tx)
        return tx

    monkeypatch.setattr("src.models.sql.database.SessionLocal", open_commit_session)
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_: None)
    second_mark_completed = Mock(side_effect=AssertionError("mark_completed must not issue a second DB transaction"))
    monkeypatch.setattr(pdf_job_service, "mark_completed", second_mark_completed)

    await supplement_add.run_supplement_add_job(_job_request())

    (tx,) = tx_session_holder
    assert tx.commits == 1 and tx.rollbacks == 0 and tx.closed
    assert job.status == PdfJobStatus.COMPLETED.value
    assert job.current_stage == "completed"
    assert job.progress_percentage == 100
    assert job.message == "Added 1 of 1 files."
    assert job.metadata_json["supplements"][0]["state"] == "added"
    assert storage.inserted[0].status == "ready"
    assert storage.inserted[0].source_import_status == "completed"
    second_mark_completed.assert_not_called()

    # 2) Failure path: commit failure rolls back BOTH file row and job row atomically; rollback cleans up.
    storage.inserted.clear()
    storage.deleted_rows.clear()
    failing_job = _pending_supplement_job(status=PdfJobStatus.RUNNING.value)
    tx_fail_holder: list[_TransactionalReadySession] = []
    commit_exc = OperationalError(
        "UPDATE pdf_processing_jobs",
        {"filename": "Secret_Paper.pdf", "email": "curator@example.org"},
        RuntimeError("deadlock detected"),
    )

    def open_failing_session():
        tx_fail = _TransactionalReadySession(failing_job, storage.inserted, commit_error=commit_exc)
        tx_fail_holder.append(tx_fail)
        return tx_fail

    monkeypatch.setattr("src.models.sql.database.SessionLocal", open_failing_session)
    failed = []
    monkeypatch.setattr(pdf_job_service, "mark_failed", lambda **kwargs: failed.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    await supplement_add.run_supplement_add_job(_job_request())

    (tx_fail,) = tx_fail_holder
    assert tx_fail.commits == 0 and tx_fail.rollbacks == 1 and tx_fail.closed
    assert failing_job.status == PdfJobStatus.RUNNING.value  # rolled back inside mark_files_ready tx
    assert storage.inserted[0].status == "pending"  # rolled back inside mark_files_ready tx
    assert storage.deleted_rows == [storage.inserted[0].id]  # _undo cleaned uncommitted row
    assert len(failed) == 1 and failed[0]["metadata"] == {"error_code": "add_failed"}
    report.assert_called_once()
    assert report.call_args.kwargs["operation"] == "supplement_add_failed"
    assert "Secret_Paper.pdf" not in str(report.call_args)
    assert "curator@example.org" not in str(report.call_args)


@pytest.mark.asyncio
async def test_provider_aclose_failure_after_commit_still_completes_job_and_reports_once(monkeypatch, storage):
    from src.models.sql.pdf_processing_job import PdfJobStatus
    from tests.unit.lib.pdf_jobs.test_pdf_job_service import _FakeSession

    job = _pending_supplement_job(status=PdfJobStatus.RUNNING.value)
    storage.ready_session = _FakeSession([job])
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock(side_effect=OSError("private-provider-close-url"))
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_: None)
    second_completed = []
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_completed", lambda **kwargs: second_completed.append(kwargs))
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    await supplement_add.run_supplement_add_job(_job_request())

    assert job.status == PdfJobStatus.COMPLETED.value
    assert job.metadata_json["supplements"][0]["state"] == "added"
    assert storage.completed_in_session == 1 and second_completed == [] and failed == []
    assert storage.ready and storage.deleted_rows == []
    provider.aclose.assert_awaited_once()
    report.assert_called_once()
    error, = report.call_args.args
    assert error.__context__ is error.__cause__ is None
    assert "private-provider-close-url" not in str(report.call_args)
    assert report.call_args.kwargs["component"] == "document_files"
    assert report.call_args.kwargs["operation"] == "supplement_add_provider_cleanup_failed"
    assert report.call_args.kwargs["level"] == "warning"
    assert report.call_args.kwargs["fingerprint"] == ["supplement_add_provider_cleanup_failed"]
    assert report.call_args.kwargs["context"] == {"exception_type": "OSError"}
    assert report.call_args.kwargs["include_request"] is False


@pytest.mark.asyncio
async def test_deadline_after_ready_commit_preserves_completed_job_even_if_aclose_fails(monkeypatch, storage):
    from src.models.sql.pdf_processing_job import PdfJobStatus
    from tests.unit.lib.pdf_jobs.test_pdf_job_service import _FakeSession

    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    job = _pending_supplement_job(status=PdfJobStatus.RUNNING.value)
    ready_session = _FakeSession([job])
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock(side_effect=OSError("private-close-after-race"))
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.openai_agents.config.get_document_source_import_timeout_seconds", lambda: 0.05)
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_: None)

    def slow_ready(items, *, on_ready_in_session=None):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(10)
        if on_ready_in_session is not None:
            on_ready_in_session(ready_session)
        storage.ready.update(items)

    monkeypatch.setattr("src.lib.document_files.service.mark_files_ready", slow_ready)
    second_completed = []
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_completed", lambda **kwargs: second_completed.append(kwargs))
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    job_task = asyncio.create_task(supplement_add.run_supplement_add_job(_job_request()))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        # Let the 0.05s job deadline expire while mark_files_ready is still committing.
        await asyncio.sleep(0.1)
    finally:
        release.set()
    await job_task

    assert job.status == PdfJobStatus.COMPLETED.value
    assert job.message == "Added 1 of 1 files."
    assert job.metadata_json["supplements"][0]["state"] == "added"
    assert second_completed == [] and failed == []
    assert storage.ready and storage.deleted_rows == []
    provider.aclose.assert_awaited_once()
    report.assert_called_once()
    assert report.call_args.kwargs["operation"] == "supplement_add_provider_cleanup_failed"


@pytest.mark.asyncio
async def test_double_external_cancellation_during_ready_commit_drains_and_preserves_completed_job(
    monkeypatch, storage,
):
    from src.models.sql.pdf_processing_job import PdfJobStatus
    from tests.unit.lib.pdf_jobs.test_pdf_job_service import _FakeSession

    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    job = _pending_supplement_job(status=PdfJobStatus.RUNNING.value)
    ready_session = _FakeSession([job])
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_: None)

    def slow_ready(items, *, on_ready_in_session=None):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(10)
        if on_ready_in_session is not None:
            on_ready_in_session(ready_session)
        storage.ready.update(items)

    monkeypatch.setattr("src.lib.document_files.service.mark_files_ready", slow_ready)
    cancelled = []
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_cancelled", lambda **kwargs: cancelled.append(kwargs))
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    job_task = asyncio.create_task(supplement_add.run_supplement_add_job(_job_request()))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        job_task.cancel("first-cancel")
        await asyncio.sleep(0)
        assert not job_task.done()
        job_task.cancel("second-cancel")
        await asyncio.sleep(0)
        assert not job_task.done()
        assert provider.aclose.await_count == 0
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError, match="first-cancel"):
        await job_task

    assert job.status == PdfJobStatus.COMPLETED.value
    assert job.metadata_json["supplements"][0]["state"] == "added"
    assert storage.ready and storage.deleted_rows == []
    assert cancelled == [] and failed == []
    provider.aclose.assert_awaited_once()
    report.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mark_cancelled_fails", [False, True])
async def test_single_external_cancellation_before_commit_rolls_back_marks_cancelled_and_propagates(
    monkeypatch, storage, mark_cancelled_fails,
):
    from sqlalchemy.exc import OperationalError

    finishing = asyncio.Event()
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())

    async def waiting_checkpoint(stage):
        if stage == "finishing":
            finishing.set()
            await asyncio.Future()

    monkeypatch.setattr(supplement_add, "_cancel_checkpoint", lambda _job_id, **_kwargs: waiting_checkpoint)
    cancelled = []
    if mark_cancelled_fails:
        db_err = OperationalError(
            "UPDATE pdf_processing_jobs",
            {"filename": "Secret_Cancel.pdf"},
            RuntimeError("db down"),
        )
        monkeypatch.setattr("src.lib.pdf_jobs.service.mark_cancelled", Mock(side_effect=db_err))
    else:
        monkeypatch.setattr("src.lib.pdf_jobs.service.mark_cancelled", lambda **kwargs: cancelled.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    job_task = asyncio.create_task(supplement_add.run_supplement_add_job(_job_request()))
    await asyncio.wait_for(finishing.wait(), timeout=10)
    job_task.cancel("shutdown")

    with pytest.raises(asyncio.CancelledError, match="shutdown"):
        await job_task

    assert storage.ready == {} and storage.deleted_rows == [row.id for row in storage.inserted]
    provider.aclose.assert_awaited_once()
    if mark_cancelled_fails:
        report.assert_called_once()
        assert report.call_args.kwargs["operation"] == "supplement_add_job_status_write_failed"
        assert "Secret_Cancel.pdf" not in str(report.call_args)
    else:
        assert len(cancelled) == 1 and cancelled[0]["job_id"] == JOB_ID
        report.assert_not_called()


@pytest.mark.asyncio
async def test_pdf_cancellation_pre_commit_marks_cancelled_quietly_and_post_commit_preserves_completed(
    monkeypatch, storage,
):
    # 1) Pre-commit PDFCancellationError -> rollback + mark_cancelled quietly.
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())

    async def cancel_checkpoint(stage):
        if stage == "finishing":
            raise PDFCancellationError("Processing cancelled by user request")

    monkeypatch.setattr(supplement_add, "_cancel_checkpoint", lambda _job_id, **_kwargs: cancel_checkpoint)
    cancelled = []
    completed = []
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_cancelled", lambda **kwargs: cancelled.append(kwargs))
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_completed", lambda **kwargs: completed.append(kwargs))
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    await supplement_add.run_supplement_add_job(_job_request())

    assert len(cancelled) == 1 and completed == [] and failed == []
    assert storage.ready == {} and storage.deleted_rows == [row.id for row in storage.inserted]
    provider.aclose.assert_awaited_once()
    report.assert_not_called()

    # 2) Post-commit PDFCancellationError -> atomic ready tx already completed the job; never mark_cancelled.
    cancelled.clear()
    provider_post = AsyncMock()
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider_post)
    committed_result = AddResult(files=(
        supplement_add.FileOutcome("20", "added", file_id=uuid4(), display_name="Made_up_S20.xlsx"),
    ))

    async def committed_then_cancelled(*_args, commit_state=None, **_kwargs):
        assert commit_state is not None
        commit_state.committed = True
        commit_state.result = committed_result
        raise PDFCancellationError("late curator cancel")

    monkeypatch.setattr(supplement_add, "add_files_to_paper", committed_then_cancelled)

    await supplement_add.run_supplement_add_job(_job_request())

    assert cancelled == [] and failed == []
    provider_post.aclose.assert_awaited_once()
    report.assert_not_called()


@pytest.mark.asyncio
async def test_provider_aclose_failure_after_primary_failure_preserves_failure_and_reports_both(monkeypatch, storage):
    storage.fail_store_on = 0
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock(side_effect=OSError("private-cleanup-boom"))
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_: None)
    completed = []
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_completed", lambda **kwargs: completed.append(kwargs))
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    await supplement_add.run_supplement_add_job(_job_request())

    assert completed == [] and len(failed) == 1
    assert failed[0]["stage"] == "failed"
    assert failed[0]["metadata"] == {"error_code": "add_failed"}
    assert storage.ready == {} and storage.deleted_rows == [row.id for row in storage.inserted]
    provider.aclose.assert_awaited_once()

    assert report.call_count == 2
    primary_call, cleanup_call = report.call_args_list
    assert primary_call.kwargs["operation"] == "supplement_add_failed"
    assert primary_call.kwargs["level"] == "error"
    assert primary_call.kwargs["context"] == {"exception_type": "RuntimeError"}
    assert cleanup_call.kwargs["operation"] == "supplement_add_provider_cleanup_failed"
    assert cleanup_call.kwargs["level"] == "warning"
    assert cleanup_call.kwargs["context"] == {"exception_type": "OSError"}
    for call in (primary_call, cleanup_call):
        err, = call.args
        assert err.__context__ is err.__cause__ is None
        assert call.kwargs["include_request"] is False
        assert "private-cleanup-boom" not in str(call)


@pytest.mark.asyncio
async def test_actual_deadline_expiry_marks_job_timed_out_and_reports_once(monkeypatch, storage):
    from src.lib.observability import background_tasks as bg_observability

    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.openai_agents.config.get_document_source_import_timeout_seconds", lambda: 0.02)

    async def slow_checkpoint(stage):
        if stage == "finishing":
            await asyncio.Future()

    monkeypatch.setattr(supplement_add, "_cancel_checkpoint", lambda _job_id, **_kwargs: slow_checkpoint)
    completed = []
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_completed", lambda **kwargs: completed.append(kwargs))
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))
    runtime_report = Mock(return_value=True)
    bg_report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", runtime_report)
    monkeypatch.setattr(bg_observability, "report_background_task_exception", bg_report)

    wrapped = bg_observability.observed_background_task(
        supplement_add.run_supplement_add_job,
        task_name="supplement_add",
        tags={"document_id": str(DOCUMENT_ID), "job_id": str(JOB_ID)},
    )
    await wrapped(_job_request())

    assert completed == [] and len(failed) == 1
    assert failed[0]["stage"] == "timed_out"
    assert failed[0]["message"] == supplement_add._MESSAGE_TIMED_OUT
    assert failed[0]["metadata"] == {"error_code": "add_timed_out"}
    assert storage.ready == {} and storage.deleted_rows == [row.id for row in storage.inserted]
    provider.aclose.assert_awaited_once()

    runtime_report.assert_called_once()
    bg_report.assert_not_called()
    error, = runtime_report.call_args.args
    assert error.__context__ is error.__cause__ is None
    assert runtime_report.call_args.kwargs["component"] == "document_files"
    assert runtime_report.call_args.kwargs["operation"] == "supplement_add_timed_out"
    assert runtime_report.call_args.kwargs["level"] == "error"
    assert runtime_report.call_args.kwargs["fingerprint"] == ["supplement_add_timed_out"]
    assert runtime_report.call_args.kwargs["context"] == {"exception_type": "TimeoutError"}
    assert runtime_report.call_args.kwargs["include_request"] is False


@pytest.mark.asyncio
async def test_upstream_timeout_before_job_deadline_routes_to_add_failed_not_timed_out(monkeypatch):
    provider = AsyncMock()
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.openai_agents.config.get_document_source_import_timeout_seconds", lambda: 60.0)
    monkeypatch.setattr(
        supplement_add,
        "add_files_to_paper",
        AsyncMock(side_effect=TimeoutError("private-upstream-dependency-timeout")),
    )
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    await supplement_add.run_supplement_add_job(_job_request())

    assert len(failed) == 1
    assert failed[0]["stage"] == "failed"
    assert failed[0]["message"] == supplement_add._MESSAGE_FAILED
    assert failed[0]["metadata"] == {"error_code": "add_failed"}
    provider.aclose.assert_awaited_once()
    report.assert_called_once()
    assert report.call_args.kwargs["operation"] == "supplement_add_failed"
    assert report.call_args.kwargs["context"] == {"exception_type": "TimeoutError"}
    assert "private-upstream-dependency-timeout" not in str(report.call_args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, expected_reported",
    [
        (SupplementAddError("stored_file_path_mismatch"), True),
        (SupplementAddError("owner_without_subject"), True),
        (SupplementAddError("paper_not_completed"), False),
        (file_listing_service.FileListingUnavailable("paper_reference_mismatch"), False),
    ],
)
async def test_internal_invariant_errors_report_once_while_business_refusals_stay_quiet(
    monkeypatch, error, expected_reported,
):
    provider = AsyncMock()
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr(supplement_add, "add_files_to_paper", AsyncMock(side_effect=error))
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    await supplement_add.run_supplement_add_job(_job_request())

    assert len(failed) == 1
    assert failed[0]["metadata"] == {"error_code": error.code}
    provider.aclose.assert_awaited_once()
    if expected_reported:
        report.assert_called_once()
        reported_err, = report.call_args.args
        assert reported_err.__context__ is reported_err.__cause__ is None
        assert report.call_args.kwargs["component"] == "document_files"
        assert report.call_args.kwargs["operation"] == "supplement_add_internal_invariant_failed"
        assert report.call_args.kwargs["context"] == {
            "exception_type": "SupplementAddError",
            "error_code": error.code,
        }
        assert report.call_args.kwargs["include_request"] is False
    else:
        report.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["run_supplement_add_job", "update_document_piece_count"])
async def test_weaviate_connection_failure_emits_single_sentry_event_with_error_log_promotion(
    monkeypatch, storage, caplog, path,
):
    import json
    import sentry_sdk
    from sentry_sdk import Client
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.transport import Transport

    from src.lib.observability import cost_context
    from src.lib.observability.sentry import before_send
    from src.lib.weaviate_client import connection as wc
    from src.models.sql.pdf_processing_job import PdfJobStatus
    from tests.unit.lib.pdf_jobs.test_pdf_job_service import _FakeSession

    events: list[dict[str, Any]] = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.headers.get("type") == "event":
                    events.append(item.payload.json)

    monkeypatch.setattr(cost_context, "execution_context", lambda **kwargs: {"activity": "background", "run_id": str(JOB_ID)})
    monkeypatch.setattr(wc.WeaviateConnection, "_instance", None)
    monkeypatch.setattr(wc, "_connection", None)
    monkeypatch.setenv("WEAVIATE_HOST", "localhost")
    monkeypatch.setenv("WEAVIATE_PORT", "8080")
    monkeypatch.setenv("WEAVIATE_SCHEME", "http")
    monkeypatch.setattr(
        wc.weaviate,
        "connect_to_local",
        Mock(side_effect=ConnectionError("private-weaviate-host-secret")),
    )

    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_: None)
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))

    job = _pending_supplement_job(status=PdfJobStatus.RUNNING.value)
    if path == "update_document_piece_count":
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_lock(_document_id):
            yield fake_lock_conn

        fake_lock_conn = object()
        # Let chunk storage succeed with fake storage, then exercise the real _after_add -> update_document_piece_count.
        conn = wc.WeaviateConnection(url="http://localhost:8080")
        monkeypatch.setattr(wc, "get_connection", lambda **_kwargs: conn)
        storage.ready_session = _FakeSession([job])
        monkeypatch.setattr("src.lib.document_files.paper_lock.paper_operation_lock", fake_lock)
        monkeypatch.setattr("src.lib.document_files.service.ready_piece_total", lambda _session, _doc: 5)
        monkeypatch.setattr(
            "sqlalchemy.orm.Session",
            lambda *, bind: _TransactionalReadySession(job, ()) if bind is fake_lock_conn else (_ for _ in ()).throw(
                AssertionError("Expected lock connection bind")
            ),
        )
        monkeypatch.setattr(supplement_add, "_after_add", _REAL_AFTER_ADD)

    client = Client(
        dsn="http://public@localhost/1",
        transport=MemoryTransport,
        default_integrations=False,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
        before_send=before_send,
        send_default_pii=False,
        include_local_variables=False,
    )
    try:
        with caplog.at_level(logging.INFO), sentry_sdk.isolation_scope() as sentry_scope:
            sentry_scope.set_client(client)
            await supplement_add.run_supplement_add_job(_job_request())
            client.flush()
    finally:
        client.close()

    assert len(events) == 1
    event = events[0]
    assert "private-weaviate-host-secret" not in json.dumps(event)
    assert "private-weaviate-host-secret" not in caplog.text
    promotable_errors = [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and not getattr(record, "sentry_skip_event", False)
    ]
    assert promotable_errors == []
    if path == "run_supplement_add_job":
        assert event["tags"]["operation"] == "supplement_add_failed"
        assert event["contexts"]["runtime_exception"]["exception_type"] == "ConnectionError"
        assert len(failed) == 1 and failed[0]["metadata"] == {"error_code": "add_failed"}
    else:
        assert event["tags"]["operation"] == "supplement_add_finish_failed"
        assert event["level"] == "warning"
        assert job.status == PdfJobStatus.COMPLETED.value
        assert storage.ready and failed == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    ["after_add_completed", "aclose_completed", "nothing_added_failed", "factory_failed", "timed_out"],
)
async def test_reporting_failure_preserves_durable_job_and_file_state(monkeypatch, storage, caplog, scenario):
    from src.models.sql.pdf_processing_job import PdfJobStatus
    from tests.unit.lib.pdf_jobs.test_pdf_job_service import _FakeSession

    job = _pending_supplement_job(status=PdfJobStatus.RUNNING.value)
    storage.ready_session = _FakeSession([job])
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_: None)
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        Mock(side_effect=RuntimeError("sentry client exploded")),
    )
    completed = []
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_completed", lambda **kwargs: completed.append(kwargs))
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))

    if scenario == "factory_failed":
        monkeypatch.setattr(
            "src.lib.document_sources.registry.get_configured_document_source_provider",
            Mock(side_effect=RuntimeError("provider init boom")),
        )
    else:
        monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)

    if scenario == "after_add_completed":
        storage.after_add_fails = True
    elif scenario == "aclose_completed":
        provider.aclose = AsyncMock(side_effect=OSError("aclose boom"))
    elif scenario == "nothing_added_failed":
        left_out = AddResult(files=(supplement_add.FileOutcome("20", "left_out", FileReason.TOO_LARGE, limit_value=40),))
        monkeypatch.setattr(supplement_add, "add_files_to_paper", AsyncMock(return_value=left_out))
    elif scenario == "timed_out":
        monkeypatch.setattr("src.lib.openai_agents.config.get_document_source_import_timeout_seconds", lambda: 0.02)

        async def slow_checkpoint(stage):
            if stage == "finishing":
                await asyncio.Future()

        monkeypatch.setattr(supplement_add, "_cancel_checkpoint", lambda _job_id, **_kwargs: slow_checkpoint)

    with caplog.at_level(logging.WARNING):
        await supplement_add.run_supplement_add_job(_job_request())

    if scenario in ("after_add_completed", "aclose_completed"):
        assert job.status == PdfJobStatus.COMPLETED.value and completed == [] and failed == []
        assert storage.ready and storage.deleted_rows == []
    else:
        assert completed == [] and len(failed) == 1
        assert storage.ready == {}
        if scenario == "timed_out":
            assert failed[0]["stage"] == "timed_out"
            assert storage.deleted_rows == [row.id for row in storage.inserted]
    promotable_errors = [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and not getattr(record, "sentry_skip_event", False)
    ]
    assert promotable_errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize("content_redaction", ["false", "true"])
@pytest.mark.parametrize(
    "scenario, expected_operations, expected_levels",
    [
        ("factory_failed", ["supplement_add_failed"], ["error"]),
        ("aclose_failed", ["supplement_add_provider_cleanup_failed"], ["warning"]),
        ("timed_out", ["supplement_add_timed_out"], ["error"]),
        ("upstream_timeout", ["supplement_add_failed"], ["error"]),
        ("invariant_failed", ["supplement_add_internal_invariant_failed"], ["error"]),
        ("nothing_added", ["supplement_add_nothing_added"], ["warning"]),
        ("finish_failed", ["supplement_add_finish_failed"], ["warning"]),
        ("post_commit_unexpected", ["supplement_add_failed"], ["error"]),
        (
            "mark_failed_db_error",
            ["supplement_add_job_status_write_failed", "supplement_add_failed"],
            ["error", "error"],
        ),
    ],
)
async def test_supplement_job_sentry_payloads_redact_sensitive_strings_and_emit_no_duplicate_log_event(
    monkeypatch, storage, caplog, content_redaction, scenario, expected_operations, expected_levels,
):
    import json
    import sentry_sdk
    from sentry_sdk import Client
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.transport import Transport
    from sqlalchemy.exc import OperationalError

    from src.lib.observability import background_tasks as bg_observability
    from src.lib.observability import cost_context
    from src.lib.observability.sentry import before_send
    from src.models.sql.pdf_processing_job import PdfJobStatus
    from tests.unit.lib.pdf_jobs.test_pdf_job_service import _FakeSession

    monkeypatch.setenv("SENTRY_CONTENT_REDACTION_ENABLED", content_redaction)
    fake_email = "curator.secret@alliance-example.org"
    fake_doc_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    fake_job_id = UUID("11111111-2222-3333-4444-555555555555")
    fake_filename = "Confidential_Supplement_Table_S99.xlsx"
    fake_exception_text = (
        f"boom fetching https://abc.internal.example/{fake_filename}"
        f"?doc={fake_doc_id}&curator={fake_email}"
    )
    sensitive_strings = (
        fake_email,
        str(fake_doc_id),
        str(fake_job_id),
        fake_filename,
        fake_exception_text,
        "abc.internal.example",
    )

    events: list[dict[str, Any]] = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.headers.get("type") == "event":
                    events.append(item.payload.json)

    monkeypatch.setattr(
        cost_context,
        "execution_context",
        lambda **kwargs: {"activity": "background", "run_id": str(fake_job_id)},
    )
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_: None)
    failed_calls = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed_calls.append(kwargs))

    provider = AsyncMock()
    if scenario == "factory_failed":
        monkeypatch.setattr(
            "src.lib.document_sources.registry.get_configured_document_source_provider",
            Mock(side_effect=RuntimeError(fake_exception_text)),
        )
    elif scenario == "finish_failed":
        _, real_provider = _provider(ROWS, GOOD)
        real_provider.aclose = AsyncMock()
        _paper_lookup(monkeypatch)
        storage.ready_session = _FakeSession([_pending_supplement_job(
            job_id=fake_job_id, document_id=fake_doc_id, status=PdfJobStatus.RUNNING.value, filename=fake_filename,
        )])
        monkeypatch.setattr(supplement_add, "_after_add", AsyncMock(side_effect=RuntimeError(fake_exception_text)))
        monkeypatch.setattr(
            "src.lib.document_sources.registry.get_configured_document_source_provider", lambda: real_provider,
        )
    else:
        monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)

    if scenario == "aclose_failed":
        added = AddResult(files=(
            supplement_add.FileOutcome("20", "added", file_id=uuid4(), display_name=fake_filename),
        ))
        monkeypatch.setattr(supplement_add, "add_files_to_paper", AsyncMock(return_value=added))
        provider.aclose = AsyncMock(side_effect=OSError(fake_exception_text))
    elif scenario == "timed_out":
        monkeypatch.setattr("src.lib.openai_agents.config.get_document_source_import_timeout_seconds", lambda: 0.02)

        async def hang_forever(*_args, **_kwargs):
            await asyncio.Future()

        monkeypatch.setattr(supplement_add, "add_files_to_paper", hang_forever)
    elif scenario == "upstream_timeout":
        monkeypatch.setattr("src.lib.openai_agents.config.get_document_source_import_timeout_seconds", lambda: 60.0)
        monkeypatch.setattr(
            supplement_add,
            "add_files_to_paper",
            AsyncMock(side_effect=TimeoutError(fake_exception_text)),
        )
    elif scenario == "invariant_failed":
        monkeypatch.setattr(
            supplement_add,
            "add_files_to_paper",
            AsyncMock(side_effect=SupplementAddError("stored_file_path_mismatch")),
        )
    elif scenario == "nothing_added":
        left_out = AddResult(files=(
            supplement_add.FileOutcome("20", "left_out", FileReason.TOO_LARGE, limit_value=40, display_name=fake_filename),
        ))
        monkeypatch.setattr(supplement_add, "add_files_to_paper", AsyncMock(return_value=left_out))
    elif scenario == "post_commit_unexpected":
        added = AddResult(files=(
            supplement_add.FileOutcome("20", "added", file_id=uuid4(), display_name=fake_filename),
        ))

        async def committed_then_boom(*_args, commit_state=None, **_kwargs):
            assert commit_state is not None
            commit_state.committed = True
            commit_state.result = added
            raise RuntimeError(fake_exception_text)

        monkeypatch.setattr(supplement_add, "add_files_to_paper", committed_then_boom)
    elif scenario == "mark_failed_db_error":
        monkeypatch.setattr(
            supplement_add,
            "add_files_to_paper",
            AsyncMock(side_effect=RuntimeError(fake_exception_text)),
        )
        db_err = OperationalError(
            "UPDATE pdf_processing_jobs SET status='failed'",
            {"filename": fake_filename, "email": fake_email, "doc": str(fake_doc_id)},
            RuntimeError(fake_exception_text),
        )
        monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", Mock(side_effect=db_err))

    request = _job_request(document_id=fake_doc_id, job_id=fake_job_id, user_id=fake_email)
    wrapped = bg_observability.observed_background_task(
        supplement_add.run_supplement_add_job,
        task_name="supplement_add",
        tags={"document_id": str(fake_doc_id), "job_id": str(fake_job_id)},
    )
    client = Client(
        dsn="http://public@localhost/1",
        transport=MemoryTransport,
        default_integrations=False,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)],
        before_send=before_send,
        send_default_pii=False,
        include_local_variables=False,
    )
    try:
        with caplog.at_level(logging.INFO), sentry_sdk.isolation_scope() as sentry_scope:
            sentry_scope.set_client(client)
            await wrapped(request)
            client.flush()
    finally:
        client.close()

    assert [event["tags"]["operation"] for event in events] == expected_operations
    assert [event["level"] for event in events] == expected_levels
    if scenario == "post_commit_unexpected":
        assert failed_calls == []
    for event in events:
        assert event["tags"]["runtime_component"] == "document_files"
        assert "request" not in event
        if event["tags"]["operation"] in ("supplement_add_nothing_added", "supplement_add_finish_failed"):
            assert event["contexts"]["runtime_exception"]["document_id"].startswith("sha256:")
        serialized = json.dumps(event)
        for secret in sensitive_strings:
            assert secret not in serialized, f"Leaked {secret!r} into Sentry payload: {serialized}"
    for secret in sensitive_strings:
        assert secret not in caplog.text, f"Leaked {secret!r} into logs: {caplog.text}"
    promotable_errors = [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and not getattr(record, "sentry_skip_event", False)
    ]
    assert promotable_errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["cancel_requested", "failed", "completed", "missing", "foreign_document"],
)
async def test_ready_commit_rejects_cancelled_terminal_missing_or_foreign_job(monkeypatch, storage, case):
    from src.lib.document_files import service as file_service
    from src.lib.observability import cost_context
    from src.lib.pdf_jobs import service as pdf_job_service
    from src.models.sql.pdf_processing_job import PdfJobStatus

    monkeypatch.setattr(
        cost_context,
        "execution_context",
        lambda **kwargs: {"activity": "background", "run_id": str(JOB_ID)},
    )
    monkeypatch.setattr(file_service, "mark_files_ready", _REAL_MARK_FILES_READY)

    if case == "missing":
        job = None
    elif case == "foreign_document":
        job = _pending_supplement_job(document_id=uuid4(), status=PdfJobStatus.RUNNING.value)
    elif case == "cancel_requested":
        job = _pending_supplement_job(status=PdfJobStatus.CANCEL_REQUESTED.value)
        job.cancel_requested = True
    elif case == "failed":
        job = _pending_supplement_job(status=PdfJobStatus.FAILED.value)
    else:
        job = _pending_supplement_job(status=PdfJobStatus.COMPLETED.value)

    tx_holder: list[Any] = []

    def open_session():
        tx = _TransactionalReadySession(job, storage.inserted)
        tx_holder.append(tx)
        return tx

    monkeypatch.setattr("src.models.sql.database.SessionLocal", open_session)
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr(pdf_job_service, "is_cancel_requested", lambda **_: False)
    monkeypatch.setattr(pdf_job_service, "update_progress", lambda **_: None)
    cancelled = []
    failed = []
    foreign_job_session = None
    if case == "foreign_document":
        # Exercise real document-scoped mark_failed to confirm a mismatched request never fails another paper's job.
        from tests.unit.lib.pdf_jobs.test_pdf_job_service import _FakeSession

        foreign_job_session = _FakeSession([job])
        monkeypatch.setattr(pdf_job_service, "SessionLocal", lambda: foreign_job_session)
    else:
        monkeypatch.setattr(pdf_job_service, "mark_cancelled", lambda **kwargs: cancelled.append(kwargs))
        monkeypatch.setattr(pdf_job_service, "mark_failed", lambda **kwargs: failed.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    await supplement_add.run_supplement_add_job(_job_request())

    (tx,) = tx_holder
    assert tx.commits == 0 and tx.rollbacks == 1
    assert storage.inserted[0].status == "pending"
    assert storage.deleted_rows == [storage.inserted[0].id]
    assert storage.deleted_ranges and storage.removed_paths
    if case == "cancel_requested":
        assert len(cancelled) == 1 and failed == []
        assert cancelled[0]["document_id"] == DOCUMENT_ID
        report.assert_not_called()
    elif case == "foreign_document":
        assert job is not None and foreign_job_session is not None
        assert job.status == PdfJobStatus.RUNNING.value
        assert job.completed_at is None
        assert job.error_message is None
        assert foreign_job_session.commit_calls == 0
        report.assert_called_once()
        assert report.call_args.kwargs["operation"] == "supplement_add_failed"
        assert report.call_args.kwargs["context"] == {"exception_type": "DocumentFileError"}
        assert str(JOB_ID) not in str(report.call_args)
    else:
        assert cancelled == [] and len(failed) == 1
        assert failed[0]["document_id"] == DOCUMENT_ID
        report.assert_called_once()
        assert report.call_args.kwargs["operation"] == "supplement_add_failed"
        assert report.call_args.kwargs["context"] == {"exception_type": "DocumentFileError"}
        assert str(JOB_ID) not in str(report.call_args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, cancel_requested, expected_exc",
    [
        ("cancel_requested", True, PDFCancellationError),
        ("cancelled", True, PDFCancellationError),
        ("cancelled", False, PDFCancellationError),
        ("failed", False, document_file_service.DocumentFileError),
        ("completed", False, document_file_service.DocumentFileError),
    ],
)
async def test_cancel_checkpoint_fails_fast_on_terminal_or_cancel_requested_progress_response(
    monkeypatch, status, cancel_requested, expected_exc,
):
    from types import SimpleNamespace

    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr(
        "src.lib.pdf_jobs.service.update_progress",
        lambda **_: SimpleNamespace(status=status, cancel_requested=cancel_requested),
    )

    checkpoint = supplement_add._cancel_checkpoint(JOB_ID)
    with pytest.raises(expected_exc):
        await checkpoint("saving_files")


@pytest.mark.asyncio
async def test_after_add_serializes_interleaved_refreshes_and_publishes_latest_sql_count(monkeypatch):
    from contextlib import asynccontextmanager

    from src.lib.document_files.paper_lock import PaperOperationInProgress

    locked = False
    first_writing = asyncio.Event()
    release_first = asyncio.Event()
    second_contended = asyncio.Event()
    sql_count = 3
    published: list[int] = []

    fake_lock_conn = object()

    @asynccontextmanager
    async def non_blocking_paper_lock(_document_id):
        nonlocal locked
        if locked:
            second_contended.set()
            raise PaperOperationInProgress(str(DOCUMENT_ID))
        locked = True
        try:
            yield fake_lock_conn
        finally:
            locked = False

    class _ReadSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, _doc_id):
            return object()

    async def publish_count(_doc_id, count, _client, _subject, *, caller_reports_failure=False):
        assert caller_reports_failure is True
        if count == 3:
            first_writing.set()
            await release_first.wait()
        published.append(count)

    monkeypatch.setattr("src.lib.document_files.paper_lock.paper_operation_lock", non_blocking_paper_lock)
    monkeypatch.setattr(
        "src.models.sql.database.SessionLocal",
        Mock(side_effect=AssertionError("_after_add must not open a second pool connection")),
    )
    monkeypatch.setattr(
        "sqlalchemy.orm.Session",
        lambda *, bind: _ReadSession() if bind is fake_lock_conn else (_ for _ in ()).throw(
            AssertionError("Expected lock connection bind")
        ),
    )
    monkeypatch.setattr("src.lib.document_files.service.ready_piece_total", lambda _s, _d: sql_count)
    monkeypatch.setattr("src.lib.pipeline.store.update_document_piece_count", publish_count)
    monkeypatch.setattr("src.lib.document_cache.invalidate_cache", lambda *_: None)

    first = asyncio.create_task(_REAL_AFTER_ADD(DOCUMENT_ID, "owner-subject", object()))
    await asyncio.wait_for(first_writing.wait(), timeout=10)
    sql_count = 5
    second = asyncio.create_task(_REAL_AFTER_ADD(DOCUMENT_ID, "owner-subject", object()))
    await asyncio.wait_for(second_contended.wait(), timeout=10)
    assert published == []
    release_first.set()
    await asyncio.gather(first, second)

    assert published == [3, 5]


@pytest.mark.asyncio
async def test_cancellation_drains_leftover_cleanup_and_reports_failure_without_duplicate_log(
    monkeypatch, storage, caplog,
):
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    leftover = supplement_add._Leftover(
        file_id=uuid4(),
        chunk_index_start=12,
        chunk_index_end=15,
        paths=("owner-subject/source_markdown/stale.md",),
    )
    monkeypatch.setattr(supplement_add, "_paper_state", lambda *_: _paper(leftovers=(leftover,)))
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    order: list[str] = []

    def blocking_delete_range(_doc_id, _subject, *, start, end, caller_reports_failure=False):
        assert caller_reports_failure is True
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)
        order.append("leftover_done")
        raise ConnectionError("private-leftover-weaviate-secret")

    monkeypatch.setattr(supplement_add, "_delete_pieces", _REAL_DELETE_PIECES)
    monkeypatch.setattr("src.lib.weaviate_client.chunks.delete_chunk_range", blocking_delete_range)
    monkeypatch.setattr(
        "src.lib.pdf_jobs.service.mark_cancelled",
        lambda **kwargs: order.append("mark_cancelled"),
    )
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    with caplog.at_level(logging.ERROR):
        job_task = asyncio.create_task(supplement_add.run_supplement_add_job(_job_request()))
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            job_task.cancel("stop")
            await asyncio.sleep(0)
            assert not job_task.done()
            assert order == []
        finally:
            release.set()

        with pytest.raises(asyncio.CancelledError, match="stop"):
            await job_task

    assert order == ["leftover_done", "mark_cancelled"]
    provider.aclose.assert_awaited_once()
    report.assert_called_once()
    assert report.call_args.kwargs["operation"] == "supplement_add_cancelled_leftover_cleanup_failed"
    assert report.call_args.kwargs["context"] == {"exception_type": "ConnectionError"}
    assert "private-leftover-weaviate-secret" not in str(report.call_args)
    assert "private-leftover-weaviate-secret" not in caplog.text
    promotable_errors = [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and not getattr(record, "sentry_skip_event", False)
    ]
    assert promotable_errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [True, False])
async def test_deadline_drain_reports_distinct_child_exception_and_preserves_outcome_and_external_cancel(
    monkeypatch, committed,
):
    draining = asyncio.Event()
    release = asyncio.Event()
    provider = AsyncMock()
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.openai_agents.config.get_document_source_import_timeout_seconds", lambda: 0.02)

    async def child_fails_during_drain(*_args, commit_state=None, **_kwargs):
        assert commit_state is not None
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            draining.set()
            await release.wait()
            if committed:
                commit_state.committed = True
                commit_state.result = AddResult(files=(
                    supplement_add.FileOutcome("20", "added", file_id=uuid4(), display_name="Made_up_S20.xlsx"),
                ))
            raise OSError("private-spool-close-secret") from None

    monkeypatch.setattr(supplement_add, "add_files_to_paper", child_fails_during_drain)
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    job_task = asyncio.create_task(supplement_add.run_supplement_add_job(_job_request()))
    try:
        await asyncio.wait_for(draining.wait(), timeout=10)
        job_task.cancel("external-stop")
        await asyncio.sleep(0)
        assert not job_task.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError, match="external-stop"):
        await job_task

    provider.aclose.assert_awaited_once()
    assert "private-spool-close-secret" not in str(report.call_args_list)
    if committed:
        assert failed == []
        report.assert_called_once()
        assert report.call_args.kwargs["operation"] == "supplement_add_failed"
        assert report.call_args.kwargs["context"] == {"exception_type": "OSError"}
    else:
        assert len(failed) == 1 and failed[0]["stage"] == "timed_out"
        assert [call.kwargs["operation"] for call in report.call_args_list] == [
            "supplement_add_timed_out",
            "supplement_add_failed",
        ]
        assert report.call_args_list[1].kwargs["context"] == {"exception_type": "OSError"}


@pytest.mark.asyncio
async def test_deadline_drain_preserves_external_cancel_on_normal_return_and_handles_multi_arg_cancel(monkeypatch):
    # 1) Child returns normal left-out result during deadline drain while external cancel arrives.
    draining = asyncio.Event()
    release = asyncio.Event()
    provider = AsyncMock()
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.openai_agents.config.get_document_source_import_timeout_seconds", lambda: 0.02)

    left_out = AddResult(files=(supplement_add.FileOutcome("20", "left_out", FileReason.ALREADY_IN_PAPER),))

    async def finishes_left_out_on_deadline(*_args, **_kwargs):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            draining.set()
            await release.wait()
            return left_out

    monkeypatch.setattr(supplement_add, "add_files_to_paper", finishes_left_out_on_deadline)
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))

    job_task = asyncio.create_task(supplement_add.run_supplement_add_job(_job_request()))
    try:
        await asyncio.wait_for(draining.wait(), timeout=10)
        job_task.cancel("external-shutdown")
        await asyncio.sleep(0)
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError, match="external-shutdown"):
        await job_task
    assert len(failed) == 1 and failed[0]["message"] == supplement_add._MESSAGE_NONE_ADDED

    # 2) Multi-arg CancelledError does not raise TypeError in task.cancel(...).
    monkeypatch.setattr("src.lib.openai_agents.config.get_document_source_import_timeout_seconds", lambda: 60.0)
    started = asyncio.Event()

    async def wait_forever(*_args, **_kwargs):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(supplement_add, "add_files_to_paper", wait_forever)
    cancelled = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_cancelled", lambda **kwargs: cancelled.append(kwargs))

    # Raise multi-arg CancelledError directly from shield to verify no TypeError in task.cancel(...).
    real_shield = asyncio.shield
    injected = False

    def shield_with_multi_arg_cancel(awaitable):
        nonlocal injected
        if not injected:
            injected = True
            raise asyncio.CancelledError("first", "second")
        return real_shield(awaitable)

    monkeypatch.setattr(asyncio, "shield", shield_with_multi_arg_cancel)
    with pytest.raises(asyncio.CancelledError) as raised:
        await supplement_add.run_supplement_add_job(_job_request())
    assert raised.value.args == ("first", "second")
    assert len(cancelled) == 1

    # 3) Non-CancelledError outer BaseException in _drain_add_task re-raises instead of leaving task unfinished.
    class _OuterInterrupt(BaseException):
        pass

    async def _stay_pending() -> AddResult:
        await asyncio.Future()
        return AddResult(files=())

    never_done = asyncio.create_task(_stay_pending())
    monkeypatch.setattr(asyncio, "shield", lambda _awaitable: (_ for _ in ()).throw(_OuterInterrupt()))
    try:
        with pytest.raises(_OuterInterrupt):
            await supplement_add._drain_add_task(never_done)
        assert not never_done.done()
    finally:
        never_done.cancel()
        await asyncio.gather(never_done, return_exceptions=True)


@pytest.mark.asyncio
async def test_external_cancel_draining_ready_commit_with_cancel_requested_stays_quiet(monkeypatch, storage):
    from src.lib.document_files import service as file_service
    from src.lib.observability import cost_context
    from src.lib.pdf_jobs import service as pdf_job_service
    from src.models.sql.pdf_processing_job import PdfJobStatus

    monkeypatch.setattr(
        cost_context,
        "execution_context",
        lambda **kwargs: {"activity": "background", "run_id": str(JOB_ID)},
    )
    job = _pending_supplement_job(status=PdfJobStatus.CANCEL_REQUESTED.value)
    job.cancel_requested = True
    tx_holder: list[Any] = []

    def open_session():
        tx = _TransactionalReadySession(job, storage.inserted)
        tx_holder.append(tx)
        return tx

    monkeypatch.setattr("src.models.sql.database.SessionLocal", open_session)

    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def blocked_mark_files_ready(sizes, *, on_ready_in_session=None):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)
        return _REAL_MARK_FILES_READY(sizes, on_ready_in_session=on_ready_in_session)

    monkeypatch.setattr(file_service, "mark_files_ready", blocked_mark_files_ready)
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr(pdf_job_service, "is_cancel_requested", lambda **_: False)
    monkeypatch.setattr(pdf_job_service, "update_progress", lambda **_: None)
    cancelled = []
    failed = []
    monkeypatch.setattr(pdf_job_service, "mark_cancelled", lambda **kwargs: cancelled.append(kwargs))
    monkeypatch.setattr(pdf_job_service, "mark_failed", lambda **kwargs: failed.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    job_task = asyncio.create_task(supplement_add.run_supplement_add_job(_job_request()))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        job_task.cancel("external-stop")
        await asyncio.sleep(0)
        assert not job_task.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError, match="external-stop"):
        await job_task

    (tx,) = tx_holder
    assert tx.commits == 0 and tx.rollbacks == 1
    assert storage.deleted_rows == [storage.inserted[0].id]
    assert storage.deleted_ranges and storage.removed_paths
    assert len(cancelled) == 1 and cancelled[0]["document_id"] == DOCUMENT_ID
    assert failed == []
    report.assert_not_called()
    provider.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_after_add_uses_piece_count_lock_wait_timeout_and_preserves_completed_job(monkeypatch, storage):
    from contextlib import asynccontextmanager

    from src.lib.document_files.paper_lock import PaperOperationInProgress
    from src.models.sql.pdf_processing_job import PdfJobStatus
    from tests.unit.lib.pdf_jobs.test_pdf_job_service import _FakeSession

    @asynccontextmanager
    async def always_busy_lock(_document_id):
        raise PaperOperationInProgress(str(DOCUMENT_ID))
        yield

    job = _pending_supplement_job(status=PdfJobStatus.RUNNING.value)
    storage.ready_session = _FakeSession([job])
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_: None)
    failed = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_failed", lambda **kwargs: failed.append(kwargs))
    monkeypatch.setattr("src.lib.document_files.paper_lock.paper_operation_lock", always_busy_lock)
    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_document_source_request_timeout_seconds",
        Mock(side_effect=AssertionError("_after_add must use its dedicated lock-wait timeout setting")),
    )
    monkeypatch.setattr("src.lib.openai_agents.config.get_document_file_piece_count_lock_wait_seconds", lambda: 0.05)
    monkeypatch.setattr(supplement_add, "_after_add", _REAL_AFTER_ADD)
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    await supplement_add.run_supplement_add_job(_job_request())

    assert job.status == PdfJobStatus.COMPLETED.value
    assert storage.ready and storage.deleted_rows == []
    assert failed == []
    report.assert_called_once()
    assert str(report.call_args.args[0]) == "Updating the paper after an add failed (PaperOperationInProgress)"
    assert report.call_args.kwargs["operation"] == "supplement_add_finish_failed"
    assert report.call_args.kwargs["level"] == "warning"
    assert report.call_args.kwargs["context"] == {"document_id": str(DOCUMENT_ID)}


@pytest.mark.asyncio
async def test_cancellation_during_insert_rows_file_removal_unfinished_emits_no_sentry_event(
    monkeypatch, storage, caplog,
):
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def blocked_insert_rows(*_args, **_kwargs):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10)
        raise supplement_add.FileRemovalUnfinished(DOCUMENT_ID)

    monkeypatch.setattr(supplement_add, "_insert_rows", blocked_insert_rows)
    _, provider = _provider(ROWS, GOOD)
    provider.aclose = AsyncMock()
    _paper_lookup(monkeypatch)
    monkeypatch.setattr("src.lib.document_sources.registry.get_configured_document_source_provider", lambda: provider)
    monkeypatch.setattr("src.lib.weaviate_client.connection.get_connection", lambda **_kwargs: object())
    monkeypatch.setattr("src.lib.pdf_jobs.service.is_cancel_requested", lambda **_: False)
    monkeypatch.setattr("src.lib.pdf_jobs.service.update_progress", lambda **_: None)
    cancelled = []
    monkeypatch.setattr("src.lib.pdf_jobs.service.mark_cancelled", lambda **kwargs: cancelled.append(kwargs))
    report = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)

    with caplog.at_level(logging.ERROR):
        job_task = asyncio.create_task(supplement_add.run_supplement_add_job(_job_request()))
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            job_task.cancel("stop-during-insert")
            await asyncio.sleep(0)
            assert not job_task.done()
        finally:
            release.set()

        with pytest.raises(asyncio.CancelledError, match="stop-during-insert"):
            await job_task

    assert len(cancelled) == 1
    assert cancelled[0]["document_id"] == DOCUMENT_ID
    assert cancelled[0]["reconcile_document"] is False
    provider.aclose.assert_awaited_once()
    report.assert_not_called()
    promotable_errors = [
        record
        for record in caplog.records
        if record.levelno >= logging.ERROR and not getattr(record, "sentry_skip_event", False)
    ]
    assert promotable_errors == []
