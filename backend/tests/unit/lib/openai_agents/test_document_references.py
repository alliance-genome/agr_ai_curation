"""Local ordinals retain exact source meaning across restart, paging and scopes."""
from tests.sdk_tool_context_support import tool_context
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agr_ai_curation_runtime.document_references import DocumentSelection
from agr_ai_curation_runtime.evidence_spans import build_evidence_spans
from src.models.sql.agent_run_history import AgentHistoryEvent, AgentHistoryPayload
from src.lib.openai_agents import document_references as refs
from src.lib.openai_agents.run_history import HistoryScope, HistoryStore
from tests.readable_files_support import files, single_file


@pytest.fixture(autouse=True)
def alliance_package_path(monkeypatch):
    # These tests exercise the isolated package even when collected on their own.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[5] /
        "packages/alliance/python/src"))


@pytest.fixture(autouse=True)
def readable_papers(monkeypatch):
    """Each paper reads as one file unless a test sets its readable files."""
    values = {}
    monkeypatch.setattr(refs, "resolve_readable_files",
                        lambda document_id, user_id: values.get(document_id, single_file(document_id)))
    return values


@pytest.fixture
def store():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AgentHistoryPayload.__table__.create(engine)
    AgentHistoryEvent.__table__.create(engine)
    yield HistoryStore(sessionmaker(bind=engine, expire_on_commit=False))
    engine.dispose()


@pytest.fixture
def scope():
    return HistoryScope("alice", "run-a", "inv-a", "extractor", "inv-a/", "chat-a")


def binding(store, scope, *, doc="doc-a", generation="generation-a", readable=None):
    value = refs.SourceBinding(doc, scope.owner_id, generation, scope, store,
        refs._read_bindings(store, scope, doc), readable or single_file(doc))
    value.pin_generation()
    return value


def source(index=42, text="Not expressed in adults. α😀 signal is absent."):
    chunk = {"id": "89220bef-62f7-0440-12bb-1d34e1f98ce0", "chunk_index": index, "text": text}
    spans = [span.to_dict() for span in build_evidence_spans(chunk_id=chunk["id"], chunk_text=text)]
    return chunk, spans


@pytest.mark.parametrize("value", [True, False, "42", -1, 1.2, None])
@pytest.mark.parametrize("field", ["chunk_index", "span_index"])
def test_selection_schema_does_not_coerce_indexes(value, field):
    with pytest.raises(ValidationError):
        DocumentSelection(**{"chunk_index": 42, "span_index": 0, field: value})


@pytest.mark.asyncio
async def test_restart_and_new_specialist_keep_issued_span_meaning(store, scope, monkeypatch):
    chunk, spans = source()
    first = binding(store, scope)
    first.retain([(chunk, spans, [0])])
    restarted = binding(HistoryStore(store.session_factory), replace(scope, run_id="run-b", invocation_id="other", lineage="other/"))
    monkeypatch.setattr(refs, "get_chunk_by_index", AsyncMock(return_value=chunk))
    selections = [DocumentSelection(chunk_index=42, span_index=0)] * 2
    assert await restarted.resolve(selections) == [spans[0]["span_id"]]
    refs.get_chunk_by_index.assert_awaited_once_with(
        chunk_index=42, user_id="alice", document_id="doc-a", readable=single_file("doc-a"))


@pytest.mark.parametrize("change", ["owner", "session", "document"])
@pytest.mark.asyncio
async def test_same_numbers_do_not_cross_owner_session_or_document(store, scope, change):
    chunk, spans = source()
    binding(store, scope).retain([(chunk, spans, [0])])
    other = replace(scope, **({"owner_id": "bob"} if change == "owner" else {"session_id": "chat-b"} if change == "session" else {}))
    foreign = binding(store, other, doc="doc-b" if change == "document" else "doc-a")
    with pytest.raises(refs.DocumentReferenceError, match="Read the selected chunk"):
        await foreign.resolve([DocumentSelection(chunk_index=42, span_index=0)])


def test_reprocessing_invalidates_conversation_without_rebinding(store, scope):
    binding(store, scope)
    with pytest.raises(refs.DocumentReferenceError, match="source changed"):
        binding(store, scope, generation="generation-b")


@pytest.mark.parametrize("change", ["text", "uuid", "span"])
def test_reread_does_not_reinterpret_an_issued_ordinal(store, scope, change):
    chunk, spans = source()
    original = binding(store, scope)
    original.retain([(chunk, spans, [0])])
    if change == "text":
        chunk = {**chunk, "text": chunk["text"] + " Changed."}
    if change == "uuid":
        chunk = {**chunk, "id": "another-source"}
    if change == "span":
        spans = [{**spans[0], "char_end": spans[0]["char_end"] + 1}]
    with pytest.raises(ValueError):
        binding(store, scope).retain([(chunk, spans, [0])])


@pytest.mark.asyncio
async def test_span_window_must_have_been_issued_and_current_source_verified(store, scope, monkeypatch):
    chunk, spans = source(text="First sentence.\n\nSecond sentence.")
    assert len(spans) > 1
    value = binding(store, scope)
    value.retain([(chunk, spans, [0])])
    with pytest.raises(refs.DocumentReferenceError, match="span window"):
        await value.resolve([DocumentSelection(chunk_index=42, span_index=1)])
    value.retain([(chunk, spans, [1])])
    monkeypatch.setattr(refs, "get_chunk_by_index", AsyncMock(return_value={**chunk, "text": chunk["text"] + " Mutation."}))
    with pytest.raises(refs.DocumentReferenceError, match="no longer matches"):
        await value.resolve([DocumentSelection(chunk_index=42, span_index=1)])


