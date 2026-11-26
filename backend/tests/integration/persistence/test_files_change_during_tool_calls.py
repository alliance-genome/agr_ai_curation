"""What a run gets when the paper's files change during, or between, its tool calls.

Real stores: PostgreSQL for the paper, its files and the run's source history;
the real test Weaviate for the pieces. Every tool is called through the path a
run uses: ``catalog_service._resolve_package_tool`` (the runner adapter, with
its result checks and model view), the real source binding and end-of-call check. A change
"during" a call happens after the call has read its text and before its
end-of-call check, the window a background add or supplement wait can hit.

The paper: a ready main PDF (pieces 0..2), a ready supplement (3..4) and a
supplement still being added (pending, 5..6). All values are made up.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import BaseModel
from sqlalchemy import delete

from agr_ai_curation_runtime.document_errors import (
    FILES_CHANGED_MESSAGE,
    FILES_CHANGED_RECORD_MESSAGE,
    DocumentReadError,
)
from src.lib.openai_agents import document_references as refs
from src.lib.pipeline.store import store_chunks_to_weaviate
from src.lib.weaviate_client import chunks as chunk_reads
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument, PDFDocumentFile
from tests.pdf_document_test_support import ensure_test_pdf_owner

from .conftest import TEST_USER_ID

BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREFIX = "files_change_during_calls_test_"
TEXT = {0: "Wing disc growth needs Dpp signalling.", 1: "Dpp mutants have small wings.",
        2: "Results confirm the requirement.", 3: "Table row one.", 4: "Table row two.",
        5: "Added methods text.", 6: "More added methods text."}


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "head")


@pytest.fixture
def paper(weaviate_connection, setup_collections, clean_chunks, monkeypatch):
    import asyncio

    with SessionLocal.begin() as db:
        owner_id = ensure_test_pdf_owner(db, auth_sub=TEST_USER_ID)
    document_id = uuid4()
    with SessionLocal.begin() as db:
        db.add(PDFDocument(id=document_id, user_id=owner_id, filename=f"{PREFIX}{document_id}.pdf",
                           status="completed", processing_completed_at=datetime.now(timezone.utc),
                           next_chunk_index=7))
        db.flush()

        def file(**values):
            return PDFDocumentFile(document_id=document_id, file_hash=uuid4().hex * 2, file_size=100, **values)

        main = file(role="main", display_name="main.pdf", status="ready", tab_order=0, display_mode="local_pdf",
                    file_path=f"o/{document_id}/main.pdf", page_count=3, chunk_index_start=0, chunk_index_end=2,
                    chunk_count=3, text_bytes=300)
        sheet = file(role="supplement", display_name="Table S1.xlsx", status="ready", tab_order=1,
                     display_mode="text", file_path=f"o/{document_id}/s1.md", chunk_index_start=3,
                     chunk_index_end=4, chunk_count=2, text_bytes=200)
        adding = file(role="supplement", display_name="Data S2.docx", status="pending", tab_order=2,
                      display_mode="text", file_path=f"o/{document_id}/s2.md", chunk_index_start=5,
                      chunk_index_end=6, chunk_count=2)
        db.add_all([main, sheet, adding])
        db.flush()
        ids = {"document_id": str(document_id), "main": main.id, "sheet": sheet.id, "adding": adding.id}

    async def store():
        for start, end in ((0, 2), (3, 4), (5, 6)):
            await store_chunks_to_weaviate(
                [{"chunk_index": index, "content": TEXT[index], "element_type": "NarrativeText",
                  "page_number": index + 1 if index <= 2 else None,
                  "section_title": "Results" if index <= 2 else "Sheet1",
                  "parent_section": "Results" if index <= 2 else "Sheet1",
                  "subsection": "Fly strains" if index == 1 else None, "metadata": {}}
                 for index in range(start, end + 1)],
                str(document_id), weaviate_connection, TEST_USER_ID, chunk_range=(start, end),
            )

    asyncio.run(store())
    monkeypatch.setattr(chunk_reads, "get_connection", lambda: weaviate_connection)
    run_id = f"run-{uuid4()}"
    ids["run_id"] = run_id
    monkeypatch.setattr(refs, "get_current_user_id", lambda: TEST_USER_ID)
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: run_id)
    monkeypatch.setattr(refs, "get_current_session_id", lambda: None)
    yield ids
    with SessionLocal.begin() as db:
        db.execute(delete(PDFDocument).where(PDFDocument.filename.like(f"{PREFIX}%")))


# --- the changes -------------------------------------------------------------------------------


def grow(ids):
    """A background add or supplement wait finishes: the pending file becomes ready."""
    with SessionLocal.begin() as db:
        db.get(PDFDocumentFile, ids["adding"]).status = "ready"


def remove_other(ids):
    """A file the call did not show is removed (here the supplement; the call reads the main file)."""
    with SessionLocal.begin() as db:
        db.delete(db.get(PDFDocumentFile, ids["sheet"]))


def status_only(ids):
    """A file's status changes without changing the readable set (pending -> failed)."""
    with SessionLocal.begin() as db:
        db.get(PDFDocumentFile, ids["adding"]).status = "failed"


