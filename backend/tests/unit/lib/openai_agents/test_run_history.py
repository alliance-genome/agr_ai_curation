"""Exact recovery, authorization and write-failure behavior of canonical history."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.models.sql.agent_run_history import AgentHistoryEvent, AgentHistoryPayload
from src.lib.openai_agents.run_history import (
    ACTIVE_HISTORY, HistoryScope, HistoryStore, RunHistory, exact_json, history_tools, wrap_history_tools,
)


@pytest.fixture
def store():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AgentHistoryPayload.__table__.create(engine)
    AgentHistoryEvent.__table__.create(engine)
    result = HistoryStore(sessionmaker(bind=engine, expire_on_commit=False))
    yield result
    engine.dispose()


@pytest.fixture
def scope(monkeypatch):
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "alice")
    monkeypatch.setattr("src.lib.context.get_current_trace_id", lambda: "run-a")
    monkeypatch.setattr("src.lib.context.get_current_session_id", lambda: "chat-a")
    return HistoryScope(owner_id="alice", run_id="run-a", invocation_id="root-a",
                        agent_key="supervisor", lineage="root-a/", session_id="chat-a", session_reader=True)


def test_restart_preserves_nested_payload_negation_nulls_and_duplicate_events(store, scope):
    value = {"nested": [{"negated": True, "quote": "Not expressed α😀.", "id": None}], "empty": []}
    first = store.append(scope, "result", exact_json(value), call_id="one")
    second = store.append(scope, "result", exact_json(value), call_id="two")
    restarted = HistoryStore(store.session_factory)
    assert first != second
    assert restarted.read(scope, first)["payload"] == value
    assert len(restarted.search(scope, "Not expressed", after=0, limit=10)) == 2
    with store.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentHistoryPayload)) == 1
        assert db.scalar(select(func.count()).select_from(AgentHistoryEvent)) == 2


def test_scope_denies_other_owner_session_sibling_and_parent_but_allows_child(store, scope):
    root = store.append(scope, "input", exact_json({"text": "secret"}))
    child_scope = replace(scope, invocation_id="child", lineage="root-a/child/", parent_invocation_id="root-a", session_reader=False)
    child = store.append(child_scope, "output", exact_json({"text": "secret child"}))
    sibling_scope = replace(child_scope, invocation_id="sibling", lineage="root-a/sibling/")
    for unauthorized, event in ((replace(scope, owner_id="bob"), root),
                                (replace(scope, session_id="chat-b"), root),
                                (child_scope, root), (sibling_scope, child)):
        with pytest.raises(ValueError, match="unavailable"):
            store.read(unauthorized, event)
        assert event not in {row["event_id"] for row in store.search(unauthorized, "secret", after=0, limit=10)}
    assert store.read(scope, child)["payload"]["text"] == "secret child"
    next_turn = replace(scope, run_id="run-b", invocation_id="root-b", lineage="root-b/")
    assert store.read(next_turn, root)["payload"]["text"] == "secret"


def test_literal_search_paging_does_not_treat_wildcards_as_sql_patterns(store, scope):
    for text in ("α_100%", "other", "α_100% repeated"):
        store.append(scope, "output", exact_json({"text": text}), tool_name="lookup")
    page = store.search(scope, "_100%", after=0, limit=1)
    second = store.search(scope, "_100%", after=page[-1]["sequence"], limit=1)
    assert len(page) == len(second) == 1
    assert page[0]["event_id"] != second[0]["event_id"]
    assert store.search(scope, "_100%", after=second[-1]["sequence"], limit=1) == []
    assert store.search(scope, "", after=0, limit=10, tool_name="different") == []


@pytest.mark.parametrize("serialization_layers", [0, 1, 2])
def test_literal_search_recovers_quotes_newlines_backslashes_and_unicode(store, scope, serialization_layers):
    quote = 'No "eve" signal.\nNot observed in adults. Path A\\B. α😀_100%\x00'
    value = {"text": quote}
    for _ in range(serialization_layers):
        value = {"output": exact_json(value)}
    ref = store.append(scope, "tool_result", exact_json(value))
    for query in (quote, 'No "eve" signal.', 'signal.\nNot observed', 'A\\B', 'α😀_100%\x00'):
        assert store.search(scope, query, after=0, limit=10)[0]["event_id"] == ref
    assert store.read(scope, ref)["payload"] == value


def test_builder_history_growth_is_linear_and_revisions_reconstruct_exactly(scope, monkeypatch):
    from src.lib.openai_agents import extraction_builder_workspace as builder
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **kwargs: None)
    class CaptureStore:
        def __init__(self):
            self.records = []
        def append(self, scope, kind, content, **metadata):
            self.records.append(json.loads(content))
            return str(len(self.records))
    store = CaptureStore()
    history = RunHistory(scope, store)
    workspace = builder.ExtractionBuilderWorkspace(run_id=scope.run_id)
    token = ACTIVE_HISTORY.set(history)
    try:
        for index in range(100):
            workspace.upsert_candidate(candidate_id=str(index), staged_fields={"items": [{"quote": "NOT α😀 " * 300}]})
        first_half_bytes = history.submitted_payload_bytes
        for index in range(100, 200):
            workspace.upsert_candidate(candidate_id=str(index), staged_fields={"items": [{"quote": "NOT α😀 " * 300}]})
        assert history.submitted_payload_bytes < first_half_bytes * 2.1
        assert history.events_written == 200
        assert history.write_duration_ms > 0
        workspace.record_validation_failure(errors=[{"message": "validation A", "resolved_to": "B"}], candidate_ids=["0"])
        workspace.record_validation_failure(errors=[{"message": "validation B", "negated": True}], candidate_ids=["0"])
        workspace.discard_candidate("0", reason="superseded")
        workspace.finalize(candidate_ids=["1"])
        reconstructed = {}
        previous = None
        for index, record in enumerate(store.records, 1):
            assert record["previous_event_id"] == previous
            previous = str(index)
            reconstructed.update((candidate["candidate_id"], candidate) for candidate in record["changed_candidates"])
        assert list(reconstructed.values()) == workspace.snapshot(redact_payload=False)["candidates"]
        assert history.events_written == len(store.records) == 204
        assert history.submitted_payload_bytes == sum(len(exact_json(record).encode()) for record in store.records)
        assert store.records[200]["changed_candidates"][0]["validation_errors"][0]["message"] == "validation A"
        assert store.records[201]["changed_candidates"][0]["validation_errors"][0]["negated"] is True
        assert len(store.records[-1]["changed_candidates"]) == 1
        assert store.records[-1]["output"]["payload"] == workspace.finalization.payload
    finally:
        ACTIVE_HISTORY.reset(token)


def test_builder_archive_failure_is_counted_without_undoing_mutation(scope, monkeypatch):
    from src.lib.openai_agents import extraction_builder_workspace as builder
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **kwargs: None)
    class BrokenStore:
        def append(self, *args, **kwargs):
            raise OSError("unavailable")
    history = RunHistory(scope, BrokenStore())
    workspace = builder.ExtractionBuilderWorkspace(run_id=scope.run_id)
    token = ACTIVE_HISTORY.set(history)
    try:
        workspace.upsert_candidate(candidate_id="one", staged_fields={"items": []})
        assert workspace.get_candidate("one").revision == 1
        assert not history.healthy and history.write_failures == 1
        assert history.events_written == history.submitted_payload_bytes == 0
    finally:
        ACTIVE_HISTORY.reset(token)


def test_concurrent_builder_writers_cannot_publish_a_broken_history_chain(scope, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from src.lib.openai_agents import extraction_builder_workspace as builder
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **kwargs: None)
    first_append, release_first, second_started = Event(), Event(), Event()
    records = []
    class BlockingStore:
        def append(self, scope, kind, content, **metadata):
            if not records:
                first_append.set()
                assert release_first.wait(5)
            records.append(json.loads(content))
            return str(len(records))
    history = RunHistory(scope, BlockingStore())
    workspace = builder.ExtractionBuilderWorkspace(run_id=scope.run_id)
    def mutate(candidate_id):
        token = ACTIVE_HISTORY.set(history)
        try:
            if candidate_id == "B":
                second_started.set()
            workspace.upsert_candidate(candidate_id=candidate_id, staged_fields={"items": [{"id": candidate_id}]})
        finally:
            ACTIVE_HISTORY.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(mutate, "A")
        assert first_append.wait(5)
        second = pool.submit(mutate, "B")
        assert second_started.wait(5)
        try:
            assert not second.done()
            # B is blocked before touching the live workspace, not merely before
            # publishing its delta. A's archived revision cannot be overwritten.
            assert set(workspace.candidates) == {"A"}
        finally:
            release_first.set()
        first.result(timeout=5)
        second.result(timeout=5)
    mutate("C")
    reconstructed, previous = {}, None
    for index, record in enumerate(records, 1):
        assert record["previous_event_id"] == previous
        previous = str(index)
        reconstructed.update((c["candidate_id"], c) for c in record["changed_candidates"])
    assert list(reconstructed.values()) == workspace.snapshot(redact_payload=False)["candidates"]
    assert [len(record["changed_candidates"]) for record in records] == [1, 1, 1]
    assert history.healthy and history.events_written == 3


@pytest.mark.asyncio
async def test_large_nested_exact_read_pages_and_search_are_bounded(store, scope, monkeypatch):
    monkeypatch.setenv("TOOL_RESULT_MAX_BYTES", "2048")
    value = {"findings": [{"quote": "NOT observed α😀 " * 500, "negated": True}]}
    ref = store.append(scope, "output", exact_json(value))
    tools = {tool.name: tool for tool in history_tools(RunHistory(scope, store))}
    cursor, sha, parts = 0, None, []
    while cursor is not None:
        result = await tools["read_run_history"].on_invoke_tool(SimpleNamespace(tool_name="read_run_history", tool_call_id="read", run_config=None), json.dumps({
            "event_id": ref, "detail_path": "payload.findings.0", "detail_cursor": cursor, "result_sha256": sha}))
        assert len(exact_json(result).encode()) <= 2048
        detail = result["detail"]
        parts.append(detail["content"])
        cursor, sha = detail["next_cursor"], detail["sha256"]
    assert json.loads("".join(parts)) == value["findings"][0]
    bad = await tools["read_run_history"].on_invoke_tool(SimpleNamespace(tool_name="read_run_history", tool_call_id="read", run_config=None), json.dumps({
        "event_id": ref, "result_sha256": "stale"}))
    assert bad["error_code"] == "stale_result_cursor"


@pytest.mark.asyncio
@pytest.mark.parametrize("reporting_fails", [False, True])
async def test_failed_archive_write_does_not_retry_or_hide_a_completed_mutation(scope, monkeypatch, caplog, reporting_fails):
    from agents import function_tool
    reports = []
    def report(exc, **kwargs):
        reports.append((exc, kwargs))
        if reporting_fails:
            raise RuntimeError("reporting offline")
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", report)
    class BrokenStore:
        def append(self, *args, **kwargs):
            raise OSError("SQL INSERT private-payload-secret")
    history = RunHistory(scope, BrokenStore())
    calls = []
    @function_tool
    async def save_record(value: str) -> dict:
        calls.append(value)
        assert ACTIVE_HISTORY.get() is history
        return {"saved": True, "id": "saved-1"}
    tool = wrap_history_tools([save_record], history)[0]
    result = await tool.on_invoke_tool(SimpleNamespace(tool_call_id="call-one", tool_name="save_record", run_config=None), '{"value":"one"}')
    assert calls == ["one"]
    assert result == {"saved": True, "id": "saved-1"}
    assert not history.healthy
    assert ACTIVE_HISTORY.get() is None
    assert history.write_failures == 1
    assert len(reports) == 1
    exc, metadata = reports[0]
    assert exc.__context__ is exc.__cause__ is None
    assert metadata == {"component": "agent_history", "operation": "write_failed", "context": {"error_type": "OSError"}}
    assert "private-payload-secret" not in str(reports) + caplog.text
    log = next(record for record in caplog.records if record.name.endswith("run_history"))
    assert log.sentry_skip_event is True and log.exc_info is None


@pytest.mark.asyncio
async def test_capture_input_deduplicates_payloads_but_preserves_repeated_messages(store, scope):
    history = RunHistory(scope, store)
    items = [{"role": "user", "content": "repeat"}] * 2
    refs = await history.capture_input(items, "instructions")
    assert refs[0] != refs[1]
    assert await history.capture_input(items, "instructions") == refs
    assert len(store.search(scope, "repeat", after=0, limit=10)) == 2


@pytest.mark.asyncio
async def test_reused_history_tool_checks_request_identity_again(store, scope, monkeypatch):
    ref = store.append(scope, "output", exact_json({"private": "alice only"}))
    tool = next(t for t in history_tools(RunHistory(scope, store)) if t.name == "read_run_history")
    monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "bob")
    result = await tool.on_invoke_tool(SimpleNamespace(tool_name=tool.name, tool_call_id="read", run_config=None), json.dumps({"event_id": ref}))
    assert "alice only" not in str(result)
    assert result["status"] == "invalid_request"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["read", "search"])
async def test_history_storage_read_failures_report_sanitized_errors(scope, monkeypatch, caplog, operation):
    reports = []
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", lambda exc, **kw: reports.append((exc, kw)))
    class BrokenStore:
        def read(self, *args, **kwargs):
            raise ValueError("SQL private-payload-secret")
        search = read
    tool = next(t for t in history_tools(RunHistory(scope, BrokenStore())) if t.name == operation + "_run_history")
    result = await tool.on_invoke_tool(SimpleNamespace(tool_name=tool.name, tool_call_id="call", run_config=None),
        json.dumps({"event_id": "missing"} if operation == "read" else {"query": "private-query"}))
    assert result["status"] == "invalid_request"
    assert len(reports) == 1
    exc, metadata = reports[0]
    assert exc.__context__ is exc.__cause__ is None
    assert metadata == {"component": "agent_history", "operation": operation + "_failed", "context": {"error_type": "ValueError"}}
    assert "private-payload-secret" not in str(reports) + caplog.text + str(result)
    assert "private-query" not in str(reports) + caplog.text
    log = next(record for record in caplog.records if record.name.endswith("run_history"))
    assert log.sentry_skip_event is True and log.exc_info is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_request", ["missing", "path", "cursor", "scope", "search_cursor"])
async def test_expected_history_request_failures_do_not_report(store, scope, monkeypatch, bad_request):
    reports = []
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", lambda *a, **kw: reports.append(kw))
    ref = store.append(scope, "output", exact_json({"text": "value"}))
    name, arguments = "read_run_history", {"event_id": ref}
    if bad_request == "missing":
        arguments["event_id"] = "missing"
    elif bad_request == "path":
        arguments["detail_path"] = "payload.missing"
    elif bad_request == "cursor":
        arguments["detail_cursor"] = -1
    elif bad_request == "scope":
        monkeypatch.setattr("src.lib.context.get_current_user_id", lambda: "bob")
    else:
        name, arguments = "search_run_history", {"after": -1}
    tool = next(t for t in history_tools(RunHistory(scope, store)) if t.name == name)
    result = await tool.on_invoke_tool(SimpleNamespace(tool_name=name, tool_call_id="call", run_config=None), json.dumps(arguments))
    assert result["status"] == "invalid_request"
    assert reports == []


@pytest.mark.asyncio
async def test_input_serialization_failure_disables_eviction_and_reports_once(store, scope, monkeypatch):
    reports = []
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", lambda exc, **kw: reports.append((exc, kw)))
    history = RunHistory(scope, store)
    assert await history.capture_input([object(), object()], "instructions") == {}
    assert not history.healthy and history.write_failures == 2
    assert len(reports) == 1 and reports[0][1]["operation"] == "write_failed"


def test_concurrent_archive_failures_alert_once_per_invocation(scope, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    reports, barrier = [], Barrier(2)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", lambda exc, **kw: reports.append((exc, kw)))
    class BrokenStore:
        def append(self, *args, **kwargs):
            barrier.wait(timeout=5)
            raise OSError("private SQL")
    history = RunHistory(scope, BrokenStore())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(history.record_sync, "output", {"value": i}) for i in range(2)]
        assert [future.result(timeout=5) for future in futures] == [None, None]
    assert not history.healthy and history.write_failures == 2
    assert len(reports) == 1


@pytest.mark.asyncio
async def test_corrupt_canonical_history_reports_integrity_failure(store, scope, monkeypatch):
    reports = []
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", lambda exc, **kw: reports.append((exc, kw)))
    ref = store.append(scope, "output", exact_json({"value": "original"}))
    with store.session_factory() as db, db.begin():
        payload = db.scalar(select(AgentHistoryPayload))
        payload.content = exact_json({"value": "corrupt"})
    tool = next(t for t in history_tools(RunHistory(scope, store)) if t.name == "read_run_history")
    result = await tool.on_invoke_tool(SimpleNamespace(tool_name=tool.name, tool_call_id="read", run_config=None), json.dumps({"event_id": ref}))
    assert result["status"] == "invalid_request"
    assert len(reports) == 1 and reports[0][1]["operation"] == "read_failed"


@pytest.mark.asyncio
async def test_model_checkpoint_survives_storage_restart_without_mutating_actions(store, scope):
    history = RunHistory(scope, store)
    items = [{"type": "compaction", "encrypted_content": "opaque-state"},
             {"type": "function_call", "call_id": "saved", "name": "save_record", "arguments": "{}"},
             {"type": "function_call_output", "call_id": "saved", "output": '{"id":"A","negated":true}'}]
    ref = await history.checkpoint(items, "Preserve negation.", {"completed_tool_call_ids": ["saved"]})
    restarted = HistoryStore(store.session_factory)
    checkpoint = restarted.read(scope, ref)["payload"]
    recovered = [restarted.read(scope, item_ref)["payload"] for item_ref in checkpoint["items"]]
    assert recovered == items and checkpoint["instructions"] == "Preserve negation."
    with pytest.raises(ValueError, match="unavailable"):
        restarted.read(replace(scope, owner_id="other"), ref)