@pytest.mark.asyncio
async def test_public_sdk_discovery_read_record_and_model_projection_preserve_canonical_source(
    store, scope, monkeypatch,
):
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from agents import RunConfig
    from agents.run import ModelInputData
    from agr_ai_curation_alliance.tools import documents, weaviate_search
    from src.lib.openai_agents.tools import record_evidence
    from src.lib.openai_agents.evidence_summary import build_record_evidence_summary_record
    from src.lib.openai_agents.model_context_view import run_config_with_history

    chunk, spans = source()
    chunk.update(document_id="doc-a", page_number=3, section_title="Results", metadata={"page_number": 3, "section_title": "Results"})
    @asynccontextmanager
    async def scoped(doc, user):
        assert (doc, user) == ("doc-a", "alice")
        token = refs._ACTIVE_SOURCE.set(binding(store, scope))
        try:
            yield
        finally:
            refs._ACTIVE_SOURCE.reset(token)

    monkeypatch.setattr(refs, "document_reference_scope", scoped)
    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", AsyncMock(return_value=chunk))
    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", AsyncMock(return_value=[chunk]))
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", AsyncMock(return_value={"previous_chunk_id": "prior", "next_chunk_id": None}))
    monkeypatch.setattr(refs, "get_chunk_by_index", AsyncMock(return_value=chunk))
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", AsyncMock(return_value=chunk))
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_chunk_by_id", AsyncMock(return_value=chunk))
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "alice")
    from src.lib.packages.package_runner_entrypoint import _build_tool_context
    context = {"document_id": "doc-a", "user_id": "alice"}
    search = documents.create_search_document_tool(context)
    read = documents.create_read_chunk_tool(context)
    record = documents.create_record_evidence_tool(context)
    assert "chunk_index" in read.params_json_schema["properties"]
    assert "chunk_id" not in read.params_json_schema["properties"]
    assert "selections" in record.params_json_schema["properties"]
    assert "span_ids" not in record.params_json_schema["properties"]
    found = await search.on_invoke_tool(_build_tool_context(search.name, {}), '{"query":"negative findings"}')
    assert found.hits[0].chunk_index == 42
    read_result = await read.on_invoke_tool(_build_tool_context(read.name, {}), '{"chunk_index":42}')
    assert read_result.chunk.chunk_index == 42
    assert read_result.chunk.previous_chunk_index == 41
    args = {"entity": "example", "selections": [{"chunk_index": 42, "span_index": 0}]}
    canonical = await record.on_invoke_tool(_build_tool_context(record.name, {}), json.dumps(args))
    assert canonical["chunk_id"] == chunk["id"]
    assert canonical["source_span_ids"] == [spans[0]["span_id"]]
    assert canonical["verified_quote"] == spans[0]["text"]
    evidence = build_record_evidence_summary_record(tool_name="record_evidence", tool_input=args, tool_output=canonical)
    assert evidence["chunk_id"] == chunk["id"]
    assert evidence["source_span_ids"] == [spans[0]["span_id"]]
    items = [
        {"type": "function_call", "name": "record_evidence", "call_id": "one", "arguments": json.dumps(args)},
        {"type": "function_call_output", "call_id": "one", "output": str(canonical)},
    ]
    # Projection remains active with optional context/history machinery disabled.
    run_config = run_config_with_history(RunConfig(), None)
    projected = await run_config.call_model_input_filter(SimpleNamespace(model_data=ModelInputData(input=items, instructions="Keep exact source.")))
    model_result = json.loads(projected.input[1]["output"])
    # The model sees the new record once: identity, location and the exact quote.
    assert model_result == {
        "status": "verified", "evidence_record_id": canonical["evidence_record_id"],
        "chunk_index": 42, "page": 3, "section": "Results",
        "verified_quote": canonical["verified_quote"],
    }
    assert chunk["id"] not in projected.input[1]["output"]
    assert chunk["id"] in items[1]["output"]  # SDK/runtime/UI original is untouched.
    assert "source_fragments" in items[1]["output"]


def _section_reading_setup(store, scope, monkeypatch, section_chunks, readable=None):
    """Real source scope, store and tools; the Weaviate fetches return ``section_chunks``.

    ``readable`` holds what each tool call's binding may read (one file by default).
    """
    from agr_ai_curation_alliance.tools import documents, weaviate_search
    from src.lib.openai_agents.tools import record_evidence

    by_index = {chunk["chunk_index"]: chunk for chunk in section_chunks}
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    current = {"readable": readable}
    monkeypatch.setattr(refs, "_load_source_binding",
                        lambda *args: binding(store, scope, readable=current["readable"]))
    monkeypatch.setattr(refs, "_verify_source_generation", lambda value: None)

    async def by_chunk_index(*, chunk_index, readable, **_kwargs):
        return by_index.get(chunk_index) if readable.contains(chunk_index) else None

    async def by_chunk_id(*, chunk_id, **_kwargs):
        return next((chunk for chunk in section_chunks if chunk["id"] == chunk_id), None)

    async def section_fetch(**_kwargs):
        return [dict(chunk) for chunk in section_chunks]

    monkeypatch.setattr(refs, "get_chunk_by_index", by_chunk_index)
    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", by_chunk_index)
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", AsyncMock(return_value={}))
    monkeypatch.setattr(weaviate_search, "get_chunks_by_parent_section", section_fetch)
    monkeypatch.setattr(weaviate_search, "get_chunks_by_subsection", section_fetch)
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", by_chunk_id)
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "alice")
    context = {"document_id": "doc-a", "user_id": "alice"}
    return {
        "read_section": documents.create_read_section_tool(context),
        "read_subsection": documents.create_read_subsection_tool(context),
        "read_chunk": documents.create_read_chunk_tool(context),
        "record_evidence": documents.create_record_evidence_tool(context),
        "readable": current,
    }


def _section_chunk(index, text, *, subsection="Expression"):
    from uuid import UUID
    return {"id": str(UUID(int=index + 1)), "chunk_index": index, "text": text, "document_id": "doc-a",
            "page_number": 3, "section_title": "Results", "parent_section": "Results",
            "subsection": subsection, "metadata": {"page_number": 3, "section_title": "Results"}}


async def _invoke(tool, arguments):
    from src.lib.packages.package_runner_entrypoint import _build_tool_context
    return await tool.on_invoke_tool(_build_tool_context(tool.name, arguments), json.dumps(arguments))


@pytest.mark.asyncio
async def test_section_read_issues_its_spans_for_record_evidence_without_read_chunk(store, scope, monkeypatch):
    chunks = [
        _section_chunk(10, "Wingless was absent from the wing disc. Hedgehog was unchanged."),
        _section_chunk(11, "Third sentence of the section."),
    ]
    outside = _section_chunk(12, "A chunk the section read never showed.")
    tools = _section_reading_setup(store, scope, monkeypatch, chunks + [outside])
    monkeypatch.setattr("agr_ai_curation_alliance.tools.weaviate_search.get_chunks_by_parent_section",
                        AsyncMock(return_value=[dict(chunk) for chunk in chunks]))

    section = await _invoke(tools["read_section"], {"section_name": "Results"})
    assert [p.chunk_index for p in section.section.passages] == [10, 11]

    args = {"entity": "wg", "selections": [{"chunk_index": 10, "span_index": 1}]}
    accepted = await _invoke(tools["record_evidence"], args)
    assert accepted["status"] == "verified"
    assert accepted["verified_quote"] == "Hedgehog was unchanged."

    never_shown = {"entity": "wg", "selections": [{"chunk_index": 12, "span_index": 0}]}
    refused = await _invoke(tools["record_evidence"], never_shown)
    assert refused["status"] == "needs_source_read"
    assert refused["error_code"] == "unissued_document_selection"
    assert refused["saved"] is False

    # A sentence number the passage never had is refused too.
    with pytest.raises(refs.DocumentReferenceError, match="unavailable"):
        await binding(store, scope).resolve([DocumentSelection(chunk_index=11, span_index=5)])


@pytest.mark.asyncio
async def test_table_rows_read_by_section_are_recorded_one_row_at_a_time(store, scope, monkeypatch):
    """P4: a whole-row table piece is read as one span per row; the column label
    above the rows has no number, so it can never be selected as evidence."""
    from uuid import UUID
    from src.lib.pipeline.chunk import chunk_parsed_document
    from src.models.strategy import ChunkingStrategy

    names = "| Strain | Genotype | Temp |"
    rows = [f"| XS{i:04d} | zeta{i} made-up. | 25 C |" for i in range(1, 121)]
    table = "\n".join([names, "| --- | --- | --- |", *rows])
    pieces = await chunk_parsed_document(
        [{"type": "Table", "text": table, "metadata": {"section_title": "Results"}}],
        ChunkingStrategy.get_research_strategy(), "doc-a",
    )
    chunks = [
        {**_section_chunk(piece.chunk_index, piece.content),
         "id": str(UUID(int=piece.chunk_index + 1)),
         "metadata": json.loads(json.dumps(piece.metadata.model_dump(), default=str))}
        for piece in pieces
    ]
    assert len(chunks) > 2
    tools = _section_reading_setup(store, scope, monkeypatch, chunks)

    section = await _invoke(tools["read_section"], {"section_name": "Results"})
    later = section.section.passages[2]
    row = later.evidence_spans[3]
    assert row.span_type == "table_row"

    accepted = await _invoke(tools["record_evidence"], {
        "entity": "zeta", "selections": [{"chunk_index": later.chunk_index, "span_index": row.span_index}],
    })
    assert accepted["status"] == "verified"
    assert accepted["verified_quote"] == row.text
    assert accepted["verified_quote"] in rows
    assert names not in accepted["verified_quote"]
    assert accepted["source_fragments"][0]["span_type"] == "table_row"

    # The label is not a span: the next free number after the rows is refused.
    with pytest.raises(refs.DocumentReferenceError, match="unavailable"):
        await binding(store, scope).resolve([
            DocumentSelection(chunk_index=later.chunk_index, span_index=len(later.evidence_spans)),
        ])