def delete_paper(ids):
    with SessionLocal.begin() as db:
        db.delete(db.get(PDFDocument, UUID(ids["document_id"])))


# --- the tools ---------------------------------------------------------------------------------


def _tool(name, ids):
    """The tool object a run's agent gets: the runner adapter around the package tool."""
    from src.lib.agent_studio import catalog_service

    context = catalog_service.ToolExecutionContext(
        document_id=ids["document_id"], user_id=TEST_USER_ID, database_url="unused",
    )
    return catalog_service._resolve_package_tool(name, context)


def _as_dict(result):
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, str):
        return json.loads(result)
    return result


async def _call(name, ids, arguments):
    from agents.tool_context import ToolContext

    tool = _tool(name, ids)
    raw = json.dumps(arguments)
    context = ToolContext(context=None, tool_name=name, tool_call_id=f"call-{name}", tool_arguments=raw)
    return _as_dict(await tool.on_invoke_tool(context, raw))


def _during(monkeypatch, method, change, ids):
    """Apply ``change`` right after the call has read its text, before its end-of-call check."""
    if method == "search":
        from agr_ai_curation_alliance.tools import weaviate_search

        search = weaviate_search.hybrid_search_chunks

        async def searched(*args, **kwargs):
            result = await search(*args, **kwargs)
            change(ids)
            return result

        monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", searched)
        return
    original = getattr(refs.SourceBinding, method)

    if method == "resolve":
        async def wrapped(self, *args, **kwargs):
            result = await original(self, *args, **kwargs)
            change(ids)
            return result
    else:
        def wrapped(self, *args, **kwargs):
            result = original(self, *args, **kwargs)
            change(ids)
            return result

    monkeypatch.setattr(refs.SourceBinding, method, wrapped)


READ = ("read_chunk", {"chunk_index": 0}, "retain")
SECTION = ("read_section", {"section_name": "Results"}, "retain")
SUBSECTION = ("read_subsection", {"parent_section": "Results", "subsection": "Fly strains"}, "retain")
SEARCH = ("search_document", {"query": "wing disc growth Dpp", "limit": 1, "search_mode": "lexical"}, "search")
RECORD = ("record_evidence", {"entity": "dpp", "selections": [{"chunk_index": 0, "span_index": 0}]}, "resolve")


def _files_read_rows(ids):
    """The run's files-read history rows, as the extraction result reads them (#1118)."""
    from src.lib.document_files.files_read import _rows

    with SessionLocal() as db:
        return _rows(db, owner_id=TEST_USER_ID, run_id=ids["run_id"], invocation_ids=None)


def _read_names(rows):
    return [[entry["file_name"] for entry in row["files"]] for row in rows]


async def _during_call(monkeypatch, ids, timing, change):
    name, arguments, method = timing
    if name == "record_evidence":
        await _call("read_chunk", ids, {"chunk_index": 0})  # the sentence is read first
    ids["rows_before"] = _files_read_rows(ids)
    _during(monkeypatch, method, change, ids)
    return await _call(name, ids, arguments)


