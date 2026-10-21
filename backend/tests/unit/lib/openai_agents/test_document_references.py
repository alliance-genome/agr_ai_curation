"""Local ordinals retain exact source meaning across restart, paging and scopes."""
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


@pytest.fixture(autouse=True)
def alliance_package_path(monkeypatch):
    # These tests exercise the isolated package even when collected on their own.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[5] /
        "packages/alliance/python/src"))


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


def binding(store, scope, *, doc="doc-a", generation="generation-a"):
    value = refs.SourceBinding(doc, scope.owner_id, generation, scope, store,
        refs._read_bindings(store, scope, doc))
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
    first.retain(chunk, spans, [0])
    restarted = binding(HistoryStore(store.session_factory), replace(scope, run_id="run-b", invocation_id="other", lineage="other/"))
    monkeypatch.setattr(refs, "get_chunk_by_index", AsyncMock(return_value=chunk))
    selections = [DocumentSelection(chunk_index=42, span_index=0)] * 2
    assert await restarted.resolve(selections) == [spans[0]["span_id"]]
    refs.get_chunk_by_index.assert_awaited_once_with(chunk_index=42, user_id="alice", document_id="doc-a")


@pytest.mark.parametrize("change", ["owner", "session", "document"])
@pytest.mark.asyncio
async def test_same_numbers_do_not_cross_owner_session_or_document(store, scope, change):
    chunk, spans = source()
    binding(store, scope).retain(chunk, spans, [0])
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
    original.retain(chunk, spans, [0])
    if change == "text":
        chunk = {**chunk, "text": chunk["text"] + " Changed."}
    if change == "uuid":
        chunk = {**chunk, "id": "another-source"}
    if change == "span":
        spans = [{**spans[0], "char_end": spans[0]["char_end"] + 1}]
    with pytest.raises(ValueError):
        binding(store, scope).retain(chunk, spans, [0])


@pytest.mark.asyncio
async def test_span_window_must_have_been_issued_and_current_source_verified(store, scope, monkeypatch):
    chunk, spans = source(text="First sentence.\n\nSecond sentence.")
    assert len(spans) > 1
    value = binding(store, scope)
    value.retain(chunk, spans, [0])
    with pytest.raises(refs.DocumentReferenceError, match="span window"):
        await value.resolve([DocumentSelection(chunk_index=42, span_index=1)])
    value.retain(chunk, spans, [1])
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
    assert model_result["selections"] == args["selections"]
    assert chunk["id"] not in projected.input[1]["output"]
    assert model_result["verified_quote"] == canonical["verified_quote"]
    assert chunk["id"] in items[1]["output"]  # SDK/runtime/UI original is untouched.


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
        value.retain(chunk, spans, [index])
        value.retain(chunk, spans, [index])
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
    value.retain(first, first_spans, [0, 1])
    value.retain(second, second_spans, [0])
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
    ("record_evidence", {"entity": "example", "span_ids": ["old-id"]}),
    ("record_evidence", {"entity": "example", "selections": [{"chunk_index": 42, "span_index": False}]}),
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
                "content": "Scientific span_id text is unchanged.", "span_text_access": "Use span_id."}})}}
    projected = await refs.project_history_source_result(original, user_id="alice")
    payload = json.loads(projected["payload"]["output"])
    assert "span_id" not in payload["summary"]
    assert "span_index" in payload["summary"]
    assert "span_id" not in payload["chunk"]["span_text_access"]
    assert payload["chunk"]["content"] == "Scientific span_id text is unchanged."
    assert "Select evidence_spans[].span_id." in original["payload"]["output"]


@pytest.mark.asyncio
async def test_workspace_failures_keep_their_actual_cause_and_classification():
    from src.lib.openai_agents.tools.evidence_workspace import create_get_recorded_evidence_tool
    from types import SimpleNamespace
    tool = create_get_recorded_evidence_tool("doc-a", "alice")
    with pytest.raises(RuntimeError, match="workspace"):
        await tool.on_invoke_tool(SimpleNamespace(tool_name="get_recorded_evidence", tool_call_id="read", run_config=None),
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
        binding(store, scope).retain(chunk, spans, [0])
    else:
        from uuid import UUID
        neighbor, _ = source(selected_chunk - 1, "Neighbor read does not issue the selected chunk.")
        neighbor["id"] = str(UUID(int=selected_chunk))
        neighbor_spans = [s.to_dict() for s in build_evidence_spans(chunk_id=neighbor["id"], chunk_text=neighbor["text"])]
        binding(store, scope).retain(neighbor, neighbor_spans, [0])
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
    value.retain(chunk, spans, [0])
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
    value.retain(chunk, spans, [0])
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
    binding(store, scope).retain(chunk, spans, [0])
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
    value.retain(chunk, spans, [0])
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
        value.retain(chunk, spans, [0])
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