@pytest.mark.asyncio
async def test_text_contains_read_issues_only_the_matching_chunks(store, scope, monkeypatch):
    chunks = [
        _section_chunk(20, "Stock A was obtained from Bloomington."),
        _section_chunk(21, "Embryos were fixed and stained."),
    ]
    tools = _section_reading_setup(store, scope, monkeypatch, chunks)

    result = await _invoke(tools["read_section"], {"section_name": "Results", "text_contains": "bloomington"})
    assert (result.section.matched_chunk_count, result.section.total_chunk_count) == (1, 2)
    assert [p.chunk_index for p in result.section.passages] == [20]

    accepted = await _invoke(tools["record_evidence"], {"entity": "A", "selections": [{"chunk_index": 20, "span_index": 0}]})
    assert accepted["status"] == "verified"
    refused = await _invoke(tools["record_evidence"], {"entity": "A", "selections": [{"chunk_index": 21, "span_index": 0}]})
    assert refused["error_code"] == "unissued_document_selection"


@pytest.mark.asyncio
async def test_section_read_issues_every_chunk_in_exactly_one_retain_transaction(store, scope, monkeypatch):
    chunks = [_section_chunk(index, f"Passage {index} reports one finding. And a second.") for index in range(40)]
    tools = _section_reading_setup(store, scope, monkeypatch, chunks)
    retains, scans = [], []
    original_retain, original_scan = refs.SourceBinding.retain, refs._read_bindings

    def counting_retain(self, reads):
        retains.append(len(reads))
        return original_retain(self, reads)

    def counting_scan(*args, **kwargs):
        scans.append(kwargs.get("db") is not None)
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(refs.SourceBinding, "retain", counting_retain)
    monkeypatch.setattr(refs, "_read_bindings", counting_scan)

    await _invoke(tools["read_section"], {"section_name": "Results"})

    assert retains == [40]
    # One locked transaction re-reads the bindings once for the whole read.
    assert scans.count(True) == 1
    records = original_scan(store, scope, "doc-a")
    assert sorted(r["chunk_index"] for r in records if r["kind"] == refs._SPAN_KIND) == list(range(40))


def test_one_read_with_two_definitions_for_one_chunk_is_refused(store, scope):
    first, first_spans = source(7, "One text.")
    second, second_spans = source(7, "Another text.")
    with pytest.raises(refs.DocumentReferenceError, match="two definitions"):
        binding(store, scope).retain([(first, first_spans, [0]), (second, second_spans, [0])])
    assert [r for r in refs._read_bindings(store, scope, "doc-a") if r["kind"] != refs._SOURCE_KIND] == []


@pytest.mark.asyncio
async def test_span_numbers_mean_the_same_from_all_three_reading_tools(store, scope, monkeypatch):
    from src.lib.openai_agents.document_result_view import document_result_view

    text = "First finding was negative. Second finding held.\n\nThird is a caption line. Fourth closes it."
    chunk = _section_chunk(30, text)
    tools = _section_reading_setup(store, scope, monkeypatch, [chunk])

    section = await _invoke(tools["read_section"], {"section_name": "Results"})
    subsection = await _invoke(tools["read_subsection"], {"parent_section": "Results", "subsection": "Expression"})
    whole = await _invoke(tools["read_chunk"], {"chunk_index": 30})
    suffix = await _invoke(tools["read_chunk"], {"chunk_index": 30, "span_offset": 2})

    def identity(spans):
        return [(span.span_index, span.span_id, span.text) for span in spans]

    expected = identity(whole.chunk.evidence_spans)
    assert len(expected) == 4
    assert identity(section.section.passages[0].evidence_spans) == expected
    assert identity(subsection.subsection.passages[0].evidence_spans) == expected
    assert identity(suffix.chunk.evidence_spans) == expected[2:]

    lines = [f"[{index}] {span_text}" for index, _, span_text in expected]
    assert document_result_view("read_section", section)[0]["section"]["passages"][0]["text"] == "\n".join(lines)
    assert document_result_view("read_subsection", subsection)[0]["subsection"]["passages"][0]["text"] == "\n".join(lines)
    assert document_result_view("read_chunk", whole)[0]["chunk"]["text"] == "\n".join(lines)
    assert document_result_view("read_chunk", suffix)[0]["chunk"]["text"] == "\n".join(lines[2:])

    # The four reads share one definition of the chunk and never conflict.
    records = refs._read_bindings(store, scope, "doc-a")
    assert len([r for r in records if r["kind"] == refs._SPAN_KIND]) == 1


@pytest.mark.asyncio
async def test_source_scope_requires_authenticated_owner_before_database(monkeypatch):
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "bob")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    from agr_ai_curation_runtime.document_errors import DocumentReadError
    with pytest.raises(DocumentReadError) as caught:
        async with refs.document_reference_scope("doc-a", "alice"):
            pytest.fail("Foreign owner was admitted")
    assert isinstance(caught.value.__cause__, refs.DocumentReferenceError)


def test_paging_retains_one_definition_and_only_new_coverage(store, scope):
    chunk, spans = source(text="First.\n\nSecond.\n\nThird.")
    value = binding(store, scope)
    for index in range(len(spans)):
        value.retain([(chunk, spans, [index])])
        value.retain([(chunk, spans, [index])])
    records = refs._read_bindings(store, scope, "doc-a")
    assert len([r for r in records if r["kind"] == refs._SPAN_KIND]) == 1
    windows = [r for r in records if r["kind"] == refs._WINDOW_KIND]
    assert sum(len(r["issued_indexes"]) for r in windows) == len(spans)


@pytest.mark.asyncio
async def test_multiple_chunks_preserve_selection_order_and_deduplicate(store, scope, monkeypatch):
    first, first_spans = source(42, "First β😀.\n\nSecond sentence.")
    second, second_spans = source(99, "Third sentence.")
    second["id"] = "99220bef-62f7-0440-12bb-1d34e1f98ce0"
    second_spans = [s.to_dict() for s in build_evidence_spans(chunk_id=second["id"], chunk_text=second["text"])]
    value = binding(store, scope)
    value.retain([(first, first_spans, [0, 1])])
    value.retain([(second, second_spans, [0])])
    async def lookup(**kwargs):
        return {42: first, 99: second}[kwargs["chunk_index"]]
    monkeypatch.setattr(refs, "get_chunk_by_index", lookup)
    indexes = [(99, 0), (42, 1), (99, 0), (42, 0)]
    assert await value.resolve([DocumentSelection(chunk_index=c, span_index=s) for c, s in indexes]) == [
        second_spans[0]["span_id"], first_spans[1]["span_id"], first_spans[0]["span_id"]]