def _is_normal(name, result):
    if name == "read_chunk":
        # The model's view renders the passage as numbered sentences.
        return result["chunk"]["chunk_index"] == 0 and TEXT[0] in json.dumps(result)
    if name == "read_section":
        return TEXT[0] in json.dumps(result) and TEXT[3] not in json.dumps(result)
    if name == "read_subsection":
        return TEXT[1] in json.dumps(result)
    if name == "search_document":
        return bool(result["hits"]) and all(hit["chunk_index"] <= 2 for hit in result["hits"])
    return result["status"] == "verified" and TEXT[0] in result["verified_quote"]


FILES_CHANGED = {"status": "files_changed", "error_code": "readable_files_changed", "message": FILES_CHANGED_MESSAGE}
FILES_CHANGED_RECORD = {"status": "files_changed", "error_code": "readable_files_changed", "saved": False,
                        "message": FILES_CHANGED_RECORD_MESSAGE}
ALL_TIMINGS = [READ, SECTION, SUBSECTION, SEARCH, RECORD]
ALL_IDS = ["read_chunk", "read_section", "read_subsection", "search", "record_evidence"]


def _withheld(name):
    return FILES_CHANGED_RECORD if name == "record_evidence" else FILES_CHANGED


# --- during a call -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ALL_TIMINGS, ids=ALL_IDS)
async def test_a_file_becoming_ready_during_a_call_leaves_its_answer_as_it_is(paper, monkeypatch, timing):
    result = await _during_call(monkeypatch, paper, timing, grow)
    assert _is_normal(timing[0], result)
    # The call stands, so its files-read row is written: the main file it showed.
    assert _read_names(_files_read_rows(paper)) == _read_names(paper["rows_before"]) + [["main.pdf"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ALL_TIMINGS, ids=ALL_IDS)
async def test_a_status_change_that_leaves_the_readable_files_alone_changes_nothing(paper, monkeypatch, timing):
    result = await _during_call(monkeypatch, paper, timing, status_only)
    assert _is_normal(timing[0], result)


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ALL_TIMINGS, ids=ALL_IDS)
async def test_a_file_the_call_did_not_show_removed_during_it_asks_the_model_to_read_again(
    paper, monkeypatch, timing,
):
    result = await _during_call(monkeypatch, paper, timing, remove_other)
    assert result == _withheld(timing[0])
    # A withheld call leaves no files-read row (it is written only once the call stands).
    assert _files_read_rows(paper) == paper["rows_before"]
    if timing[0] == "record_evidence":
        # Nothing was saved: no verified result, so no evidence record is built from it.
        from src.lib.openai_agents.evidence_summary import build_record_evidence_summary_record

        assert build_record_evidence_summary_record(
            tool_name="record_evidence", tool_input=timing[1], tool_output=result) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", [
    ("read_chunk", {"chunk_index": 3}, "retain"),
    ("search_document", {"query": "table row", "limit": 1, "search_mode": "lexical"}, "search"),
    ("record_evidence", {"entity": "row", "selections": [{"chunk_index": 3, "span_index": 0}]}, "resolve"),
], ids=["read", "search", "record_evidence"])
async def test_the_file_the_call_was_showing_removed_during_it_stays_a_failed_read(paper, monkeypatch, timing):
    name, arguments, method = timing
    if name == "record_evidence":
        await _call("read_chunk", paper, {"chunk_index": 3})
    before = _files_read_rows(paper)
    _during(monkeypatch, method, remove_other, paper)  # the supplement the call shows
    with pytest.raises(DocumentReadError):
        await _call(name, paper, arguments)
    assert _files_read_rows(paper) == before  # a call that ends the run leaves no row


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ALL_TIMINGS, ids=ALL_IDS)
async def test_the_paper_deleted_during_a_call_stays_a_failed_read(paper, monkeypatch, timing):
    with pytest.raises(DocumentReadError):
        await _during_call(monkeypatch, paper, timing, delete_paper)
    assert _files_read_rows(paper) == paper["rows_before"]


# --- between two calls of one run --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_file_that_became_ready_between_calls_is_read_and_searched_from_the_next_call(paper):
    await _call("read_chunk", paper, {"chunk_index": 0})
    grow(paper)

    added = await _call("read_chunk", paper, {"chunk_index": 5})
    assert added["chunk"]["chunk_index"] == 5 and TEXT[5] in json.dumps(added)
    found = await _call("search_document", paper, {"query": "added methods text", "limit": 5, "search_mode": "lexical"})
    assert 5 in {hit["chunk_index"] for hit in found["hits"]}


