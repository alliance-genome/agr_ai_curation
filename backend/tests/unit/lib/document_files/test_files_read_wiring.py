"""Files read wiring (KANBAN-1976 / ALL-1460, P8b2): who names the producing invocation,
what reaches the trace, and that results saved before files were recorded are unchanged.

The lookup itself runs on PostgreSQL (tests/integration/persistence/test_extraction_result_files_read.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from src.lib.curation_workspace import extraction_results as results
from src.schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)

FILES = [
    {"file_id": "file-0", "file_name": "paper.pdf", "role": "main",
     "converted_artifact_id": None, "converted_artifact_sha256": None},
    {"file_id": "file-1", "file_name": "Table S1.xlsx", "role": "supplement",
     "converted_artifact_id": "artifact-1", "converted_artifact_sha256": "1" * 64},
]


def _record(**overrides):
    values = dict(
        extraction_result_id="result-1", document_id="doc-1", adapter_key="reference_adapter",
        agent_key="gene_extractor", source_kind=CurationExtractionSourceKind.CHAT,
        candidate_count=1, payload_json={"items": []},
        created_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )
    values.update(overrides)
    return CurationExtractionResultRecord(**values)


def test_a_result_saved_before_files_were_recorded_serializes_exactly_as_before():
    # Golden: the served JSON of a pre-release result (files_read NULL) has no new key.
    served = json.loads(_record().model_dump_json())

    assert served == {
        "extraction_result_id": "result-1", "execution_receipt": None, "document_id": "doc-1",
        "adapter_key": "reference_adapter", "agent_key": "gene_extractor", "source_kind": "chat",
        "origin_session_id": None, "trace_id": None, "flow_run_id": None, "user_id": None,
        "candidate_count": 1, "conversation_summary": None, "payload_json": {"items": []},
        "idempotency_key": None, "payload_hash": None, "created_at": "2026-11-01T00:00:00Z",
        "metadata": {},
    }


def test_a_recorded_result_serves_its_files_and_an_empty_list_means_none_read():
    assert _record(files_read=FILES).model_dump(mode="json")["files_read"] == FILES
    assert _record(files_read=[]).model_dump(mode="json")["files_read"] == []


def test_the_record_stores_the_producing_invocations_files_and_traces_them(monkeypatch):
    lookups, traced = [], []
    monkeypatch.setattr(results, "files_read_for_result",
                        lambda db, **kwargs: lookups.append(kwargs) or FILES)
    monkeypatch.setattr(results, "write_extraction_trace_event", lambda **kwargs: traced.append(kwargs))
    request = CurationExtractionPersistenceRequest(
        document_id=str(uuid4()), adapter_key="reference_adapter", agent_key="gene_extractor",
        source_kind=CurationExtractionSourceKind.FLOW, trace_id="trace-1", user_id="owner-1",
        payload_json={"items": []}, history_invocation_ids=["invocation-1", "writer-1"],
    )

    record = results._build_extraction_result_record(request, session=object())
    record.id = "result-1"
    results._trace_files_read(record)

    assert record.files_read == FILES
    assert lookups == [{"owner_id": "owner-1", "run_id": "trace-1", "document_id": request.document_id,
                        "invocation_ids": ["invocation-1", "writer-1"]}]
    assert traced == [{"event_type": "extraction.files_read", "trace_id": "trace-1",
                       "output_summary": {"extraction_result_id": "result-1", "files_read": FILES}}]


def test_a_result_without_an_identified_run_records_nothing_and_traces_nothing(monkeypatch):
    traced = []
    monkeypatch.setattr(results, "write_extraction_trace_event", lambda **kwargs: traced.append(kwargs))
    request = CurationExtractionPersistenceRequest(
        document_id=str(uuid4()), adapter_key="reference_adapter", agent_key="gene_extractor",
        source_kind=CurationExtractionSourceKind.CHAT, payload_json={"items": []},
    )

    record = results._build_extraction_result_record(request, session=object())
    results._trace_files_read(record)

    assert record.files_read is None
    assert traced == []


def _agent(invocation_id):
    return SimpleNamespace(run_history=SimpleNamespace(scope=SimpleNamespace(invocation_id=invocation_id)))


def test_the_result_reads_are_the_agents_and_every_fanout_writers():
    from src.lib.openai_agents import streaming_tools

    planner = _agent("planner-1")
    fanout = SimpleNamespace(writer_invocation_ids=["writer-1", "writer-1-rerun", "writer-2"],
                             writer_invocations_unknown=False)

    assert streaming_tools._files_read_scope(planner) == {"history_invocation_ids": ["planner-1"]}
    assert streaming_tools._files_read_scope(planner, fanout) == {
        "history_invocation_ids": ["planner-1", "writer-1", "writer-1-rerun", "writer-2"]}
    # History off: every read row carries the run, so the whole run is complete.
    assert streaming_tools._files_read_scope(SimpleNamespace(run_history=None), fanout) == {}


def test_a_writer_whose_invocation_is_unknown_leaves_the_result_unrecorded():
    from src.lib.openai_agents import streaming_tools

    fanout = SimpleNamespace(writer_invocation_ids=["writer-1"], writer_invocations_unknown=True)

    assert streaming_tools._files_read_scope(_agent("planner-1"), fanout) == {"files_read_unrecorded": True}


def test_an_unrecorded_scope_saves_null_files_read(monkeypatch):
    monkeypatch.setattr(results, "files_read_for_result", lambda db, **kwargs: FILES)
    request = CurationExtractionPersistenceRequest(
        document_id=str(uuid4()), adapter_key="reference_adapter", agent_key="gene_extractor",
        source_kind=CurationExtractionSourceKind.CHAT, trace_id="trace-1", user_id="owner-1",
        payload_json={"items": []}, history_invocation_ids=["planner-1"], files_read_unrecorded=True,
    )

    assert results._build_extraction_result_record(request, session=object()).files_read is None


def test_the_specialists_extraction_event_names_its_invocations():
    from src.lib.openai_agents import streaming_tools

    event = streaming_tools._with_files_read_scope(
        {"internal": {"tool_output": "{}"}}, {"history_invocation_ids": ["planner-1", "writer-1"]},
    )

    assert event["internal"]["history_invocation_ids"] == ["planner-1", "writer-1"]


def test_a_flow_step_takes_the_invocations_from_its_extraction_event():
    from src.lib.flows import executor
    from src.lib.openai_agents.event_types import INTERNAL_EXTRACTION_RESULT_EVENT_TYPE

    event = {"type": INTERNAL_EXTRACTION_RESULT_EVENT_TYPE, "details": {"toolName": "ask_gene"},
             "internal": {"tool_output": "{}", "history_invocation_ids": ["planner-1", "writer-1"]}}
    cursor = {"live_events": [event], "live_index": 0}
    _output, audit = executor._internal_extraction_tool_output_with_audit_since(cursor, tool_name="ask_gene")

    assert executor._internal_extraction_files_read_scope_since(cursor, tool_name="ask_gene") == {
        "history_invocation_ids": ["planner-1", "writer-1"], "files_read_unrecorded": False}
    # The lookup audit is published in flow events; the invocation ids stay out of it.
    assert "history_invocation_ids" not in audit


def test_the_trace_metadata_carries_file_ids_and_names_only(monkeypatch):
    from src.lib.openai_agents import runner

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(runner, "SessionLocal", _Session)
    monkeypatch.setattr(runner, "files_read_for_result", lambda db, **kwargs: FILES)
    updates = []
    span = SimpleNamespace(update=lambda **kwargs: updates.append(kwargs))

    runner._record_trace_files_read(span, user_id="owner-1", trace_id="trace-1", document_id="doc-1")

    assert updates == [{"metadata": {"files_read": [
        {"file_id": "file-0", "file_name": "paper.pdf"},
        {"file_id": "file-1", "file_name": "Table S1.xlsx"},
    ]}}]