@pytest.mark.asyncio
async def test_source_guard_reprocessing_during_tool_is_terminal_and_resets_context(store, scope, monkeypatch):
    from agr_ai_curation_runtime.document_errors import DocumentReadError
    value = binding(store, scope)
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: value)
    def changed(_binding):
        raise refs.DocumentReferenceError("Document changed while reading")
    monkeypatch.setattr(refs, "_verify_source_generation", changed)
    with pytest.raises(DocumentReadError) as caught:
        async with refs.document_reference_scope("doc-a", "alice"):
            assert refs._ACTIVE_SOURCE.get() is value
    assert isinstance(caught.value.__cause__, refs.DocumentReferenceError)
    assert refs._ACTIVE_SOURCE.get() is None


@pytest.mark.asyncio
async def test_cancelled_source_guard_drains_owned_worker_before_return(store, scope, monkeypatch):
    import asyncio
    import threading
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    value = binding(store, scope)
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    def load(*args):
        entered.set()
        assert release.wait(5)
        finished.set()
        return value
    monkeypatch.setattr(refs, "_load_source_binding", load)
    async def run():
        async with refs.document_reference_scope("doc-a", "alice"):
            pytest.fail("Cancelled read entered tool body")
    task = asyncio.create_task(run())
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert refs._ACTIVE_SOURCE.get() is None


@pytest.mark.asyncio
async def test_history_arguments_project_structured_ids_without_rewriting_quote(monkeypatch):
    import json
    chunk, spans = source()
    chunk["document_id"] = "doc-a"
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_chunk_by_id", AsyncMock(return_value=chunk))
    original = {"event": {"tool_name": "document.record_evidence", "kind": "tool_start"},
        "payload": {"arguments": json.dumps({"span_ids": [spans[0]["span_id"]], "entity": chunk["id"]})}}
    projected = await refs.project_history_source_result(original, user_id="alice")
    args = projected["payload"]["arguments"]
    assert args["selections"] == [{"chunk_index": 42, "span_index": 0}]
    assert "span_ids" not in args
    assert args["entity"] == chunk["id"]  # Never rewrite scientific text.
    assert isinstance(original["payload"]["arguments"], str)


@pytest.mark.parametrize("tool_name,arguments", [
    ("read_chunk", {"chunk_id": "89220bef-62f7-0440-12bb-1d34e1f98ce"}),
    ("read_chunk", {"chunk_index": True}),
    ("read_chunk", {"chunk_index": "42"}),
    ("read_chunk", {"chunk_index": -1}),
])
@pytest.mark.asyncio
async def test_public_sdk_rejects_old_or_invalid_references_before_source_lookup(
    store, scope, monkeypatch, tool_name, arguments,
):
    import json
    from agr_ai_curation_alliance.tools import documents, weaviate_search
    from agr_ai_curation_runtime.document_errors import DocumentReadError
    from src.lib.packages.package_runner_entrypoint import _build_tool_context
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: binding(store, scope))
    lookup = AsyncMock()
    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", lookup)
    tool = getattr(documents, "create_" + tool_name + "_tool")({"document_id": "doc-a", "user_id": "alice"})
    with pytest.raises(DocumentReadError):
        await tool.on_invoke_tool(_build_tool_context(tool.name, {}), json.dumps(arguments))
    lookup.assert_not_awaited()


def test_record_evidence_schema_is_non_strict_with_only_entity_and_selections_required():
    from agr_ai_curation_alliance.tools import documents
    tool = documents.create_record_evidence_tool({"document_id": "doc-a", "user_id": "alice"})
    schema = tool.params_json_schema
    assert tool.strict_json_schema is False
    assert sorted(schema["required"]) == ["entity", "selections"]
    assert schema["additionalProperties"] is False
    # The optional targets stay available; the model omits them instead of writing nulls.
    assert {"evidence_record_id", "object_id", "pending_ref_id", "field_path",
            "object_type", "validation_finding_id"} <= set(schema["properties"])


@pytest.mark.parametrize("raw_arguments,field", [
    (json.dumps({"entity": "example", "span_ids": ["old-id"]}), "span_ids"),
    (json.dumps({"entity": "example", "selections": [{"chunk_index": 42, "span_index": False}]}),
     "selections.0.span_index"),
    (json.dumps({"selections": [{"chunk_index": 42, "span_index": 0}]}), "entity"),
    ('{"entity": "example", "selections": [', None),
    ("[]", None),
])
@pytest.mark.asyncio
async def test_record_evidence_returns_malformed_arguments_to_the_model_without_saving(
    store, scope, monkeypatch, raw_arguments, field,
):
    from agr_ai_curation_alliance.tools import documents
    from src.lib.observability.tool_results import classify_tool_result
    from src.lib.openai_agents.tools import record_evidence
    value = binding(store, scope)
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: value)
    monkeypatch.setattr(refs, "_verify_source_generation", lambda value: None)
    selection_lookup, canonical_lookup = AsyncMock(), AsyncMock()
    monkeypatch.setattr(refs, "get_chunk_by_index", selection_lookup)
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", canonical_lookup)
    tool = documents.create_record_evidence_tool({"document_id": "doc-a", "user_id": "alice"})
    output = await tool.on_invoke_tool(tool_context(tool.name, raw_arguments), raw_arguments)
    result = json.loads(output)
    assert result["status"] == "invalid_arguments"
    assert result["saved"] is False
    assert "record_evidence again" in result["message"]
    if field is not None:
        assert field in [problem["field"] for problem in result["argument_errors"]]
    assert "old-id" not in output  # Problems name fields; they do not echo inputs.
    assert classify_tool_result(output).success is False
    selection_lookup.assert_not_awaited()
    canonical_lookup.assert_not_awaited()
    assert value.staged_updates == []
    assert refs._ACTIVE_SOURCE.get() is None
    # The model view carries the correction request through unchanged.
    items = [{"type": "function_call", "name": "record_evidence", "call_id": "bad", "arguments": raw_arguments},
             {"type": "function_call_output", "call_id": "bad", "output": output}]
    projected = await refs.project_model_source_items(items, user_id="alice", cache={})
    assert json.loads(projected[1]["output"]) == result


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.asyncio
async def test_existing_evidence_update_waits_for_final_source_generation_check(store, scope, monkeypatch, changed):
    from agr_ai_curation_runtime.document_errors import DocumentReadError
    value = binding(store, scope)
    existing = {"verified_quote": "Original source"}
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: value)
    def verify(_binding):
        if changed:
            raise refs.DocumentReferenceError("Source changed")
    monkeypatch.setattr(refs, "_verify_source_generation", verify)
    async def update():
        async with refs.document_reference_scope("doc-a", "alice"):
            refs.stage_evidence_update(existing, {"verified_quote": "Replacement source"})
            assert existing["verified_quote"] == "Original source"
    if changed:
        with pytest.raises(DocumentReadError):
            await update()
        assert existing["verified_quote"] == "Original source"
    else:
        await update()
        assert existing["verified_quote"] == "Replacement source"


@pytest.mark.asyncio
async def test_old_document_history_refreshes_only_backend_guidance(monkeypatch):
    chunk, spans = source()
    chunk["document_id"] = "doc-a"
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_chunk_by_id", AsyncMock(return_value=chunk))
    original = {"event": {"tool_name": "read_chunk", "kind": "input_item"}, "payload": {
        "type": "function_call_output", "call_id": "old", "output": json.dumps({
            "summary": "Select evidence_spans[].span_id.", "chunk": {"chunk_id": chunk["id"],
                "content": "Scientific span_id text is unchanged."}})}}
    projected = await refs.project_history_source_result(original, user_id="alice")
    payload = json.loads(projected["payload"]["output"])
    assert "span_id" not in payload["summary"]
    assert "span_index" in payload["summary"]
    assert payload["chunk"]["content"] == "Scientific span_id text is unchanged."
    assert "Select evidence_spans[].span_id." in original["payload"]["output"]