@pytest.mark.asyncio
async def test_after_a_file_is_removed_between_calls_the_next_calls_use_the_current_files(paper):
    await _call("read_chunk", paper, {"chunk_index": 3})
    remove_other(paper)

    main = await _call("read_chunk", paper, {"chunk_index": 0})
    assert _is_normal("read_chunk", main)
    gone = await _call("read_chunk", paper, {"chunk_index": 3})
    assert gone.get("chunk") is None and "0 to 2" in gone["summary"]  # an answer naming the readable chunks
    refused = await _call("record_evidence", paper,
                          {"entity": "row", "selections": [{"chunk_index": 3, "span_index": 0}]})
    assert refused.get("status") != "verified"


@pytest.mark.asyncio
async def test_a_status_change_between_calls_changes_nothing(paper):
    await _call("read_chunk", paper, {"chunk_index": 0})
    status_only(paper)
    assert _is_normal("read_chunk", await _call("read_chunk", paper, {"chunk_index": 0}))


@pytest.mark.asyncio
async def test_the_paper_deleted_between_calls_fails_the_next_read(paper):
    await _call("read_chunk", paper, {"chunk_index": 0})
    delete_paper(paper)
    with pytest.raises(DocumentReadError):
        await _call("read_chunk", paper, {"chunk_index": 0})



# --- review additions: the withheld path keeps everything as it was --------------------------


@pytest.mark.asyncio
async def test_a_withheld_update_of_an_existing_evidence_record_leaves_it_unchanged(paper, monkeypatch):
    from copy import deepcopy

    from src.lib.openai_agents.evidence_summary import build_record_evidence_summary_record
    from src.lib.openai_agents.tools.evidence_workspace import (
        reset_active_evidence_records,
        set_active_evidence_records,
    )

    records: list[dict] = []
    token = set_active_evidence_records(records)
    try:
        await _call("read_chunk", paper, {"chunk_index": 0})
        await _call("read_chunk", paper, {"chunk_index": 1})
        arguments = {"entity": "dpp", "selections": [{"chunk_index": 0, "span_index": 0}]}
        first = await _call("record_evidence", paper, arguments)
        assert first["status"] == "verified"
        records.append(build_record_evidence_summary_record(
            tool_name="record_evidence", tool_input=arguments, tool_output=first))
        before = deepcopy(records)

        _during(monkeypatch, "resolve", remove_other, paper)
        result = await _call("record_evidence", paper, {
            "entity": "dpp", "evidence_record_id": first["evidence_record_id"],
            "selections": [{"chunk_index": 1, "span_index": 0}]})
    finally:
        reset_active_evidence_records(token)

    assert result == FILES_CHANGED_RECORD
    assert records == before  # the staged source update was not applied


@pytest.mark.asyncio
async def test_a_read_of_a_file_removed_in_the_removal_order_before_its_fetch_asks_to_read_again(
    paper, monkeypatch,
):
    """The removal deletes the file's row, then its pieces. Both land after the call bound
    its readable files and before read_chunk fetches the (removed) chunk: the call showed
    nothing, so it is withheld, decided from the readable files, not the failed fetch."""
    from agr_ai_curation_alliance.tools import weaviate_search
    from src.lib.weaviate_client.chunks import delete_chunk_range

    fetch = weaviate_search.get_chunk_by_index

    async def removed_then_fetched(*args, **kwargs):
        remove_other(paper)
        delete_chunk_range(paper["document_id"], TEST_USER_ID, start=3, end=4)
        return await fetch(*args, **kwargs)

    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", removed_then_fetched)
    assert await _call("read_chunk", paper, {"chunk_index": 3}) == FILES_CHANGED
    assert _files_read_rows(paper) == []


@pytest.mark.asyncio
async def test_a_failed_fetch_with_the_readable_files_unchanged_stays_a_failed_read(paper, monkeypatch):
    from agr_ai_curation_alliance.tools import weaviate_search

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", nothing)
    with pytest.raises(DocumentReadError):
        await _call("read_chunk", paper, {"chunk_index": 0})
    assert _files_read_rows(paper) == []


@pytest.mark.asyncio
async def test_a_failed_fetch_after_a_read_shown_its_spans_leaves_no_files_read_row(paper, monkeypatch):
    """A section read issues the spans of its first piece, then a later fetch fails:
    span issuance stays (it is immediate), but the failed call writes no files-read row."""
    retain = refs.SourceBinding.retain

    def retained_then_failing(self, reads):
        retain(self, reads)
        raise refs.DocumentReferenceError("fetch failed after the read was retained")

    monkeypatch.setattr(refs.SourceBinding, "retain", retained_then_failing)
    with pytest.raises(DocumentReadError):
        await _call("read_section", paper, {"section_name": "Results"})
    assert _files_read_rows(paper) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("selections", [
    # Sentences from two files: refused as selections in several files.
    [{"chunk_index": 0, "span_index": 0}, {"chunk_index": 3, "span_index": 0}],
    # The first sentence was read, the second never was: refused as unissued.
    [{"chunk_index": 3, "span_index": 0}, {"chunk_index": 1, "span_index": 0}],
], ids=["several_files", "unissued_second"])
async def test_a_refused_record_evidence_leaves_no_files_read_row(paper, selections):
    """A refusal shows the model no paper text, so no file is recorded as read for it."""
    await _call("read_chunk", paper, {"chunk_index": 0})
    await _call("read_chunk", paper, {"chunk_index": 3})
    before = _files_read_rows(paper)

    result = await _call("record_evidence", paper, {"entity": "dpp", "selections": selections})

    assert result.get("status") != "verified" and "verified_quote" not in result
    assert _files_read_rows(paper) == before


@pytest.mark.asyncio
async def test_the_files_changed_event_row_names_its_tool_and_call(paper, monkeypatch):
    """The run-history row itself carries the tool name and call id, as other per-call events do."""
    from sqlalchemy import select

    from src.lib.context import set_current_trace_id, set_current_user_id
    from src.lib.openai_agents.run_history import ACTIVE_HISTORY, HistoryScope, RunHistory
    from src.models.sql.agent_run_history import AgentHistoryEvent

    set_current_user_id(TEST_USER_ID)
    set_current_trace_id(paper["run_id"])
    history = RunHistory(HistoryScope(TEST_USER_ID, paper["run_id"], f"inv-{uuid4().hex[:8]}", "extractor", "x/"))
    token = ACTIVE_HISTORY.set(history)
    try:
        assert await _during_call(monkeypatch, paper, READ, remove_other) == FILES_CHANGED
    finally:
        ACTIVE_HISTORY.reset(token)

    with SessionLocal() as db:
        rows = db.execute(select(AgentHistoryEvent.tool_name, AgentHistoryEvent.call_id).where(
            AgentHistoryEvent.run_id == paper["run_id"],
            AgentHistoryEvent.kind == "document_files_changed")).all()
    assert [tuple(row) for row in rows] == [("read_chunk", "call-read_chunk")]


@pytest.mark.asyncio
async def test_a_withheld_call_is_recorded_in_run_history_with_its_tool_and_call_ids(paper, monkeypatch):
    events = []

    async def record(kind, value, **metadata):
        events.append((kind, value))

    monkeypatch.setattr(refs, "record_runtime_payload", record)
    result = await _during_call(monkeypatch, paper, READ, remove_other)

    assert result == FILES_CHANGED
    # Ids only, never text: a trace can tie the event to the withheld call.
    assert events == [("document_files_changed", {
        "document_id": paper["document_id"], "tool_name": "read_chunk", "call_id": "call-read_chunk"})]


def test_a_stored_withheld_read_projects_through_history_unchanged():
    import asyncio

    from agr_ai_curation_runtime.document_errors import files_changed_answer

    for tool in ("read_chunk", "read_section", "read_subsection", "search_document", "record_evidence"):
        stored = {"event": {"tool_name": tool, "kind": "canonical_tool_result"},
                  "payload": files_changed_answer(tool)}
        projected = asyncio.run(refs.project_history_source_result(stored, user_id=TEST_USER_ID))
        assert projected["payload"] == files_changed_answer(tool)