@pytest.mark.parametrize("extra", [
    {"objectId": "AGR:1", "field_path": "label"},
    {"evidenceRecordId": "existing"},
    {"evidence_record": "existing"},
    {"span_ids": ["old-id"]},
])
@pytest.mark.asyncio
async def test_record_evidence_rejects_undeclared_arguments_instead_of_ignoring_them(
    store, scope, monkeypatch, extra,
):
    from src.lib.observability.tool_results import classify_tool_result
    from src.lib.openai_agents.tools import record_evidence
    from src.lib.packages.package_runner_entrypoint import _build_tool_context
    chunk, spans = source()
    value = binding(store, scope)
    value.retain([(chunk, spans, [0])])
    loads = []
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: loads.append(args) or value)
    canonical_lookup = AsyncMock(return_value=chunk)
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", canonical_lookup)
    existing = {"evidence_record_id": "existing", "document_id": "doc-a", "status": "verified",
                "verified_quote": "Original quote", "source_span_ids": ["original"]}
    workspace = [existing]
    tool = record_evidence.create_record_evidence_tool("doc-a", "alice", workspace_records=workspace)
    args = {"entity": "example", "selections": [{"chunk_index": 42, "span_index": 0}], **extra}
    output = await tool.on_invoke_tool(_build_tool_context(tool.name, args), json.dumps(args))
    result = json.loads(output)
    assert result["status"] == "invalid_arguments" and result["saved"] is False
    assert [problem["field"] for problem in result["argument_errors"]] == sorted(
        key for key in extra if key != "field_path")
    assert "AGR:1" not in output and "old-id" not in output
    assert classify_tool_result(output).success is False
    assert loads == []  # Nothing ran, not even the source binding.
    canonical_lookup.assert_not_awaited()
    assert workspace == [existing] and existing["verified_quote"] == "Original quote"
    assert value.staged_updates == []


def _verified_record_result(chunk, spans):
    span = spans[0]
    fragment = {"span_id": span["span_id"], "chunk_id": chunk["id"], "document_id": "doc-a",
                "text": span["text"], "char_start": span["char_start"], "char_end": span["char_end"],
                "span_index": 0, "span_type": span["span_type"], "spanizer_version": span["spanizer_version"],
                "page": 3, "section": "Results"}
    return {"status": "verified", "entity": "example", "span_ids": [span["span_id"]],
            "source_span_ids": [span["span_id"]], "verified_quote": span["text"], "document_id": "doc-a",
            "chunk_id": chunk["id"], "chunk_ids": [chunk["id"]], "source_fragments": [fragment],
            "envelope_target": {"object_id": "candidate-1", "field_path": "label"},
            "page": 3, "section": "Results", "subsection": "Expression", "evidence_record_id": "evidence-0123456789abcdef",
            "selections": [{"chunk_index": 42, "span_index": 0}]}


@pytest.mark.asyncio
async def test_model_view_shows_a_verified_record_once_while_history_keeps_the_full_result(monkeypatch):
    chunk, spans = source()
    chunk["document_id"] = "doc-a"
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_chunk_by_id", AsyncMock(return_value=chunk))
    canonical = _verified_record_result(chunk, spans)
    output = str(canonical)  # The SDK sends Python dictionary results as repr.
    items = [{"type": "function_call", "name": "record_evidence", "call_id": "one", "arguments": "{}"},
             {"type": "function_call_output", "call_id": "one", "output": output}]
    projected = await refs.project_model_source_items(items, user_id="alice", cache={})
    assert json.loads(projected[1]["output"]) == {
        "status": "verified", "evidence_record_id": "evidence-0123456789abcdef", "chunk_index": 42,
        "page": 3, "section": "Results", "subsection": "Expression", "verified_quote": spans[0]["text"]}
    assert projected[1]["output"].count(spans[0]["text"]) == 1
    assert items[1]["output"] == output  # The SDK, evidence and UI keep the original.

    history = await refs.project_history_source_result({
        "event": {"tool_name": "record_evidence", "kind": "input_item"},
        "payload": {"type": "function_call_output", "call_id": "one", "output": output}}, user_id="alice")
    archived = json.loads(history["payload"]["output"])
    assert archived["entity"] == "example"
    assert archived["source_fragments"][0]["text"] == spans[0]["text"]
    assert archived["envelope_target"] == canonical["envelope_target"]
    assert archived["selections"] == [{"chunk_index": 42, "span_index": 0}]


@pytest.mark.asyncio
async def test_model_view_of_unverified_record_results_is_unchanged(monkeypatch):
    rejected = {"status": "needs_source_read", "error_code": "unissued_document_selection", "saved": False,
                "selection": {"chunk_index": 42, "span_index": 0}, "message": "Nothing was saved."}
    items = [{"type": "function_call", "name": "record_evidence", "call_id": "one", "arguments": "{}"},
             {"type": "function_call_output", "call_id": "one", "output": str(rejected)}]
    projected = await refs.project_model_source_items(items, user_id="alice", cache={})
    assert json.loads(projected[1]["output"]) == rejected


@pytest.mark.asyncio
async def test_model_view_of_evidence_list_drops_repeated_document_and_empty_targets(monkeypatch):
    chunk, _ = source()
    chunk["document_id"] = "doc-a"
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_chunk_by_id", AsyncMock(return_value=chunk))
    attached = [{"object_id": "candidate-1", "field_path": "label"}]
    listed = {"status": "ok", "document_id": "doc-a", "count": 2, "evidence_records": [
        {"evidence_record_id": "evidence-1", "status": "active", "entity": "first", "page": 3,
         "section": "Results", "chunk_id": chunk["id"], "document_id": "doc-a", "source_span_count": 1,
         "envelope_targets": [], "verified_quote_chars": 40},
        {"evidence_record_id": "evidence-2", "status": "active", "entity": "second", "page": 3,
         "section": "Results", "chunk_id": chunk["id"], "document_id": "doc-a", "source_span_count": 1,
         "envelope_targets": attached, "verified_quote_chars": 12}]}
    items = [{"type": "function_call", "name": "list_recorded_evidence", "call_id": "list", "arguments": "{}"},
             {"type": "function_call_output", "call_id": "list", "output": json.dumps(listed)}]
    projected = json.loads((await refs.project_model_source_items(items, user_id="alice", cache={}))[1]["output"])
    assert projected["document_id"] == "doc-a"
    first, second = projected["evidence_records"]
    assert first == {"evidence_record_id": "evidence-1", "status": "active", "entity": "first", "page": 3,
                     "section": "Results", "chunk_index": 42, "source_span_count": 1, "verified_quote_chars": 40}
    assert second["envelope_targets"] == attached
    assert "document_id" not in second
    assert json.loads(items[1]["output"]) == listed


@pytest.mark.asyncio
async def test_workspace_failures_keep_their_actual_cause_and_classification():
    from src.lib.openai_agents.tools.evidence_workspace import create_get_recorded_evidence_tool
    tool = create_get_recorded_evidence_tool("doc-a", "alice")
    with pytest.raises(RuntimeError, match="workspace"):
        await tool.on_invoke_tool(tool_context("get_recorded_evidence", tool_call_id="read", run_config=None),
            json.dumps({"evidence_record_id": "missing"}))
    # Other workspace SDK tools retain their existing recoverable error contract.
    items = [{"type": "function_call", "name": "attach_evidence_to_object", "call_id": "attach", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "attach", "output": "An error occurred while running the tool: missing workspace"}]
    assert await refs.project_model_source_items(items, user_id="alice", cache={}) == items