@pytest.mark.asyncio
async def test_fan_out_prefetch_of_a_withheld_read_is_never_shared(paper, monkeypatch):
    from src.lib.openai_agents import fanout_extraction

    _during(monkeypatch, "retain", remove_other, paper)
    runner = fanout_extraction.FanoutExtraction.__new__(fanout_extraction.FanoutExtraction)
    runner.read_tool = _tool("read_chunk", paper)
    runner._chunk_views = {}
    runner.planner_agent = SimpleNamespace()  # a planner run without retained history

    runner.user_id = TEST_USER_ID
    runner.document_id = paper["document_id"]
    [view] = await runner._prefetch_chunks([0])
    assert view.index == 0

    assert FILES_CHANGED_MESSAGE in view.text
    assert not runner._chunk_views  # the next writer that cites chunk 0 reads it again


async def _fanout_run(paper, monkeypatch, read, plan):
    from src.lib.openai_agents import fanout_extraction as fanout
    from src.lib.openai_agents.extraction_builder_workspace import ExtractionBuilderWorkspace

    monkeypatch.setenv("EXTRACTION_FANOUT_MIN_ITEMS", "1")
    agent = SimpleNamespace(
        tools=[read, SimpleNamespace(name="stage"), SimpleNamespace(name="finalize")],
        cost_boundary={"document_id": paper["document_id"], "user_id": TEST_USER_ID},
    )
    monkeypatch.setattr(fanout.tool_roles, "builder_finalization_tool_names", lambda: {"finalize"})
    monkeypatch.setattr(fanout.tool_roles, "builder_stage_tool_names", lambda: {"stage"})
    run = fanout.prepare_fanout_extraction(
        agent, builder_workspace=ExtractionBuilderWorkspace(run_id=paper["run_id"]),
        specialist_name="extractor", input_text="Extract the records.",
        emit_event=lambda _: None, record_live_evidence=lambda _: None, evidence_records=[],
    )
    assert run is not None
    assert json.loads(await run.submit_tool.on_invoke_tool(None, json.dumps(plan)))["status"] == "accepted"
    run.planner_agent = agent
    run.max_turns = 1
    run.base_run_config = None
    monkeypatch.setattr(run, "_writer_agent", lambda attempt_key: attempt_key[0])
    return run


def _result_files(paper, invocation_ids=None):
    from src.lib.document_files.files_read import files_read_for_result

    with SessionLocal() as db:
        files = files_read_for_result(db, owner_id=TEST_USER_ID, run_id=paper["run_id"],
                                      document_id=paper["document_id"], invocation_ids=invocation_ids)
    return {entry["file_id"]: entry["removed"] for entry in files}


@pytest.mark.asyncio
async def test_fanout_rechecks_prefetched_passages_at_writer_dispatch(paper, monkeypatch):
    import asyncio

    from src.lib.openai_agents import runner

    read = _tool("read_chunk", paper)
    invoke = read.on_invoke_tool
    first_dispatched = asyncio.Event()
    reads = []

    async def read_with_removal(ctx, arguments):
        index = json.loads(arguments)["chunk_index"]
        reads.append(index)
        if index == 1:
            # Writer 2 already holds views of chunks 0 and 3. Let writer 1 use
            # them, then remove the supplement before writer 2 gets its last chunk.
            await first_dispatched.wait()
            remove_other(paper)
        return await invoke(ctx, arguments)

    read.on_invoke_tool = read_with_removal
    plan = {"items": [{"label": "First", "chunks": [0, 3]}, {"label": "Second", "chunks": [0, 3, 1]}],
            "notes": []}
    run = await _fanout_run(paper, monkeypatch, read, plan)
    assignments = {}

    async def capture_writer(index, messages, **kwargs):
        assignments[index] = messages[-1]["content"]
        if index == 1:
            first_dispatched.set()
        return SimpleNamespace(final_output="done", new_items=[])

    monkeypatch.setattr(runner, "run_agent_with_owned_openai_resources", capture_writer)
    await run._run_writers([(0,), (1,)])
    assert TEXT[3] in assignments[1]
    assert TEXT[3] not in assignments[2]
    assert FILES_CHANGED_MESSAGE in assignments[2]
    assert TEXT[1] in assignments[2]

    # A later writer sees the same current range as a fresh resolved read.
    gone = await _call("read_chunk", paper, {"chunk_index": 3})
    await run._run_writer(3, 3, (0,))
    assert gone["summary"] in assignments[3]
    assert TEXT[3] not in assignments[3]
    assert all(TEXT[0] in assignment for assignment in assignments.values())
    assert reads.count(0) == 1
    assert _result_files(paper) == {str(paper["main"]): False, str(paper["sheet"]): True}


@pytest.mark.asyncio
@pytest.mark.parametrize("during_handoff", [False, True], ids=["removal-first", "writer-first"])
async def test_removal_orders_writer_input_and_files_read(paper, monkeypatch, during_handoff):
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError

    from src.lib.context import set_current_trace_id, set_current_user_id

    from src.lib.openai_agents import runner
    from src.lib.openai_agents.run_history import HistoryScope, RunHistory
    from src.lib.pdf_jobs import service as pdf_jobs
    from src.models.sql.agent_run_history import AgentHistoryEvent

    read = _tool("read_chunk", paper)
    plan = {"items": [{"label": "Only", "chunks": [3, 0]}], "notes": []}
    run = await _fanout_run(paper, monkeypatch, read, plan)
    set_current_user_id(TEST_USER_ID)
    set_current_trace_id(paper["run_id"])
    scope = HistoryScope(TEST_USER_ID, paper["run_id"], str(uuid4()), "extractor", "planner/")
    run.planner_agent.run_history = RunHistory(scope)
    invoke = read.on_invoke_tool
    supplement_wait = pdf_jobs.supplement_wait_active
    final_lookup_pending = False
    removal_refused = False

    async def after_prefetch(ctx, arguments):
        nonlocal final_lookup_pending
        result = await invoke(ctx, arguments)
        if json.loads(arguments)["chunk_index"] == 0:
            if during_handoff:
                final_lookup_pending = True
            else:
                remove_other(paper)
        return result

    def remove_sheet_nowait():
        with SessionLocal.begin() as remover:
            sheet = remover.get(PDFDocumentFile, paper["sheet"], with_for_update={"nowait": True})
            remover.delete(sheet)

    def race_final_lookup(db, paper_id):
        nonlocal final_lookup_pending, removal_refused
        if final_lookup_pending:
            final_lookup_pending = False
            # The resolver has already fetched the file rows. Use the same
            # NOWAIT file lock as removal, from an independent transaction.
            try:
                remove_sheet_nowait()
            except OperationalError as exc:
                assert exc.orig.pgcode == "55P03"  # lock_not_available
                removal_refused = True
        return supplement_wait(db, paper_id)

    async def accept_writer(index, messages, **kwargs):
        assignment = messages[-1]["content"]
        assert TEXT[0] in assignment
        if during_handoff:
            assert removal_refused
            assert TEXT[3] in assignment
            # The writer has accepted its input. Removal can now proceed.
            remove_sheet_nowait()
        else:
            assert FILES_CHANGED_MESSAGE in assignment
            assert TEXT[3] not in assignment
        return SimpleNamespace(final_output="done", new_items=[])

    read.on_invoke_tool = after_prefetch
    monkeypatch.setattr(pdf_jobs, "supplement_wait_active", race_final_lookup)
    monkeypatch.setattr(runner, "run_agent_with_owned_openai_resources", accept_writer)
    await run._run_writer(1, 1, (0,))
    expected = {str(paper["main"]): False}
    if during_handoff:
        expected[str(paper["sheet"])] = True
    assert _result_files(paper, [scope.invocation_id]) == expected
    # Both real tool calls retain their canonical history, even when a passage
    # was withheld from the only writer.
    with SessionLocal() as db:
        call_ids = db.scalars(select(AgentHistoryEvent.call_id).where(
            AgentHistoryEvent.invocation_id == scope.invocation_id,
            AgentHistoryEvent.kind == "canonical_tool_result",
        )).all()
    assert sorted(call_ids) == ["fanout_prefetch_0", "fanout_prefetch_3"]