@pytest.mark.asyncio
@pytest.mark.parametrize("unissued", ["chunk", "span_window"])
@pytest.mark.parametrize("selected_chunk", [31, 36])
async def test_unissued_selection_requires_explicit_read_then_resubmission(store, scope, monkeypatch, unissued, selected_chunk):
    from agr_ai_curation_alliance.tools import documents, weaviate_search
    from src.lib.openai_agents.tools import record_evidence
    from src.lib.openai_agents.evidence_summary import build_record_evidence_summary_record
    from src.lib.packages.package_runner_entrypoint import _build_tool_context

    chunk, spans = source(selected_chunk, "First exact sentence.\n\nSecond α😀 sentence.")
    chunk.update(document_id="doc-a", page_number=3, section_title="Results", metadata={"page_number": 3, "section_title": "Results"})
    selected_span = 1 if unissued == "span_window" else 0
    if unissued == "span_window":
        binding(store, scope).retain([(chunk, spans, [0])])
    else:
        from uuid import UUID
        neighbor, _ = source(selected_chunk - 1, "Neighbor read does not issue the selected chunk.")
        neighbor["id"] = str(UUID(int=selected_chunk))
        neighbor_spans = [s.to_dict() for s in build_evidence_spans(chunk_id=neighbor["id"], chunk_text=neighbor["text"])]
        binding(store, scope).retain([(neighbor, neighbor_spans, [0])])
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: binding(store, scope))
    verifications = []
    monkeypatch.setattr(refs, "_verify_source_generation", lambda value: verifications.append(value.generation))
    lookup = AsyncMock(return_value=chunk)
    monkeypatch.setattr(refs, "get_chunk_by_index", lookup)
    monkeypatch.setattr(weaviate_search, "get_chunk_by_index", AsyncMock(return_value=chunk))
    monkeypatch.setattr(weaviate_search, "get_chunk_neighbor_ids", AsyncMock(return_value={}))
    canonical_lookup = AsyncMock(return_value=chunk)
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", canonical_lookup)
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "alice")
    context = {"document_id": "doc-a", "user_id": "alice"}
    record = documents.create_record_evidence_tool(context)
    read = documents.create_read_chunk_tool(context)
    args = {"entity": "example", "selections": [{"chunk_index": selected_chunk, "span_index": selected_span}]}
    result = await record.on_invoke_tool(_build_tool_context(record.name, args), json.dumps(args))
    assert result["status"] == "needs_source_read"
    assert result["error_code"] == "unissued_document_selection"
    assert result["saved"] is False
    assert result["selection"] == args["selections"][0]
    assert "read_chunk" in result["message"]
    assert "evidence_record_id" not in result
    assert build_record_evidence_summary_record(tool_name="record_evidence", tool_input=args, tool_output=result) is None
    lookup.assert_not_awaited()
    canonical_lookup.assert_not_awaited()
    assert len(verifications) == 1
    assert refs._ACTIVE_SOURCE.get() is None

    # The agent must issue the read and submit the selection again itself.
    await read.on_invoke_tool(_build_tool_context(read.name, {}), json.dumps({"chunk_index": selected_chunk}))
    accepted = await record.on_invoke_tool(_build_tool_context(record.name, args), json.dumps(args))
    assert accepted["status"] == "verified"
    assert accepted["verified_quote"] == spans[selected_span]["text"]
    assert accepted["source_span_ids"] == [spans[selected_span]["span_id"]]
    assert accepted["selections"] == args["selections"]
    assert len(verifications) == 3


@pytest.mark.asyncio
async def test_mixed_issued_and_unissued_selections_cannot_partially_update_evidence(store, scope, monkeypatch):
    from copy import deepcopy
    from src.lib.openai_agents.tools import record_evidence
    from src.lib.packages.package_runner_entrypoint import _build_tool_context
    chunk, spans = source()
    value = binding(store, scope)
    value.retain([(chunk, spans, [0])])
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: value)
    monkeypatch.setattr(refs, "_verify_source_generation", lambda value: None)
    monkeypatch.setattr(refs, "get_chunk_by_index", AsyncMock(return_value=chunk))
    canonical_lookup = AsyncMock(return_value=chunk)
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", canonical_lookup)
    existing = {"evidence_record_id": "existing", "document_id": "doc-a", "status": "verified",
                "verified_quote": "Original quote", "source_span_ids": ["original"],
                "object_id": "original-target", "field_path": "original-field"}
    before = deepcopy(existing)
    tool = record_evidence.create_record_evidence_tool("doc-a", "alice", workspace_records=[existing])
    args = {"entity": "example", "evidence_record_id": "existing", "selections": [
        {"chunk_index": 42, "span_index": 0}, {"chunk_index": 43, "span_index": 0}]}
    result = await tool.on_invoke_tool(_build_tool_context(tool.name, args), json.dumps(args))
    assert result["status"] == "needs_source_read"
    assert result["selection"] == args["selections"][1]
    assert existing == before
    assert value.staged_updates == []
    canonical_lookup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["history", "missing_chunk", "changed_chunk", "lookup", "corrupt_span", "invalid_span", "generation_after_rejection"])
async def test_record_evidence_source_faults_remain_terminal(store, scope, monkeypatch, fault):
    from agr_ai_curation_runtime.document_errors import DocumentReadError
    from src.lib.openai_agents.tools import record_evidence
    from src.lib.packages.package_runner_entrypoint import _build_tool_context
    chunk, spans = source()
    value = binding(store, scope)
    value.retain([(chunk, spans, [0])])
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: value)
    monkeypatch.setattr(refs, "_verify_source_generation", lambda value: None)
    monkeypatch.setattr(refs, "get_chunk_by_index", AsyncMock(return_value=chunk))
    canonical_lookup = AsyncMock()
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", canonical_lookup)
    def fail(*args, **kwargs):
        raise RuntimeError("Private source dependency failed")
    if fault == "history":
        monkeypatch.setattr(refs, "_read_bindings", fail)
    elif fault == "missing_chunk":
        monkeypatch.setattr(refs, "get_chunk_by_index", AsyncMock(return_value=None))
    elif fault == "changed_chunk":
        monkeypatch.setattr(refs, "get_chunk_by_index", AsyncMock(return_value={**chunk, "text": "Changed"}))
    elif fault == "lookup":
        monkeypatch.setattr(refs, "get_chunk_by_index", AsyncMock(side_effect=RuntimeError("Private lookup failed")))
    elif fault == "corrupt_span":
        monkeypatch.setattr(refs, "resolve_evidence_span_id", fail)
    elif fault == "generation_after_rejection":
        monkeypatch.setattr(refs, "_verify_source_generation", fail)
    tool = record_evidence.create_record_evidence_tool("doc-a", "alice")
    args = {"entity": "example", "selections": [{"chunk_index": 43 if fault == "generation_after_rejection" else 42, "span_index": 99 if fault == "invalid_span" else 0}]}
    with pytest.raises(DocumentReadError) as caught:
        await tool.on_invoke_tool(_build_tool_context(tool.name, args), json.dumps(args))
    assert caught.value.__cause__ is not None
    assert value.staged_updates == []
    assert refs._ACTIVE_SOURCE.get() is None
    canonical_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_record_calls_keep_read_prerequisites_scoped(store, scope, monkeypatch):
    import asyncio
    from src.lib.openai_agents.tools import record_evidence
    from src.lib.packages.package_runner_entrypoint import _build_tool_context
    chunk, spans = source()
    chunk.update(document_id="doc-a", page_number=3, section_title="Results", metadata={"page_number": 3, "section_title": "Results"})
    binding(store, scope).retain([(chunk, spans, [0])])
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: binding(store, scope))
    monkeypatch.setattr(refs, "_verify_source_generation", lambda value: None)
    monkeypatch.setattr(refs, "get_chunk_by_index", AsyncMock(return_value=chunk))
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", AsyncMock(return_value=chunk))
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "alice")
    tool = record_evidence.create_record_evidence_tool("doc-a", "alice")
    async def record(index):
        args = {"entity": "example", "selections": [{"chunk_index": index, "span_index": 0}]}
        return await tool.on_invoke_tool(_build_tool_context(tool.name, args), json.dumps(args))
    accepted, rejected = await asyncio.gather(record(42), record(43))
    assert accepted["status"] == "verified"
    assert accepted["verified_quote"] == spans[0]["text"]
    assert rejected["status"] == "needs_source_read"
    assert rejected["saved"] is False
    assert "evidence_record_id" not in rejected
    assert refs._ACTIVE_SOURCE.get() is None


@pytest.mark.asyncio
async def test_cancelled_selection_verification_never_reaches_evidence_mutation(store, scope, monkeypatch):
    import asyncio
    from src.lib.openai_agents.tools import record_evidence
    from src.lib.packages.package_runner_entrypoint import _build_tool_context
    chunk, spans = source()
    value = binding(store, scope)
    value.retain([(chunk, spans, [0])])
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: value)
    monkeypatch.setattr(refs, "_verify_source_generation", lambda value: None)
    entered = asyncio.Event()
    async def wait_for_source(**kwargs):
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr(refs, "get_chunk_by_index", wait_for_source)
    canonical_lookup = AsyncMock()
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", canonical_lookup)
    tool = record_evidence.create_record_evidence_tool("doc-a", "alice")
    args = {"entity": "example", "selections": [{"chunk_index": 42, "span_index": 0}]}
    task = asyncio.create_task(tool.on_invoke_tool(_build_tool_context(tool.name, args), json.dumps(args)))
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert value.staged_updates == []
    canonical_lookup.assert_not_awaited()
    assert refs._ACTIVE_SOURCE.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source_failure", [False, True])
async def test_selection_result_through_flow_boundary_and_sentry(store, scope, monkeypatch, source_failure):
    """Real SDK tool errors reach the endpoint owner; read prerequisites do not."""
    from types import SimpleNamespace
    from uuid import uuid4
    import sentry_sdk
    from sentry_sdk import Client
    from sentry_sdk.transport import Transport
    from sentry_sdk.integrations.logging import LoggingIntegration
    from src.lib.observability.sentry import before_send
    from src.lib.openai_agents.tools import record_evidence
    from src.lib.packages.package_runner_entrypoint import _build_tool_context
    from src.lib.openai_agents.curator_errors import failure_for_event
    from tests.unit.api import test_chat_execute_flow_endpoint as boundary

    events = []
    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.headers.get("type") == "event":
                    events.append(item.payload.json)

    calls = boundary._patch_stream_dependencies(monkeypatch, cancel_requested=False)
    chunk, spans = source()
    value = binding(store, scope)
    if source_failure:
        value.retain([(chunk, spans, [0])])
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: value)
    monkeypatch.setattr(refs, "_verify_source_generation", lambda value: None)
    monkeypatch.setattr(refs, "get_chunk_by_index", AsyncMock(side_effect=RuntimeError("PRIVATE source outage")))
    canonical_lookup = AsyncMock()
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", canonical_lookup)
    tool = record_evidence.create_record_evidence_tool("doc-a", "alice")
    args = {"entity": "example", "selections": [{"chunk_index": 42, "span_index": 0}]}
    results = []
    async def execute(**kwargs):
        result = await tool.on_invoke_tool(_build_tool_context(tool.name, args), json.dumps(args))
        results.append(result)
        # No implicit read or retry: the test executor returns the tool guidance.
        yield {"type": "RUN_FINISHED", "data": {"response": result["message"]}}
    boundary._patch_chat_impl(monkeypatch, "execute_flow", execute)
    flow_id = uuid4()
    flow = SimpleNamespace(id=flow_id, user_id=7, is_active=True, visibility="private",
        project_id=None, shared_at=None, name="Evidence", execution_count=0,
        last_executed_at=None, flow_definition={})
    client = Client(dsn="http://public@localhost/1", transport=MemoryTransport,
        default_integrations=False, integrations=[LoggingIntegration(event_level=40)],
        before_send=before_send, send_default_pii=False)
    try:
        with sentry_sdk.isolation_scope() as sentry_scope:
            sentry_scope.set_client(client)
            response = await boundary.chat.execute_flow_endpoint(
                request=boundary.chat.ExecuteFlowRequest(flow_id=flow_id, session_id="evidence-boundary"),
                db=boundary._DummyDB(flow=flow), user={"sub": "auth-sub", "cognito:groups": []})
            streamed = await boundary._consume_stream(response)
            client.flush()
        if source_failure:
            assert results == []
            assert [event["type"] for event in streamed] == ["SUPERVISOR_ERROR", "RUN_ERROR"]
            assert streamed[1]["message"] == failure_for_event({"error_type": "DocumentReadError"}).message
            assert len(events) == 1
            assert events[0]["tags"]["runtime_component"] == "execute_flow_stream"
            assert events[0]["tags"]["operation"] == "event_generator_failed"
        else:
            assert results[0]["status"] == "needs_source_read"
            assert results[0]["saved"] is False
            assert not any(event["type"] in {"RUN_ERROR", "SUPERVISOR_ERROR"} for event in streamed)
            assert events == []
        canonical_lookup.assert_not_awaited()
        assert value.staged_updates == []
        assert calls["clear"] == ["evidence-boundary"]
    finally:
        client.close()
        boundary.chat._LOCAL_CANCEL_EVENTS.clear()
        boundary.chat._LOCAL_SESSION_OWNERS.clear()
        boundary.chat.executable_run_manager._runs.clear()
        boundary.chat.executable_run_manager._active_session_run_ids.clear()


THREE_FILES = files("doc-a", ("paper.pdf", 0, 11), ("Table S1.xlsx", 12, 13), ("Data S2.docx", 20, 21))


@pytest.mark.asyncio
async def test_read_chunk_outside_the_readable_files_gets_their_ranges_with_names(store, scope, monkeypatch):
    tools = _section_reading_setup(store, scope, monkeypatch, [_section_chunk(10, "Main text.")],
                                   readable=THREE_FILES)

    for index in (-1, 15, 22):
        result = await _invoke(tools["read_chunk"], {"chunk_index": index})
        assert result.chunk is None
        assert result.error_code == "invalid_result_cursor"
        assert result.summary == (
            f"Invalid read_chunk request: chunk_index {index} does not exist; this document has chunks "
            "0 to 11 (paper.pdf), 12 to 13 (Table S1.xlsx), 20 to 21 (Data S2.docx)."
        )


@pytest.mark.asyncio
async def test_read_chunk_past_the_end_of_a_one_file_paper_reads_as_before(store, scope, monkeypatch):
    tools = _section_reading_setup(store, scope, monkeypatch, [_section_chunk(10, "Main text.")],
                                   readable=single_file("doc-a", 12))

    result = await _invoke(tools["read_chunk"], {"chunk_index": 12})
    assert result.summary == (
        "Invalid read_chunk request: chunk_index 12 does not exist; this document has chunks 0 to 11."
    )


@pytest.mark.asyncio
async def test_a_missing_piece_inside_a_readable_file_still_fails_the_read(store, scope, monkeypatch):
    from agr_ai_curation_runtime.document_errors import DocumentReadError
    tools = _section_reading_setup(store, scope, monkeypatch, [_section_chunk(10, "Main text.")],
                                   readable=THREE_FILES)

    with pytest.raises(DocumentReadError):
        await _invoke(tools["read_chunk"], {"chunk_index": 12})


@pytest.mark.asyncio
async def test_evidence_from_a_file_that_stopped_being_readable_is_refused_to_the_model(store, scope, monkeypatch):
    sheet = _section_chunk(12, "Row one of the sheet.")
    tools = _section_reading_setup(store, scope, monkeypatch, [sheet], readable=THREE_FILES)
    read = await _invoke(tools["read_chunk"], {"chunk_index": 12})
    assert read.chunk.chunk_index == 12

    # The sheet is removed from the paper before the evidence is recorded.
    tools["readable"]["readable"] = files("doc-a", ("paper.pdf", 0, 11), ("Data S2.docx", 20, 21))
    refused = await _invoke(tools["record_evidence"], {
        "entity": "wg", "selections": [{"chunk_index": 12, "span_index": 0}]})

    assert refused == {
        "status": "needs_source_read",
        "error_code": "unreadable_document_selection",
        "saved": False,
        "selection": {"chunk_index": 12, "span_index": 0},
        "message": (
            "Nothing was saved. chunk_index 12 is not part of a file you can read now. "
            "This document has chunks 0 to 11 (paper.pdf), 20 to 21 (Data S2.docx). "
            "Read a passage from them and call record_evidence again."
        ),
    }


@pytest.mark.asyncio
async def test_a_selection_outside_the_readable_files_never_reaches_the_store(store, scope, monkeypatch):
    chunk, spans = source(12)
    # Read while the paper held a sheet at 12..13; the sheet has since been removed.
    value = binding(store, scope, readable=files("doc-a", ("paper.pdf", 0, 11), ("Data S2.docx", 20, 21)))
    value.retain([(chunk, spans, [0])])
    lookup = AsyncMock()
    monkeypatch.setattr(refs, "get_chunk_by_index", lookup)

    with pytest.raises(refs.UnreadableDocumentSelectionError) as caught:
        await value.resolve([DocumentSelection(chunk_index=12, span_index=0)])
    assert caught.value.selection.chunk_index == 12
    lookup.assert_not_called()


@pytest.mark.asyncio
async def test_a_span_id_on_an_unreadable_piece_is_a_source_failure(store, scope, monkeypatch):
    """The internal span-ID path checks the piece number of a piece loaded by id."""
    from src.lib.openai_agents.tools import record_evidence
    chunk, spans = source(12)
    chunk["document_id"] = "doc-a"
    value = binding(store, scope, readable=files("doc-a", ("paper.pdf", 0, 11)))
    monkeypatch.setattr(refs, "get_current_user_id", lambda: "alice")
    monkeypatch.setattr(refs, "get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr(refs, "get_current_session_id", lambda: "chat-a")
    monkeypatch.setattr(refs, "_load_source_binding", lambda *args: value)
    monkeypatch.setattr(refs, "_verify_source_generation", lambda value: None)
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", AsyncMock(return_value=chunk))
    recorder = record_evidence._create_canonical_evidence_recorder("doc-a", "alice")

    async with refs.document_reference_scope("doc-a", "alice"):
        result = await recorder(entity="wg", span_ids=[spans[0]["span_id"]])

    assert result["resolution_failure"] == "unreadable_chunk"
    assert result.get("status") != "verified"


@pytest.mark.asyncio
async def test_history_projection_marks_a_piece_of_an_unreadable_file_unavailable(monkeypatch, readable_papers):
    chunk, spans = source(12)
    chunk["document_id"] = "doc-a"
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_chunk_by_id", AsyncMock(return_value=chunk))
    readable_papers["doc-a"] = files("doc-a", ("paper.pdf", 0, 11))

    projected = await refs.project_source_references(
        {"chunk_id": chunk["id"], "span_ids": [spans[0]["span_id"]]}, user_id="alice", document_id="doc-a")

    assert projected["source_reference_status"] == "source_unavailable"
    assert projected["selections"] == []
    assert "chunk_index" not in projected

    readable_papers["doc-a"] = files("doc-a", ("paper.pdf", 0, 11), ("Table S1.xlsx", 12, 13))
    readable_now = await refs.project_source_references(
        {"chunk_id": chunk["id"], "span_ids": [spans[0]["span_id"]]}, user_id="alice", document_id="doc-a")
    assert readable_now["chunk_index"] == 12
    assert readable_now["selections"] == [{"chunk_index": 12, "span_index": 0}]


def test_the_active_tool_call_exposes_its_readable_files(store, scope):
    from agr_ai_curation_runtime.document_references import current_readable_files
    value = binding(store, scope, readable=THREE_FILES)

    with pytest.raises(refs.DocumentReferenceError, match="Missing source binding"):
        current_readable_files()
    token = refs._ACTIVE_SOURCE.set(value)
    try:
        assert current_readable_files() is THREE_FILES
    finally:
        refs._ACTIVE_SOURCE.reset(token)



@pytest.mark.asyncio
async def test_a_never_read_selection_on_a_one_file_paper_gets_todays_unissued_answer(store, scope, monkeypatch):
    """Past the end of a one-file paper (pieces 0..348) the answer is exactly today's."""
    tools = _section_reading_setup(store, scope, monkeypatch, [_section_chunk(10, "Main text.")],
                                   readable=single_file("doc-a", 349))

    refused = await _invoke(tools["record_evidence"], {
        "entity": "wg", "selections": [{"chunk_index": 349, "span_index": 0}]})

    assert refused == {
        "status": "needs_source_read",
        "error_code": "unissued_document_selection",
        "saved": False,
        "selection": {"chunk_index": 349, "span_index": 0},
        "message": (
            "Nothing was saved. Call read_chunk for the selected chunk_index "
            "and read the window containing the selected span_index. "
            "Then explicitly call record_evidence again with issued selections."
        ),
    }


@pytest.mark.asyncio
async def test_a_never_read_piece_of_a_removed_file_is_still_unissued(store, scope):
    value = binding(store, scope, readable=files("doc-a", ("paper.pdf", 0, 11), ("Data S2.docx", 20, 21)))

    with pytest.raises(refs.UnissuedDocumentSelectionError):
        await value.resolve([DocumentSelection(chunk_index=12, span_index=0)])


@pytest.mark.asyncio
async def test_history_projection_of_a_ready_file_without_a_range_is_a_named_read_failure(monkeypatch):
    """The fill step has not run: projection fails like the document tools, never unhandled."""
    from agr_ai_curation_runtime.document_errors import DocumentReadError
    from src.lib.document_files.service import FileRangeMissingError
    chunk, spans = source(12)
    chunk["document_id"] = "doc-a"
    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_chunk_by_id", AsyncMock(return_value=chunk))

    def unfilled(document_id, user_id):
        raise FileRangeMissingError("File has no recorded piece range")

    monkeypatch.setattr(refs, "resolve_readable_files", unfilled)
    with pytest.raises(DocumentReadError) as caught:
        await refs.project_source_references(
            {"chunk_id": chunk["id"], "span_ids": [spans[0]["span_id"]]}, user_id="alice", document_id="doc-a")
    assert isinstance(caught.value.__cause__, FileRangeMissingError)
