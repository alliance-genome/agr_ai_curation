"""Runtime-focused tests for supervisor agent helpers."""

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lib.chat_history_repository import ChatMessageRecord
from src.lib.openai_agents import curation_context_registry, supervisor_context_tools
from src.lib.openai_agents.agents import supervisor_agent


def _patch_supervisor_prompt_bundle(monkeypatch, *, version: int = 1):
    prompt = SimpleNamespace(
        agent_name="supervisor",
        prompt_type="system",
        group_id=None,
        version=version,
        id="prompt-id",
    )

    def _bundle(_agent_id, group_id=None, runtime_context=None):
        rendered = "\n\n".join(
            part
            for part in ["Base prompt", str(runtime_context or "").strip()]
            if part
        )
        return SimpleNamespace(
            render=lambda: rendered,
            hash=f"hash-{version}",
            to_manifest=lambda: {
                "agent_id": "supervisor",
                "layers": [],
                "hash": f"hash-{version}",
            },
        )

    monkeypatch.setattr(supervisor_agent, "build_agent_prompt_layers", _bundle)
    monkeypatch.setattr(supervisor_agent, "prompt_templates_for_bundle", lambda _bundle: [prompt])


class _Field:
    def __eq__(self, _other):
        return True

    def asc(self):
        return self


class _FakeAgentRecord:
    visibility = _Field()
    is_active = _Field()
    supervisor_enabled = _Field()
    agent_key = _Field()


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self.filtered = False
        self.ordered = False

    def filter(self, *_args, **_kwargs):
        self.filtered = True
        return self

    def order_by(self, *_args, **_kwargs):
        self.ordered = True
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False
        self.last_query = None

    def query(self, _model):
        self.last_query = _FakeQuery(self._rows)
        return self.last_query

    def close(self):
        self.closed = True


class _PrepExtractionRecord:
    def __init__(self, **overrides):
        payload = {
            "extraction_result_id": "extract-1",
            "document_id": "document-1",
            "adapter_key": "reference_adapter",
            "profile_key": None,
            "domain_key": "disease",
            "agent_key": "disease_extractor",
            "source_kind": "chat",
            "origin_session_id": "session-1",
            "trace_id": "trace-upstream",
            "flow_run_id": None,
            "user_id": "user-1",
            "candidate_count": 1,
            "conversation_summary": "Disease extraction kept APOE-related findings.",
            "payload_json": {
                "items": [
                    {
                        "label": "APOE",
                        "entity_type": "gene",
                        "evidence": [
                            {
                                "entity": "APOE",
                                "verified_quote": "APOE was associated with the disease phenotype.",
                                "page": 3,
                                "section": "Results",
                                "subsection": "Disease association",
                                "chunk_id": "chunk-apoe-1",
                                "figure_reference": "Fig. 2",
                            }
                        ],
                    }
                ],
                "run_summary": {"candidate_count": 1},
            },
            "created_at": "2026-03-21T00:00:00Z",
            "metadata": {},
        }
        payload.update(overrides)
        self._payload = payload
        for key, value in payload.items():
            setattr(self, key, value)

    def model_dump(self, mode="python"):
        return dict(self._payload)


class _DumpablePayload:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, mode="python"):
        return dict(self._payload)


class _FakeContextDb:
    def __init__(self, *, row=None, scalar_value=False):
        self.row = row
        self.scalar_value = scalar_value
        self.closed = False

    def get(self, _model, _row_id):
        return self.row

    def scalar(self, _statement):
        return self.scalar_value

    def close(self):
        self.closed = True


class _FakeFileOutputQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeSessionFilesDb(_FakeContextDb):
    def __init__(self, *, row=None, user_files=None, other_user_file=None, scalar_value=False):
        super().__init__(row=row, scalar_value=scalar_value)
        self.user_files = list(user_files or [])
        self.other_user_file = other_user_file
        self.query_count = 0

    def query(self, _model):
        self.query_count += 1
        if self.query_count % 2 == 1:
            return _FakeFileOutputQuery([self.other_user_file] if self.other_user_file else [])
        return _FakeFileOutputQuery(self.user_files)


def _chat_message_record(**overrides):
    payload = {
        "message_id": uuid4(),
        "session_id": "session-1",
        "chat_kind": "assistant",
        "turn_id": "turn-1",
        "role": "assistant",
        "message_type": "text",
        "content": "Assistant response.",
        "payload_json": None,
        "trace_id": None,
        "created_at": datetime(2026, 6, 6, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return ChatMessageRecord(**payload)


def test_supervisor_prompt_explains_result_inspection_boundaries():
    repo_root = Path(__file__).resolve().parents[6]
    prompt_text = (repo_root / "config/agents/supervisor/prompt.yaml").read_text()
    normalized_prompt = " ".join(prompt_text.split())

    assert "inspect_results(action=\"help\")" in prompt_text
    assert "extraction-result:<uuid>" in prompt_text
    assert "do not call another extractor just to summarize" in normalized_prompt
    assert "Export and curation prep are separate explicit actions" in prompt_text
    assert "trace inspection only to debug behavior" in prompt_text
    assert "inspect_curation_context" not in prompt_text


@pytest.mark.asyncio
async def test_inspect_chat_traces_inventory_includes_main_chat_and_flow_rows(monkeypatch):
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_trace_id", lambda: None)
    monkeypatch.setattr(
        supervisor_context_tools,
        "_list_session_messages",
        lambda **_kwargs: [
            _chat_message_record(role="user", content="Why did you extract crb?"),
            _chat_message_record(
                role="assistant",
                content="I extracted crb because the Results section supported it.",
                trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ),
            _chat_message_record(
                role="flow",
                message_type="flow_summary",
                content="Flow completed.",
                payload_json={"_assistant_message": "Flow extracted one gene."},
                trace_id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            ),
        ],
    )

    response = await supervisor_context_tools.inspect_chat_traces(
        detail="inventory",
        limit=10,
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert [trace["trace_id"] for trace in payload["traces"]] == [
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
    assert payload["traces"][0]["source"] == "assistant_message"
    assert payload["traces"][1]["source"] == "execute_flow_transcript"
    assert payload["traces"][0]["user_question_preview"] == "Why did you extract crb?"


@pytest.mark.asyncio
async def test_inspect_chat_traces_rejects_unowned_trace_before_trace_review(monkeypatch):
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_trace_id", lambda: None)
    monkeypatch.setattr(
        supervisor_context_tools,
        "_list_session_messages",
        lambda **_kwargs: [
            _chat_message_record(role="user", content="Question"),
            _chat_message_record(
                role="assistant",
                content="Answer",
                trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ),
        ],
    )

    async def _unexpected_trace_review_call(_trace_id):
        raise AssertionError("TraceReview must not be called for unauthorized trace IDs")

    monkeypatch.setattr(supervisor_context_tools, "get_trace_summary", _unexpected_trace_review_call)

    response = await supervisor_context_tools.inspect_chat_traces(
        detail="summary",
        trace_id="cccccccccccccccccccccccccccccccc",
    )

    payload = json.loads(response)
    assert payload["status"] == "unauthorized_trace"
    assert payload["authorized_trace_ids"] == ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]


@pytest.mark.asyncio
async def test_inspect_chat_traces_summary_uses_authorized_allowlist(monkeypatch):
    captured = {}
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_trace_id", lambda: None)
    monkeypatch.setattr(
        supervisor_context_tools,
        "_list_session_messages",
        lambda **_kwargs: [
            _chat_message_record(role="user", content="Question"),
            _chat_message_record(
                role="assistant",
                content="Answer",
                trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ),
        ],
    )

    async def _fake_trace_summary(trace_id):
        captured["trace_id"] = trace_id
        return {
            "status": "success",
            "data": {"trace_id": trace_id, "tool_call_count": 2},
            "token_info": {"estimated_tokens": 50},
            "error": None,
        }

    monkeypatch.setattr(supervisor_context_tools, "get_trace_summary", _fake_trace_summary)

    response = await supervisor_context_tools.inspect_chat_traces(
        detail="summary",
        trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["data"]["tool_call_count"] == 2
    assert captured["trace_id"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


@pytest.mark.asyncio
async def test_inspect_chat_traces_inventory_turn_ref_selects_previous_completed_trace(monkeypatch):
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        supervisor_context_tools,
        "get_current_trace_id",
        lambda: "cccccccccccccccccccccccccccccccc",
    )
    monkeypatch.setattr(
        supervisor_context_tools,
        "_list_session_messages",
        lambda **_kwargs: [
            _chat_message_record(role="user", content="First question", turn_id="turn-1"),
            _chat_message_record(
                role="assistant",
                content="First answer",
                trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                turn_id="turn-1",
            ),
            _chat_message_record(role="user", content="Second question", turn_id="turn-2"),
            _chat_message_record(
                role="assistant",
                content="Second answer",
                trace_id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                turn_id="turn-2",
            ),
        ],
    )

    response = await supervisor_context_tools.inspect_chat_traces(
        detail="inventory",
        turn_ref="previous",
        limit=10,
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert [trace["trace_id"] for trace in payload["traces"]] == [
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    ]
    assert payload["traces"][0]["source"] == "assistant_message"


@pytest.mark.asyncio
async def test_inspect_chat_traces_uses_safe_trace_review_flags(monkeypatch):
    captured = {}
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_trace_id", lambda: None)
    monkeypatch.setattr(
        supervisor_context_tools,
        "_list_session_messages",
        lambda **_kwargs: [
            _chat_message_record(role="user", content="Question"),
            _chat_message_record(
                role="assistant",
                content="Answer",
                trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            ),
        ],
    )

    async def _fake_diagnostic_report(trace_id, **kwargs):
        captured["diagnostic"] = {"trace_id": trace_id, **kwargs}
        return {"status": "success", "data": {"ok": True}, "error": None}

    async def _fake_payloads(trace_id, **kwargs):
        captured["payloads"] = {"trace_id": trace_id, **kwargs}
        return {"status": "success", "data": {"payloads": []}, "error": None}

    monkeypatch.setattr(
        supervisor_context_tools,
        "get_extraction_diagnostic_report",
        _fake_diagnostic_report,
    )
    monkeypatch.setattr(supervisor_context_tools, "get_trace_payloads", _fake_payloads)

    diagnostic_response = await supervisor_context_tools.inspect_chat_traces(
        detail="diagnostic_report",
        trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    payload_response = await supervisor_context_tools.inspect_chat_traces(
        detail="payload_inventory",
        trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        limit=3,
        cursor="2",
    )

    assert json.loads(diagnostic_response)["status"] == "ok"
    assert captured["diagnostic"]["include_raw_args"] is False
    assert captured["diagnostic"]["include_raw_outputs"] is False
    assert json.loads(payload_response)["status"] == "ok"
    assert captured["payloads"]["include_values"] is False
    assert captured["payloads"]["limit"] == 3
    assert captured["payloads"]["offset"] == 2


@pytest.mark.asyncio
async def test_inspect_chat_traces_inventory_pages_recent_traces(monkeypatch):
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_trace_id", lambda: None)
    messages = [
        _chat_message_record(
            role="assistant",
            trace_id=f"{index:032d}",
            turn_id=f"turn-{index}",
            created_at=datetime(2026, 6, 6, 0, 0, index, tzinfo=timezone.utc),
        )
        for index in range(30)
    ]
    monkeypatch.setattr(
        supervisor_context_tools,
        "_list_session_messages",
        lambda **_kwargs: messages,
    )

    first_response = await supervisor_context_tools.inspect_chat_traces(
        detail="inventory",
        limit=2,
    )
    second_response = await supervisor_context_tools.inspect_chat_traces(
        detail="inventory",
        limit=2,
        cursor="2",
    )

    first_payload = json.loads(first_response)
    second_payload = json.loads(second_response)
    assert [trace["trace_id"] for trace in first_payload["traces"]] == [
        f"{28:032d}",
        f"{29:032d}",
    ]
    assert first_payload["truncated"] is True
    assert first_payload["next_cursor"] == "2"
    assert [trace["trace_id"] for trace in second_payload["traces"]] == [
        f"{26:032d}",
        f"{27:032d}",
    ]
    assert second_payload["next_cursor"] == "4"


@pytest.mark.asyncio
async def test_inspect_curation_context_summarizes_authorized_persisted_results(monkeypatch):
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_trace_id", lambda: None)
    monkeypatch.setattr(supervisor_context_tools, "_active_document_id", lambda _user_id: None)
    monkeypatch.setattr(
        supervisor_context_tools,
        "list_extraction_results",
        lambda **_kwargs: [
            _PrepExtractionRecord(
                extraction_result_id="extract-1",
                adapter_key="gene",
                trace_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                payload_json={
                    "domain_pack_id": "gene",
                    "objects": [
                        {
                            "object_type": "gene_mention_evidence",
                            "pending_ref_id": "gene-1",
                            "status": "validated",
                            "payload": {
                                "mention": "crb",
                                "primary_external_id": "FB:FBgn0259685",
                            },
                        }
                    ],
                    "validation_findings": [{"status": "resolved"}],
                },
            )
        ],
    )

    response = await supervisor_context_tools.inspect_curation_context(
        scope="current_chat",
        detail="summary",
        adapter_keys=["gene"],
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["results"][0]["extraction_result_id"] == "extract-1"
    assert payload["results"][0]["object_count"] == 1
    assert payload["results"][0]["validation_finding_count"] == 1


@pytest.mark.asyncio
async def test_inspect_curation_context_current_chat_includes_flow_results(monkeypatch):
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_trace_id", lambda: None)
    monkeypatch.setattr(supervisor_context_tools, "_active_document_id", lambda _user_id: None)

    calls = []

    def fake_list_extraction_results(**kwargs):
        calls.append(kwargs)
        if kwargs.get("source_kind") == supervisor_context_tools.CurationExtractionSourceKind.FLOW:
            return [
                _PrepExtractionRecord(
                    extraction_result_id="flow-extract-1",
                    source_kind="flow",
                    flow_run_id="flow-run-1",
                    adapter_key="gene",
                    payload_json={
                        "evidence": [
                            {
                                "evidence_record_id": "evidence-1",
                                "verified_quote": "crb was found in the flow result.",
                            }
                        ],
                    },
                )
            ]
        return [
            _PrepExtractionRecord(
                extraction_result_id="chat-extract-1",
                source_kind="chat",
                flow_run_id=None,
                adapter_key="gene",
                payload_json={"evidence": []},
            )
        ]

    monkeypatch.setattr(
        supervisor_context_tools,
        "list_extraction_results",
        fake_list_extraction_results,
    )

    response = await supervisor_context_tools.inspect_curation_context(
        detail="evidence",
        extraction_result_id="flow-extract-1",
        flow_run_id="flow-run-1",
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["refs"][0]["extraction_result_id"] == "flow-extract-1"
    assert payload["results"][0]["evidence"] == [
        {
            "evidence_record_id": "evidence-1",
            "verified_quote": "crb was found in the flow result.",
        }
    ]
    assert any(
        call.get("origin_session_id") == "session-1"
        and call.get("source_kind") == supervisor_context_tools.CurationExtractionSourceKind.FLOW
        for call in calls
    )


@pytest.mark.asyncio
async def test_inspect_curation_context_extraction_result_scope_requires_id(monkeypatch):
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")

    response = await supervisor_context_tools.inspect_curation_context(
        scope="extraction_result",
    )

    payload = json.loads(response)
    assert payload["status"] == "invalid_request"
    assert "extraction_result_id is required" in payload["message"]


@pytest.mark.asyncio
async def test_inspect_curation_context_review_session_inventory_is_authorized(monkeypatch):
    session_id = uuid4()
    session_row = SimpleNamespace(
        id=session_id,
        created_by_id="user-1",
        assigned_curator_id=None,
        flow_run_id="flow-run-1",
    )
    db = _FakeContextDb(row=session_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "get_session_detail",
        lambda _db, _session_id: _DumpablePayload(
            {
                "session_id": str(session_id),
                "status": "new",
                "adapter": {"adapter_key": "gene"},
                "document": {
                    "document_id": "document-1",
                    "title": "Paper",
                    "page_count": 3,
                },
                "flow_run_id": "flow-run-1",
                "progress": {
                    "total_candidates": 2,
                    "reviewed_candidates": 0,
                    "pending_candidates": 2,
                    "accepted_candidates": 0,
                    "rejected_candidates": 0,
                    "manual_candidates": 0,
                },
                "validation": None,
                "current_candidate_id": None,
                "prepared_at": "2026-06-06T00:00:00Z",
                "last_worked_at": None,
                "warnings": [],
                "tags": [],
            }
        ),
    )

    response = await supervisor_context_tools.inspect_curation_context(
        scope="review_session",
        review_session_id=str(session_id),
        detail="inventory",
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["scope"] == "review_session"
    assert payload["refs"][0]["review_session_id"] == str(session_id)
    assert payload["results"][0]["available_details"] == [
        "inventory",
        "summary",
        "candidates",
        "objects",
        "evidence",
        "validation_findings",
        "field",
    ]
    assert db.closed is True


@pytest.mark.asyncio
async def test_inspect_curation_context_review_session_allows_assigned_curator(monkeypatch):
    session_id = uuid4()
    session_row = SimpleNamespace(
        id=session_id,
        created_by_id="user-2",
        assigned_curator_id="user-1",
        flow_run_id="flow-run-1",
    )
    db = _FakeContextDb(row=session_row, scalar_value=False)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "get_session_detail",
        lambda _db, _session_id: _review_session_detail_payload(session_id),
    )

    response = await supervisor_context_tools.inspect_curation_context(
        scope="review_session",
        review_session_id=str(session_id),
        detail="inventory",
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["refs"][0]["review_session_id"] == str(session_id)


@pytest.mark.asyncio
async def test_inspect_curation_context_review_session_rejects_unauthorized_without_details(monkeypatch):
    session_id = uuid4()
    session_row = SimpleNamespace(
        id=session_id,
        created_by_id="user-2",
        assigned_curator_id=None,
        flow_run_id="flow-run-1",
    )
    db = _FakeContextDb(row=session_row, scalar_value=False)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)

    response = await supervisor_context_tools.inspect_curation_context(
        scope="review_session",
        review_session_id=str(session_id),
        detail="inventory",
    )

    payload = json.loads(response)
    assert payload["status"] == "unauthorized_context"
    assert "review_session_id" not in payload
    assert "results" not in payload


@pytest.mark.asyncio
async def test_inspect_curation_context_review_session_rejects_missing_without_details(monkeypatch):
    session_id = uuid4()
    db = _FakeContextDb(row=None)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)

    response = await supervisor_context_tools.inspect_curation_context(
        scope="review_session",
        review_session_id=str(session_id),
        detail="inventory",
    )

    payload = json.loads(response)
    assert payload["status"] == "unauthorized_context"
    assert "review_session_id" not in payload
    assert "results" not in payload


def _review_session_detail_payload(session_id, *, flow_run_id="flow-run-1"):
    return _DumpablePayload(
        {
            "session_id": str(session_id),
            "status": "in_review",
            "adapter": {"adapter_key": "gene"},
            "document": {"document_id": "document-1", "title": "Paper"},
            "flow_run_id": flow_run_id,
            "progress": {"total_candidates": 2},
            "validation": {"status": "needs_review"},
            "current_candidate_id": "cand-1",
            "prepared_at": "2026-06-06T00:00:00Z",
            "last_worked_at": None,
            "warnings": [],
            "tags": ["flow"],
            "notes": "Curator note " + ("x" * 1000),
            "extraction_results": [],
        }
    )


def _review_workspace_payload(session_id):
    return _DumpablePayload(
        {
            "workspace": {
                "candidates": [
                    {
                        "candidate_id": "cand-1",
                        "session_id": str(session_id),
                        "projection_ref": {
                            "envelope_id": "env-1",
                            "object_id": "obj-1",
                        },
                        "normalized_payload": {
                            "object_type": "Gene",
                            "symbol": "BRCA1",
                            "large_note": "x" * 1000,
                        },
                        "status": "accepted",
                        "adapter_key": "gene",
                        "display_label": "BRCA1",
                        "secondary_label": "TEST:1",
                        "evidence_anchors": [
                            {
                                "evidence_record_id": "ev-1",
                                "verified_quote": "BRCA1 was found.",
                                "large_payload": "y" * 1000,
                            }
                        ],
                        "validation_summary_projections": [
                            {
                                "finding_id": "vf-1",
                                "status": "resolved",
                                "message": "Identifier resolved.",
                                "large_payload": "z" * 1000,
                            }
                        ],
                    },
                    {
                        "candidate_id": "cand-2",
                        "session_id": str(session_id),
                        "projection_ref": {
                            "envelope_id": "env-1",
                            "object_id": "obj-2",
                        },
                        "normalized_payload": {
                            "object_type": "Gene",
                            "symbol": "TP53",
                        },
                        "status": "pending",
                        "adapter_key": "gene",
                        "display_label": "TP53",
                        "secondary_label": "TEST:2",
                        "evidence_anchor_projections": [
                            {"evidence_record_id": "ev-2", "quote": "TP53 quote."}
                        ],
                        "validation_summary_projections": [],
                    },
                ]
            }
        }
    )


@pytest.mark.asyncio
async def test_inspect_curation_context_review_session_candidates_paginate_and_filter(monkeypatch):
    session_id = uuid4()
    session_row = SimpleNamespace(
        id=session_id,
        created_by_id="user-1",
        assigned_curator_id=None,
        flow_run_id="flow-run-1",
    )
    db = _FakeContextDb(row=session_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "get_session_detail",
        lambda _db, _session_id: _review_session_detail_payload(session_id),
    )
    monkeypatch.setattr(
        supervisor_context_tools,
        "get_session_workspace",
        lambda _db, _session_id: _review_workspace_payload(session_id),
    )

    first_response = await supervisor_context_tools.inspect_curation_context(
        scope="review_session",
        review_session_id=str(session_id),
        detail="candidates",
        limit=1,
    )
    filtered_response = await supervisor_context_tools.inspect_curation_context(
        scope="review_session",
        review_session_id=str(session_id),
        detail="objects",
        object_ref="obj-2",
    )

    first_payload = json.loads(first_response)
    filtered_payload = json.loads(filtered_response)
    assert first_payload["status"] == "ok"
    assert first_payload["total_count"] == 2
    assert first_payload["truncated"] is True
    assert first_payload["next_cursor"] == "1"
    assert first_payload["results"][0]["candidate_id"] == "cand-1"
    assert filtered_payload["results"] == [
        {
            "candidate_id": "cand-2",
            "session_id": str(session_id),
            "envelope_id": "env-1",
            "object_id": "obj-2",
            "object_type": "Gene",
            "status": "pending",
            "adapter_key": "gene",
            "display_label": "TP53",
            "secondary_label": "TEST:2",
            "fields": {"object_type": "Gene", "symbol": "TP53"},
            "evidence_count": 1,
            "validation_finding_count": 0,
        }
    ]


@pytest.mark.asyncio
async def test_inspect_curation_context_review_session_evidence_validation_and_field(monkeypatch):
    session_id = uuid4()
    session_row = SimpleNamespace(
        id=session_id,
        created_by_id="user-1",
        assigned_curator_id=None,
        flow_run_id="flow-run-1",
    )
    db = _FakeContextDb(row=session_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "get_session_detail",
        lambda _db, _session_id: _review_session_detail_payload(session_id),
    )
    monkeypatch.setattr(
        supervisor_context_tools,
        "get_session_workspace",
        lambda _db, _session_id: _review_workspace_payload(session_id),
    )

    evidence_response = await supervisor_context_tools.inspect_curation_context(
        scope="review_session",
        review_session_id=str(session_id),
        detail="evidence",
        object_ref="cand-1",
    )
    validation_response = await supervisor_context_tools.inspect_curation_context(
        scope="review_session",
        review_session_id=str(session_id),
        detail="validation_findings",
        object_ref="cand-1",
    )
    field_missing_response = await supervisor_context_tools.inspect_curation_context(
        scope="review_session",
        review_session_id=str(session_id),
        detail="field",
    )
    field_response = await supervisor_context_tools.inspect_curation_context(
        scope="review_session",
        review_session_id=str(session_id),
        detail="field",
        object_ref="cand-1",
        field_path="normalized_payload.large_note",
    )

    evidence_payload = json.loads(evidence_response)
    validation_payload = json.loads(validation_response)
    field_missing_payload = json.loads(field_missing_response)
    field_payload = json.loads(field_response)
    assert evidence_payload["results"] == [
        {
            "evidence_record_id": "ev-1",
            "verified_quote": "BRCA1 was found.",
            "candidate_id": "cand-1",
        }
    ]
    assert validation_payload["results"][0]["candidate_id"] == "cand-1"
    assert validation_payload["results"][0]["status"] == "resolved"
    assert "z" * 600 not in json.dumps(validation_payload)
    assert field_missing_payload["status"] == "invalid_request"
    assert field_payload["results"][0]["candidate_id"] == "cand-1"
    assert field_payload["results"][0]["value"].endswith("...")
    assert "x" * 600 not in json.dumps(field_payload)


@pytest.mark.asyncio
async def test_inspect_curation_context_review_session_linked_flow_authorization(monkeypatch):
    session_id = uuid4()
    session_row = SimpleNamespace(
        id=session_id,
        created_by_id="other-user",
        assigned_curator_id=None,
        flow_run_id="flow-run-1",
    )
    db = _FakeContextDb(row=session_row, scalar_value=True)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "get_session_detail",
        lambda _db, _session_id: _review_session_detail_payload(session_id),
    )

    response = await supervisor_context_tools.inspect_curation_context(
        scope="review_session",
        review_session_id=str(session_id),
        flow_run_id="flow-run-1",
        detail="inventory",
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["refs"][0]["review_session_id"] == str(session_id)


@pytest.mark.asyncio
async def test_inspect_curation_context_file_metadata_enforces_owner(monkeypatch):
    file_id = uuid4()
    file_row = SimpleNamespace(
        id=file_id,
        filename="results.csv",
        file_type="csv",
        file_size=42,
        curator_id="user-1",
        session_id="session-1",
        trace_id="a" * 32,
        agent_name="csv_formatter",
        generation_model="configured-model",
        download_count=0,
        last_download_at=None,
        created_at="2026-06-06T00:00:00Z",
        file_metadata={"projection": "artifact"},
    )
    db = _FakeContextDb(row=file_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)

    response = await supervisor_context_tools.inspect_curation_context(
        scope="file",
        file_id=str(file_id),
        detail="metadata",
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["results"][0]["file_id"] == str(file_id)
    assert payload["results"][0]["download_url"] == f"/api/files/{file_id}/download"


@pytest.mark.asyncio
async def test_inspect_curation_context_file_rejects_unauthorized_without_details(monkeypatch):
    file_id = uuid4()
    file_row = SimpleNamespace(
        id=file_id,
        filename="hidden.csv",
        file_type="csv",
        file_size=42,
        curator_id="user-2",
        session_id="session-2",
        trace_id=None,
        agent_name="csv_formatter",
        generation_model=None,
        download_count=0,
        last_download_at=None,
        created_at="2026-06-06T00:00:00Z",
        file_metadata={},
    )
    db = _FakeContextDb(row=file_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)

    response = await supervisor_context_tools.inspect_curation_context(
        scope="file",
        file_id=str(file_id),
        detail="metadata",
    )

    payload = json.loads(response)
    assert payload["status"] == "unauthorized_context"
    assert "hidden.csv" not in json.dumps(payload)
    assert "results" not in payload


@pytest.mark.asyncio
async def test_inspect_curation_context_file_rejects_missing_without_details(monkeypatch):
    file_id = uuid4()
    db = _FakeContextDb(row=None)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)

    response = await supervisor_context_tools.inspect_curation_context(
        scope="file",
        file_id=str(file_id),
        detail="metadata",
    )

    payload = json.loads(response)
    assert payload["status"] == "unauthorized_context"
    assert "file_id" not in payload
    assert "results" not in payload


@pytest.mark.asyncio
async def test_inspect_curation_context_file_schema_is_bounded_under_storage(
    monkeypatch,
    tmp_path,
):
    file_id = uuid4()
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    file_path = storage_base / "results.csv"
    file_path.write_text("symbol,status\nBRCA1,validated\nTP53,needs_review\n", encoding="utf-8")
    file_row = SimpleNamespace(
        id=file_id,
        filename="results.csv",
        file_type="csv",
        file_size=file_path.stat().st_size,
        curator_id="user-1",
        session_id="session-1",
        trace_id="a" * 32,
        agent_name="csv_formatter",
        generation_model=None,
        download_count=0,
        last_download_at=None,
        created_at="2026-06-06T00:00:00Z",
        file_metadata={},
        file_path=str(file_path),
    )
    db = _FakeContextDb(row=file_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "FileOutputStorageService",
        lambda: SimpleNamespace(base_path=storage_base),
    )

    response = await supervisor_context_tools.inspect_curation_context(
        scope="file",
        file_id=str(file_id),
        detail="schema",
        limit=1,
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["results"][0]["headers"] == ["symbol", "status"]
    assert payload["results"][0]["preview_rows"] == [
        {"symbol": "BRCA1", "status": "validated"}
    ]


@pytest.mark.asyncio
async def test_inspect_curation_context_tsv_file_preview_is_bounded(
    monkeypatch,
    tmp_path,
):
    file_id = uuid4()
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    file_path = storage_base / "results.tsv"
    file_path.write_text("symbol\tstatus\nBRCA1\tvalidated\nTP53\tneeds_review\n", encoding="utf-8")
    file_row = SimpleNamespace(
        id=file_id,
        filename="results.tsv",
        file_type="tsv",
        file_size=file_path.stat().st_size,
        curator_id="user-1",
        session_id="session-1",
        trace_id="a" * 32,
        agent_name="tsv_formatter",
        generation_model=None,
        download_count=0,
        last_download_at=None,
        created_at="2026-06-06T00:00:00Z",
        file_metadata={},
        file_path=str(file_path),
    )
    db = _FakeContextDb(row=file_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "FileOutputStorageService",
        lambda: SimpleNamespace(base_path=storage_base),
    )

    response = await supervisor_context_tools.inspect_curation_context(
        scope="file",
        file_id=str(file_id),
        detail="preview",
        limit=1,
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["results"][0]["headers"] == ["symbol", "status"]
    assert payload["results"][0]["rows"] == [{"symbol": "BRCA1", "status": "validated"}]


@pytest.mark.asyncio
async def test_inspect_curation_context_ragged_csv_preview_is_bounded(
    monkeypatch,
    tmp_path,
):
    file_id = uuid4()
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    file_path = storage_base / "results.csv"
    long_extra = "x" * 1000
    file_path.write_text(
        f"symbol,status\nBRCA1,validated,{long_extra}\n",
        encoding="utf-8",
    )
    file_row = SimpleNamespace(
        id=file_id,
        filename="results.csv",
        file_type="csv",
        file_size=file_path.stat().st_size,
        curator_id="user-1",
        session_id="session-1",
        trace_id="a" * 32,
        agent_name="csv_formatter",
        generation_model=None,
        download_count=0,
        last_download_at=None,
        created_at="2026-06-06T00:00:00Z",
        file_metadata={},
        file_path=str(file_path),
    )
    db = _FakeContextDb(row=file_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "FileOutputStorageService",
        lambda: SimpleNamespace(base_path=storage_base),
    )

    response = await supervisor_context_tools.inspect_curation_context(
        scope="file",
        file_id=str(file_id),
        detail="preview",
        limit=1,
    )

    payload = json.loads(response)
    row = payload["results"][0]["rows"][0]
    assert payload["status"] == "ok"
    assert row["symbol"] == "BRCA1"
    assert row["status"] == "validated"
    assert row["_extra_values"][0].endswith("...")
    assert "x" * 600 not in json.dumps(payload)


@pytest.mark.asyncio
async def test_inspect_curation_context_json_file_preview_and_field_are_bounded(
    monkeypatch,
    tmp_path,
):
    file_id = uuid4()
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    file_path = storage_base / "results.json"
    file_path.write_text(
        json.dumps(
            [
                {
                    "gene": "BRCA1",
                    "status": "validated",
                    "note": "x" * 1000,
                },
                {"gene": "TP53", "status": "needs_review"},
            ]
        ),
        encoding="utf-8",
    )
    file_row = SimpleNamespace(
        id=file_id,
        filename="results.json",
        file_type="json",
        file_size=file_path.stat().st_size,
        curator_id="user-1",
        session_id="session-1",
        trace_id="a" * 32,
        agent_name="json_formatter",
        generation_model=None,
        download_count=0,
        last_download_at=None,
        created_at="2026-06-06T00:00:00Z",
        file_metadata={},
        file_path=str(file_path),
    )
    db = _FakeContextDb(row=file_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "FileOutputStorageService",
        lambda: SimpleNamespace(base_path=storage_base),
    )

    preview_response = await supervisor_context_tools.inspect_curation_context(
        scope="file",
        file_id=str(file_id),
        detail="preview",
        limit=1,
    )
    field_response = await supervisor_context_tools.inspect_curation_context(
        scope="file",
        file_id=str(file_id),
        detail="field",
        field_path="0.note",
    )

    preview_payload = json.loads(preview_response)
    field_payload = json.loads(field_response)
    assert preview_payload["status"] == "ok"
    assert preview_payload["results"][0]["preview"][0]["gene"] == "BRCA1"
    assert len(preview_payload["results"][0]["preview"]) == 2
    assert preview_payload["results"][0]["preview"][1]["truncated_count"] == 1
    assert field_payload["results"][0]["value"].endswith("...")
    assert "x" * 600 not in json.dumps(field_payload)


@pytest.mark.asyncio
async def test_inspect_curation_context_malformed_json_file_returns_bounded_error(
    monkeypatch,
    tmp_path,
):
    file_id = uuid4()
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    file_path = storage_base / "broken.json"
    file_path.write_text('{"gene": "BRCA1", "note": "' + ("x" * 1000), encoding="utf-8")
    file_row = SimpleNamespace(
        id=file_id,
        filename="broken.json",
        file_type="json",
        file_size=file_path.stat().st_size,
        curator_id="user-1",
        session_id="session-1",
        trace_id="a" * 32,
        agent_name="json_formatter",
        generation_model=None,
        download_count=0,
        last_download_at=None,
        created_at="2026-06-06T00:00:00Z",
        file_metadata={},
        file_path=str(file_path),
    )
    db = _FakeContextDb(row=file_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "FileOutputStorageService",
        lambda: SimpleNamespace(base_path=storage_base),
    )

    response = await supervisor_context_tools.inspect_curation_context(
        scope="file",
        file_id=str(file_id),
        detail="preview",
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["results"][0]["error"].startswith("Malformed JSON:")
    assert "x" * 600 not in json.dumps(payload)


@pytest.mark.asyncio
async def test_inspect_curation_context_large_json_file_refuses_bounded_preview(
    monkeypatch,
    tmp_path,
):
    file_id = uuid4()
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    file_path = storage_base / "large.json"
    file_path.write_text(
        json.dumps(
            [
                {
                    "gene": "BRCA1",
                    "note": "x" * (supervisor_context_tools._MAX_FILE_PREVIEW_BYTES + 100),
                }
            ]
        ),
        encoding="utf-8",
    )
    file_row = SimpleNamespace(
        id=file_id,
        filename="large.json",
        file_type="json",
        file_size=file_path.stat().st_size,
        curator_id="user-1",
        session_id="session-1",
        trace_id="a" * 32,
        agent_name="json_formatter",
        generation_model=None,
        download_count=0,
        last_download_at=None,
        created_at="2026-06-06T00:00:00Z",
        file_metadata={},
        file_path=str(file_path),
    )
    db = _FakeContextDb(row=file_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "FileOutputStorageService",
        lambda: SimpleNamespace(base_path=storage_base),
    )

    response = await supervisor_context_tools.inspect_curation_context(
        scope="file",
        file_id=str(file_id),
        detail="preview",
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert "too large" in payload["results"][0]["error"]
    assert payload["results"][0]["truncated"] is True
    assert "x" * 600 not in json.dumps(payload)


@pytest.mark.asyncio
async def test_inspect_curation_context_file_preview_rejects_path_escape(
    monkeypatch,
    tmp_path,
):
    file_id = uuid4()
    storage_base = tmp_path / "storage"
    storage_base.mkdir()
    outside_path = tmp_path / "outside.csv"
    outside_path.write_text("secret\nleak\n", encoding="utf-8")
    symlink_path = storage_base / "linked-outside.csv"
    symlink_path.symlink_to(outside_path)
    file_row = SimpleNamespace(
        id=file_id,
        filename="linked-outside.csv",
        file_type="csv",
        file_size=outside_path.stat().st_size,
        curator_id="user-1",
        session_id="session-1",
        trace_id="a" * 32,
        agent_name="csv_formatter",
        generation_model=None,
        download_count=0,
        last_download_at=None,
        created_at="2026-06-06T00:00:00Z",
        file_metadata={},
        file_path=str(symlink_path),
    )
    db = _FakeContextDb(row=file_row)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        supervisor_context_tools,
        "FileOutputStorageService",
        lambda: SimpleNamespace(base_path=storage_base),
    )

    response = await supervisor_context_tools.inspect_curation_context(
        scope="file",
        file_id=str(file_id),
        detail="preview",
    )

    payload = json.loads(response)
    assert payload["status"] == "unavailable"
    assert "secret" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_inspect_curation_context_session_files_preserves_mixed_curator_policy(monkeypatch):
    user_file = SimpleNamespace(
        id=uuid4(),
        filename="mine.csv",
        file_type="csv",
        file_size=12,
        curator_id="user-1",
        session_id="session-1",
        trace_id=None,
        agent_name="csv_formatter",
        generation_model=None,
        download_count=0,
        last_download_at=None,
        created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        file_metadata={},
    )
    other_file = SimpleNamespace(
        id=uuid4(),
        filename="other.csv",
        file_type="csv",
        file_size=12,
        curator_id="user-2",
        session_id="session-1",
        trace_id=None,
        agent_name="csv_formatter",
        generation_model=None,
        download_count=0,
        last_download_at=None,
        created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        file_metadata={},
    )
    db = _FakeSessionFilesDb(user_files=[user_file], other_user_file=other_file)
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)

    response = await supervisor_context_tools.inspect_curation_context(
        scope="session_files",
    )

    payload = json.loads(response)
    assert payload["status"] == "unauthorized_context"
    assert "mixed-curator" in payload["message"]
    assert "mine.csv" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_inspect_curation_context_session_files_authorizes_review_session(monkeypatch):
    session_id = uuid4()
    session_row = SimpleNamespace(
        id=session_id,
        created_by_id="user-1",
        assigned_curator_id=None,
        flow_run_id="flow-run-1",
    )
    user_file = SimpleNamespace(
        id=uuid4(),
        filename="review-output.csv",
        file_type="csv",
        file_size=12,
        curator_id="user-1",
        session_id=str(session_id),
        trace_id=None,
        agent_name="csv_formatter",
        generation_model=None,
        download_count=0,
        last_download_at=None,
        created_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        file_metadata={},
    )
    db = _FakeSessionFilesDb(row=session_row, user_files=[user_file])
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "SessionLocal", lambda: db)

    response = await supervisor_context_tools.inspect_curation_context(
        scope="session_files",
        review_session_id=str(session_id),
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["refs"][0]["review_session_id"] == str(session_id)
    assert payload["results"][0]["filename"] == "review-output.csv"


@pytest.mark.asyncio
async def test_inspect_curation_context_evidence_detail_returns_compact_records(monkeypatch):
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_trace_id", lambda: None)
    monkeypatch.setattr(supervisor_context_tools, "_active_document_id", lambda _user_id: None)
    monkeypatch.setattr(
        supervisor_context_tools,
        "list_extraction_results",
        lambda **_kwargs: [
            _PrepExtractionRecord(
                extraction_result_id="extract-1",
                adapter_key="gene",
                payload_json={
                    "objects": [
                        {
                            "pending_ref_id": "gene-1",
                            "payload": {
                                "mention": "crb",
                                "large_context": "x" * 1000,
                            },
                            "evidence": [
                                {
                                    "evidence_record_id": "evidence-1",
                                    "verified_quote": "crb was identified in the paper.",
                                    "large_payload": {"nested": "y" * 1000},
                                }
                            ],
                        }
                    ],
                    "evidence": [
                        {
                            "evidence_record_id": "evidence-2",
                            "quote": "A second supporting quote.",
                        }
                    ],
                },
            )
        ],
    )

    response = await supervisor_context_tools.inspect_curation_context(
        detail="evidence",
        limit=1,
    )

    payload = json.loads(response)
    evidence = payload["results"][0]["evidence"]
    assert payload["status"] == "ok"
    assert evidence == [
        {
            "evidence_record_id": "evidence-1",
            "verified_quote": "crb was identified in the paper.",
        }
    ]
    assert "large_payload" not in evidence[0]
    assert payload["results"][0]["truncated"] is True
    assert payload["results"][0]["next_cursor"] == "1"


@pytest.mark.asyncio
async def test_inspect_curation_context_evidence_detail_uses_nested_cursor(monkeypatch):
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_trace_id", lambda: None)
    monkeypatch.setattr(supervisor_context_tools, "_active_document_id", lambda _user_id: None)
    monkeypatch.setattr(
        supervisor_context_tools,
        "list_extraction_results",
        lambda **_kwargs: [
            _PrepExtractionRecord(
                extraction_result_id="extract-1",
                adapter_key="gene",
                payload_json={
                    "evidence": [
                        {"evidence_record_id": "evidence-1", "quote": "First quote."},
                        {"evidence_record_id": "evidence-2", "quote": "Second quote."},
                        {"evidence_record_id": "evidence-3", "quote": "Third quote."},
                    ],
                },
            )
        ],
    )

    response = await supervisor_context_tools.inspect_curation_context(
        detail="evidence",
        extraction_result_id="extract-1",
        limit=1,
        cursor="1",
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["next_cursor"] is None
    assert payload["results"][0]["evidence"] == [
        {"evidence_record_id": "evidence-2", "quote": "Second quote."}
    ]
    assert payload["results"][0]["next_cursor"] == "2"


@pytest.mark.asyncio
async def test_inspect_curation_context_current_turn_reads_registry(monkeypatch):
    curation_context_registry.clear_current_turn_curation_context()
    monkeypatch.setattr(supervisor_context_tools, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_context_tools, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        supervisor_context_tools,
        "get_current_trace_id",
        lambda: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    curation_context_registry.register_internal_extraction_event(
        {
            "type": "INTERNAL_EXTRACTION_RESULT",
            "timestamp": "2026-06-06T00:00:00Z",
            "details": {
                "toolName": "ask_gene_extractor_specialist",
                "friendlyName": "Gene: Internal Extraction Result",
            },
            "internal": {
                "canonical_payload": {
                    "domain_pack_id": "gene",
                    "objects": [
                        {
                            "object_type": "gene_mention_evidence",
                            "pending_ref_id": "gene-1",
                            "status": "validated",
                            "payload": {"mention": "crb"},
                        }
                    ],
                },
                "builder_finalization": {"builder_run_id": "builder-1"},
            },
        }
    )

    response = await supervisor_context_tools.inspect_curation_context(
        scope="current_turn",
        detail="objects",
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["refs"][0]["extraction_result_id"] == "current-turn:1"
    assert payload["refs"][0]["builder_run_id"] == "builder-1"
    assert payload["results"][0]["objects"][0]["fields"] == {"mention": "crb"}
    curation_context_registry.clear_current_turn_curation_context()


def test_build_model_settings_applies_reasoning_and_provider_parallel_policy(monkeypatch):
    monkeypatch.setattr("src.lib.openai_agents.config.supports_reasoning", lambda _model: True)
    monkeypatch.setattr("src.lib.openai_agents.config.supports_temperature", lambda _model: False)
    monkeypatch.setattr(
        "src.lib.openai_agents.config.resolve_model_provider",
        lambda _model, _provider_override=None: "openai",
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda _provider: SimpleNamespace(
            driver="openai_native",
            supports_parallel_tool_calls=False,
        ),
    )

    settings = supervisor_agent._build_model_settings(
        model="gpt-5.4-mini",
        temperature=0.7,
        reasoning_effort="high",
    )

    assert settings is not None
    assert settings.temperature is None
    assert settings.reasoning is not None
    assert settings.reasoning.effort == "high"
    assert settings.parallel_tool_calls is False


def test_build_model_settings_returns_none_when_no_overrides(monkeypatch):
    monkeypatch.setattr("src.lib.openai_agents.config.supports_reasoning", lambda _model: False)
    monkeypatch.setattr("src.lib.openai_agents.config.supports_temperature", lambda _model: True)
    monkeypatch.setattr(
        "src.lib.openai_agents.config.resolve_model_provider",
        lambda _model, _provider_override=None: "openai",
    )
    monkeypatch.setattr(
        "src.lib.config.providers_loader.get_provider",
        lambda _provider: SimpleNamespace(supports_parallel_tool_calls=True),
    )

    settings = supervisor_agent._build_model_settings(
        model="gpt-4o",
        temperature=None,
        reasoning_effort=None,
    )

    assert settings is not None
    assert settings.temperature is None
    assert settings.reasoning is None
    assert settings.parallel_tool_calls is True


def test_build_model_settings_raises_for_unknown_provider(monkeypatch):
    monkeypatch.setattr("src.lib.openai_agents.config.supports_reasoning", lambda _model: False)
    monkeypatch.setattr("src.lib.openai_agents.config.supports_temperature", lambda _model: True)
    monkeypatch.setattr(
        "src.lib.openai_agents.config.resolve_model_provider",
        lambda _model, _provider_override=None: "missing-provider",
    )
    monkeypatch.setattr("src.lib.config.providers_loader.get_provider", lambda _provider: None)

    with pytest.raises(ValueError, match="Unknown provider_id"):
        supervisor_agent._build_model_settings(model="gpt-4o")


def test_get_supervisor_specialist_specs_builds_specs_and_skips_metadata_failures(monkeypatch):
    rows = [
        SimpleNamespace(
            agent_key="gene-extractor",
            name="Gene Extractor Agent",
            description="Fallback description",
            supervisor_description="Extract genes from paper text",
            group_rules_enabled=1,
            supervisor_batchable=1,
            supervisor_batching_entity="gene",
        ),
        SimpleNamespace(
            agent_key="broken-specialist",
            name="Broken Specialist",
            description=None,
            supervisor_description=None,
            group_rules_enabled=0,
            supervisor_batchable=0,
            supervisor_batching_entity=None,
        ),
    ]
    session = _FakeSession(rows)

    monkeypatch.setattr("src.models.sql.agent.Agent", _FakeAgentRecord)
    monkeypatch.setattr("src.models.sql.database.SessionLocal", lambda: session)

    def _metadata(agent_key):
        if agent_key == "gene-extractor":
            return {"requires_document": True}
        raise RuntimeError("metadata failure")

    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_agent_metadata", _metadata)

    specs = supervisor_agent._get_supervisor_specialist_specs()

    assert session.closed is True
    assert session.last_query is not None
    assert session.last_query.filtered is True
    assert session.last_query.ordered is True
    assert len(specs) == 1
    assert specs[0]["agent_key"] == "gene-extractor"
    assert specs[0]["tool_name"] == "ask_gene_extractor_specialist"
    assert specs[0]["description"] == "Extract genes from paper text"
    assert specs[0]["requires_document"] is True
    assert specs[0]["group_rules_enabled"] is True
    assert specs[0]["batchable"] is True
    assert specs[0]["batching_entity"] == "gene"


def test_create_dynamic_specialist_tools_skips_document_required_tools_without_document(monkeypatch):
    monkeypatch.setattr(
        supervisor_agent,
        "_get_supervisor_specialist_specs",
        lambda: [
            {
                "tool_name": "ask_pdf_extraction_specialist",
                "agent_key": "pdf_extraction",
                "description": "PDF extraction",
                "requires_document": True,
            }
        ],
    )

    calls = []
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda _agent_key, **_kwargs: calls.append(_kwargs),
    )

    tools = supervisor_agent._create_dynamic_specialist_tools(document_id=None, user_id=None)

    assert tools == []
    assert calls == []


def test_create_dynamic_specialist_tools_passes_document_and_group_context(monkeypatch):
    monkeypatch.setattr(
        supervisor_agent,
        "_get_supervisor_specialist_specs",
        lambda: [
            {
                "tool_name": "ask_gene_expression_specialist",
                "agent_key": "gene-expression",
                "name": "Gene Expression Agent",
                "description": "Extract expression assertions",
                "requires_document": True,
                "group_rules_enabled": True,
            }
        ],
    )

    captured = {}

    def _get_agent_by_id(agent_key, **kwargs):
        captured["agent_key"] = agent_key
        captured["kwargs"] = kwargs
        return SimpleNamespace(name="Gene Expression Agent")

    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_agent_by_id", _get_agent_by_id)
    monkeypatch.setattr(
        supervisor_agent,
        "_create_streaming_tool",
        lambda **kwargs: f"wrapped::{kwargs['tool_name']}::{kwargs['specialist_name']}",
    )

    tools = supervisor_agent._create_dynamic_specialist_tools(
        document_id="doc-1",
        user_id="user-1",
        document_name="paper.pdf",
        sections=["Introduction"],
        hierarchy={"sections": [{"name": "Introduction"}]},
        abstract="abstract text",
        active_groups=["WB"],
    )

    assert captured["agent_key"] == "gene-expression"
    assert captured["kwargs"]["document_id"] == "doc-1"
    assert captured["kwargs"]["user_id"] == "user-1"
    assert captured["kwargs"]["document_name"] == "paper.pdf"
    assert captured["kwargs"]["sections"] == ["Introduction"]
    assert captured["kwargs"]["hierarchy"] == {"sections": [{"name": "Introduction"}]}
    assert captured["kwargs"]["abstract"] == "abstract text"
    assert captured["kwargs"]["active_groups"] == ["WB"]
    assert tools == ["wrapped::ask_gene_expression_specialist::Gene Expression"]


def test_create_dynamic_specialist_tools_continues_after_agent_construction_failure(monkeypatch):
    monkeypatch.setattr(
        supervisor_agent,
        "_get_supervisor_specialist_specs",
        lambda: [
            {
                "tool_name": "ask_bad_specialist",
                "agent_key": "bad",
                "description": "Bad specialist",
                "requires_document": False,
            },
            {
                "tool_name": "ask_good_specialist",
                "agent_key": "good",
                "name": "Good Agent",
                "description": "Good specialist",
                "requires_document": False,
            },
        ],
    )

    def _get_agent_by_id(agent_key, **_kwargs):
        if agent_key == "bad":
            raise RuntimeError("cannot build bad agent")
        return SimpleNamespace(name="Good Agent")

    monkeypatch.setattr("src.lib.agent_studio.catalog_service.get_agent_by_id", _get_agent_by_id)
    monkeypatch.setattr(
        supervisor_agent,
        "_create_streaming_tool",
        lambda **kwargs: f"wrapped::{kwargs['tool_name']}",
    )

    tools = supervisor_agent._create_dynamic_specialist_tools()
    assert tools == ["wrapped::ask_good_specialist"]


def test_fetch_document_sections_sync_uses_asyncio_run_without_running_loop(monkeypatch):
    import asyncio

    async def _fake_get_document_sections(_document_id, _user_id):
        return [{"name": "intro"}]

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_document_sections",
        _fake_get_document_sections,
    )
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError()))

    def _fake_run(coro):
        coro.close()
        return [{"name": "intro"}]

    monkeypatch.setattr(asyncio, "run", _fake_run)

    sections = supervisor_agent._fetch_document_sections_sync("doc-1", "user-1")
    assert sections == [{"name": "intro"}]


def test_fetch_document_sections_sync_uses_threadpool_when_loop_running(monkeypatch):
    import asyncio
    import concurrent.futures

    async def _fake_get_document_sections(_document_id, _user_id):
        return [{"name": "methods"}]

    class _FakeFuture:
        def __init__(self, coro):
            self._coro = coro

        def result(self, timeout=None):
            assert timeout == 10
            self._coro.close()
            return [{"name": "methods"}]

    class _FakePool:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, coro):
            assert fn is asyncio.run
            return _FakeFuture(coro)

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_document_sections",
        _fake_get_document_sections,
    )
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: object())
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", lambda: _FakePool())

    sections = supervisor_agent._fetch_document_sections_sync("doc-1", "user-1")
    assert sections == [{"name": "methods"}]


def test_fetch_document_sections_sync_returns_empty_on_exception(monkeypatch):
    import asyncio

    async def _fake_get_document_sections(_document_id, _user_id):
        return [{"name": "ignored"}]

    def _failing_run(coro):
        coro.close()
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_document_sections",
        _fake_get_document_sections,
    )
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(asyncio, "run", _failing_run)

    assert supervisor_agent._fetch_document_sections_sync("doc-1", "user-1") == []


def test_fetch_document_hierarchy_sync_returns_none_on_exception(monkeypatch):
    import asyncio

    async def _fake_get_hierarchy(_document_id, _user_id):
        return {"sections": [{"name": "ignored"}]}

    def _failing_run(coro):
        coro.close()
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.lib.weaviate_client.chunks.get_document_sections_hierarchical",
        _fake_get_hierarchy,
    )
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(asyncio, "run", _failing_run)

    assert supervisor_agent.fetch_document_hierarchy_sync("doc-1", "user-1") is None


def test_create_supervisor_agent_without_document_adds_unavailable_note(monkeypatch):
    captured_agent = {}
    captured_pending = {}
    captured_langfuse = {}

    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_agent_config",
        lambda _name: SimpleNamespace(model="gpt-4o", temperature=None, reasoning=None),
    )
    monkeypatch.setattr("src.lib.openai_agents.config.log_agent_config", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.openai_agents.config.resolve_model_provider", lambda _model: "openai")
    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_model_for_agent",
        lambda model, provider_override=None: model,
    )
    monkeypatch.setattr(supervisor_agent, "_build_model_settings", lambda **_kwargs: None)
    monkeypatch.setattr(
        supervisor_agent,
        "_create_dynamic_specialist_tools",
        lambda **_kwargs: [SimpleNamespace(name="ask_gene_specialist")],
    )
    monkeypatch.setattr(
        supervisor_agent,
        "_get_supervisor_specialist_specs",
        lambda: [
            {"tool_name": "ask_gene_specialist", "requires_document": False},
            {"tool_name": "ask_pdf_extraction_specialist", "requires_document": True},
        ],
    )
    _patch_supervisor_prompt_bundle(monkeypatch, version=7)
    monkeypatch.setattr(
        supervisor_agent,
        "set_pending_prompts",
        lambda name, prompts, **kwargs: captured_pending.update(
            {"name": name, "prompts": prompts, "kwargs": kwargs}
        ),
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.langfuse_client.log_agent_config",
        lambda **kwargs: captured_langfuse.update(kwargs),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "function_tool",
        lambda **decorator_kwargs: (
            lambda fn: (
                setattr(fn, "name", decorator_kwargs.get("name_override", fn.__name__)),
                setattr(fn, "description", decorator_kwargs.get("description_override", "")),
                fn,
            )[2]
        ),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "Agent",
        lambda **kwargs: captured_agent.update(kwargs) or SimpleNamespace(**kwargs),
    )

    created = supervisor_agent.create_supervisor_agent(document_id=None, user_id=None)

    assert "Only these specialist tools are currently installed" in created.instructions
    assert "ask_gene_specialist" in created.instructions
    assert "No PDF document is currently loaded" in created.instructions
    assert "ask_pdf_extraction_specialist" in created.instructions
    assert supervisor_agent.CURATION_PREP_CONFIRMATION_QUESTION in created.instructions
    assert any(getattr(tool, "name", "") == "prepare_for_curation" for tool in created.tools)
    assert any(getattr(tool, "name", "") == "export_to_file" for tool in created.tools)
    assert captured_pending["name"] == "Query Supervisor"
    assert captured_langfuse["metadata"]["specialist_count"] == len(created.tools)


@pytest.mark.asyncio
async def test_ordinary_non_flow_export_to_file_uses_standard_csv_save_tool(monkeypatch):
    captured_save = {}

    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_agent_config",
        lambda _name: SimpleNamespace(model="gpt-4o", temperature=None, reasoning=None),
    )
    monkeypatch.setattr("src.lib.openai_agents.config.log_agent_config", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.openai_agents.config.resolve_model_provider", lambda _model: "openai")
    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_model_for_agent",
        lambda model, provider_override=None: model,
    )
    monkeypatch.setattr(supervisor_agent, "_build_model_settings", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor_agent, "_get_supervisor_specialist_specs", lambda: [])
    _patch_supervisor_prompt_bundle(monkeypatch, version=17)
    monkeypatch.setattr(supervisor_agent, "set_pending_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "src.lib.openai_agents.langfuse_client.log_agent_config",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        supervisor_agent,
        "function_tool",
        lambda **decorator_kwargs: (
            lambda fn: (
                setattr(fn, "name", decorator_kwargs.get("name_override", fn.__name__)),
                setattr(fn, "description", decorator_kwargs.get("description_override", "")),
                fn,
            )[2]
        ),
    )
    monkeypatch.setattr(supervisor_agent, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))

    async def _fake_save_csv_impl(data_json, filename, columns=None):
        captured_save.update(
            {
                "data_json": data_json,
                "filename": filename,
                "columns": columns,
            }
        )
        return {
            "file_id": "file-chat-csv",
            "filename": "chat_export.csv",
            "format": "csv",
            "download_url": "/api/files/file-chat-csv/download",
        }

    monkeypatch.setattr(
        "src.lib.openai_agents.tools.file_output_tools._save_csv_impl",
        _fake_save_csv_impl,
    )

    created = supervisor_agent.create_supervisor_agent(document_id=None, user_id=None)
    export_tool = next(
        tool
        for tool in created.tools
        if getattr(tool, "name", "") == "export_to_file"
    )

    response = await export_tool(
        format_type="csv",
        data='[{"gene":"BRCA1","status":"validated"}]',
        filename_hint="chat_export",
    )

    payload = json.loads(response)
    assert captured_save == {
        "data_json": '[{"gene":"BRCA1","status":"validated"}]',
        "filename": "chat_export",
        "columns": None,
    }
    assert payload["file_id"] == "file-chat-csv"
    assert payload["download_url"].endswith("/download")


def test_create_supervisor_agent_with_zero_specialists_enables_core_only_mode(monkeypatch):
    captured_langfuse = {}

    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_agent_config",
        lambda _name: SimpleNamespace(model="gpt-4o", temperature=None, reasoning=None),
    )
    monkeypatch.setattr("src.lib.openai_agents.config.log_agent_config", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.openai_agents.config.resolve_model_provider", lambda _model: "openai")
    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_model_for_agent",
        lambda model, provider_override=None: model,
    )
    monkeypatch.setattr(supervisor_agent, "_build_model_settings", lambda **_kwargs: None)
    monkeypatch.setattr(supervisor_agent, "_get_supervisor_specialist_specs", lambda: [])
    _patch_supervisor_prompt_bundle(monkeypatch, version=11)
    monkeypatch.setattr(supervisor_agent, "set_pending_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "src.lib.openai_agents.langfuse_client.log_agent_config",
        lambda **kwargs: captured_langfuse.update(kwargs),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "function_tool",
        lambda **decorator_kwargs: (
            lambda fn: (
                setattr(fn, "name", decorator_kwargs.get("name_override", fn.__name__)),
                setattr(fn, "description", decorator_kwargs.get("description_override", "")),
                fn,
            )[2]
        ),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "Agent",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    created = supervisor_agent.create_supervisor_agent(document_id=None, user_id=None)

    assert "CORE-ONLY MODE" in created.instructions
    assert "No domain specialist tools are currently installed" in created.instructions
    assert [getattr(tool, "name", "") for tool in created.tools] == [
        "prepare_for_curation",
        "inspect_results",
        "inspect_chat_traces",
        "export_to_file",
    ]
    inspect_tool = next(
        tool
        for tool in created.tools
        if getattr(tool, "name", "") == "inspect_results"
    )
    inspect_params = inspect.signature(inspect_tool).parameters
    assert "action" in inspect_params
    assert "result_ref" in inspect_params
    assert "object_ref" in inspect_params
    assert "review_session_id" not in inspect_params
    assert "file_id" not in inspect_params
    tools_by_name = {getattr(tool, "name", ""): tool for tool in created.tools}
    assert (
        "persisted canonical extraction results"
        in tools_by_name["prepare_for_curation"].description
    )
    assert (
        "use inspect_results for persisted extraction objects"
        in tools_by_name["inspect_chat_traces"].description
    )
    assert (
        "Use only when the user explicitly asks"
        in tools_by_name["export_to_file"].description
    )
    assert captured_langfuse["metadata"]["specialist_count"] == 4


def test_create_supervisor_agent_with_document_extracts_sections_and_enables_guardrails(monkeypatch):
    captured_dynamic = {}

    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_agent_config",
        lambda _name: SimpleNamespace(model="gpt-4o", temperature=0.0, reasoning="low"),
    )
    monkeypatch.setattr("src.lib.openai_agents.config.log_agent_config", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.openai_agents.config.resolve_model_provider", lambda _model: "openai")
    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_model_for_agent",
        lambda model, provider_override=None: model,
    )
    monkeypatch.setattr(supervisor_agent, "_build_model_settings", lambda **_kwargs: None)
    monkeypatch.setattr(
        supervisor_agent,
        "_get_supervisor_specialist_specs",
        lambda: [{"tool_name": "ask_pdf_extraction_specialist", "requires_document": True}],
    )
    monkeypatch.setattr(
        supervisor_agent,
        "_create_dynamic_specialist_tools",
        lambda **kwargs: captured_dynamic.update(kwargs) or [SimpleNamespace(name="ask_pdf_extraction_specialist")],
    )
    _patch_supervisor_prompt_bundle(monkeypatch, version=9)
    monkeypatch.setattr(supervisor_agent, "set_pending_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.openai_agents.langfuse_client.log_agent_config", lambda **_kwargs: None)
    monkeypatch.setattr(
        supervisor_agent,
        "function_tool",
        lambda **decorator_kwargs: (
            lambda fn: (
                setattr(fn, "name", decorator_kwargs.get("name_override", fn.__name__)),
                fn,
            )[1]
        ),
    )
    monkeypatch.setattr(supervisor_agent, "safety_guardrail", "safety")
    monkeypatch.setattr(supervisor_agent, "GUARDRAILS_AVAILABLE", True)
    monkeypatch.setattr(
        supervisor_agent,
        "Agent",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    created = supervisor_agent.create_supervisor_agent(
        document_id="doc-2",
        user_id="user-2",
        hierarchy={"sections": [{"name": "Introduction"}, {"name": "Methods"}]},
        enable_guardrails=True,
    )

    assert "DOCUMENT CONTEXT: A PDF document is loaded." in created.instructions
    assert "RUNTIME TOOL DESCRIPTIONS ARE AUTHORITATIVE" in created.instructions
    assert created.input_guardrails == ["safety"]
    assert captured_dynamic["sections"] == ["Introduction", "Methods"]


def test_create_supervisor_agent_applies_model_overrides(monkeypatch):
    captured_dynamic = {}

    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_agent_config",
        lambda _name: SimpleNamespace(model="gpt-5.5", temperature=0.1, reasoning="medium"),
    )
    monkeypatch.setattr("src.lib.openai_agents.config.log_agent_config", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.openai_agents.config.resolve_model_provider", lambda _model: "openai")
    monkeypatch.setattr(
        "src.lib.openai_agents.config.get_model_for_agent",
        lambda model, provider_override=None: model,
    )
    monkeypatch.setattr(supervisor_agent, "_build_model_settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(supervisor_agent, "_get_supervisor_specialist_specs", lambda: [])
    monkeypatch.setattr(
        supervisor_agent,
        "_create_dynamic_specialist_tools",
        lambda **kwargs: captured_dynamic.update(kwargs) or [],
    )
    _patch_supervisor_prompt_bundle(monkeypatch, version=12)
    monkeypatch.setattr(supervisor_agent, "set_pending_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr("src.lib.openai_agents.langfuse_client.log_agent_config", lambda **_kwargs: None)
    monkeypatch.setattr(
        supervisor_agent,
        "function_tool",
        lambda **decorator_kwargs: (
            lambda fn: (
                setattr(fn, "name", decorator_kwargs.get("name_override", fn.__name__)),
                fn,
            )[1]
        ),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "Agent",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    created = supervisor_agent.create_supervisor_agent(
        document_id="doc-override",
        user_id="user-override",
        model_override="gpt-5.4-mini",
        temperature_override=0.0,
        reasoning_override="minimal",
        specialist_model_override="gpt-5.4-mini",
        specialist_temperature_override=0.0,
        specialist_reasoning_override="minimal",
    )

    assert created.model == "gpt-5.4-mini"
    assert created.model_settings["model"] == "gpt-5.4-mini"
    assert created.model_settings["temperature"] == 0.0
    assert created.model_settings["reasoning_effort"] == "minimal"
    assert captured_dynamic["specialist_model_override"] == "gpt-5.4-mini"
    assert captured_dynamic["specialist_temperature_override"] == 0.0
    assert captured_dynamic["specialist_reasoning_override"] == "minimal"


def test_is_explicit_curation_prep_confirmation_rejects_not_ready():
    assert supervisor_agent._is_explicit_curation_prep_confirmation("not ready") is False


def test_filter_extraction_results_for_scope_excludes_unscoped_records_when_scope_confirmed():
    matching_record = _PrepExtractionRecord(
        extraction_result_id="extract-1",
        adapter_key="reference_adapter",
        domain_key="disease",
    )
    unscoped_record = _PrepExtractionRecord(
        extraction_result_id="extract-2",
        adapter_key=None,
        profile_key=None,
        domain_key=None,
    )

    scoped_results, notes = supervisor_agent._filter_extraction_results_for_scope(
        [matching_record, unscoped_record],
        {
            "adapter_keys": ["reference_adapter"],
        },
    )

    assert [record.extraction_result_id for record in scoped_results] == ["extract-1"]
    assert notes == []


def test_filter_extraction_results_for_scope_does_not_fall_back_to_unscoped_records():
    scoped_results, notes = supervisor_agent._filter_extraction_results_for_scope(
        [
            _PrepExtractionRecord(
                extraction_result_id="extract-1",
                adapter_key=None,
                profile_key=None,
                domain_key=None,
            ),
            _PrepExtractionRecord(
                extraction_result_id="extract-2",
                adapter_key=None,
                profile_key=None,
                domain_key=None,
            ),
        ],
        {
            "adapter_keys": ["reference_adapter"],
        },
    )

    assert scoped_results == []
    assert notes == []


@pytest.mark.asyncio
async def test_dispatch_curation_prep_requires_prior_confirmation_prompt(monkeypatch):
    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: None),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "latest_assistant_message_for_session",
        lambda **_kwargs: "I can help with that.",
    )
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda *_args, **_kwargs: pytest.fail("extraction lookup should not run without checkpoint"),
    )

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, please prepare them.",
    )

    payload = json.loads(response)
    assert payload["status"] == "confirmation_required"
    assert "Ready to prepare these for curation?" in payload["message"]


@pytest.mark.asyncio
async def test_dispatch_curation_prep_runs_deterministic_prep_with_confirmed_scope(monkeypatch):
    captured = {}

    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_agent, "get_current_trace_id", lambda: "trace-1")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: None),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "latest_assistant_message_for_session",
        lambda **_kwargs: "Ready to prepare these for curation?",
    )
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda *_args, **_kwargs: [_PrepExtractionRecord()],
    )
    async def _fake_run_curation_prep(
        extraction_results,
        *,
        scope_confirmation,
        persistence_context=None,
        db=None,
    ):
        captured["extraction_results"] = extraction_results
        captured["scope_confirmation"] = scope_confirmation
        captured["persistence_context"] = persistence_context
        captured["db"] = db
        return SimpleNamespace(
            candidates=[],
            envelope_refs=[SimpleNamespace(review_row_count=1)],
            review_row_count=1,
            run_metadata=SimpleNamespace(
                warnings=[],
                processing_notes=["Prepared from confirmed chat extraction context."],
            ),
        )

    monkeypatch.setattr(supervisor_agent, "run_curation_prep", _fake_run_curation_prep)

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, prepare the confirmed disease findings.",
        scope_summary="Disease findings for APOE.",
        adapter_keys=["reference_adapter"],
    )

    payload = json.loads(response)
    assert payload["status"] == "prepared"
    assert payload["candidate_count"] == 1
    assert payload["document_id"] == "document-1"
    assert payload["processing_notes"] == ["Prepared from confirmed chat extraction context."]
    assert len(captured["extraction_results"]) == 1
    assert captured["scope_confirmation"].confirmed is True
    assert captured["scope_confirmation"].adapter_keys == ["reference_adapter"]
    assert any(
        "Disease findings for APOE." in note
        for note in captured["scope_confirmation"].notes
    )
    assert any(
        "Yes, prepare the confirmed disease findings." in note
        for note in captured["scope_confirmation"].notes
    )
    assert captured["persistence_context"].origin_session_id == "session-1"
    assert captured["persistence_context"].trace_id == "trace-1"
    assert captured["persistence_context"].user_id == "user-1"


@pytest.mark.asyncio
async def test_dispatch_curation_prep_reports_envelope_review_row_count(monkeypatch):
    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_agent, "get_current_trace_id", lambda: "trace-1")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: None),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "latest_assistant_message_for_session",
        lambda **_kwargs: "Ready to prepare these for curation?",
    )
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda *_args, **_kwargs: [_PrepExtractionRecord(adapter_key="gene")],
    )

    async def _fake_run_curation_prep(*_args, **_kwargs):
        return SimpleNamespace(
            candidates=[],
            envelope_refs=[SimpleNamespace(review_row_count=2)],
            review_row_count=2,
            run_metadata=SimpleNamespace(
                warnings=[],
                processing_notes=["Prepared persisted envelope review rows."],
            ),
        )

    monkeypatch.setattr(supervisor_agent, "run_curation_prep", _fake_run_curation_prep)

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, prepare the confirmed gene findings.",
        adapter_keys=["gene"],
    )

    payload = json.loads(response)
    assert payload["status"] == "prepared"
    assert payload["candidate_count"] == 2
    assert payload["message"] == "Prepared 2 candidate annotations for curation review."
    assert payload["processing_notes"] == ["Prepared persisted envelope review rows."]


@pytest.mark.asyncio
async def test_dispatch_curation_prep_rejects_ambiguous_scope(monkeypatch):
    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: None),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "latest_assistant_message_for_session",
        lambda **_kwargs: "Ready to prepare these for curation?",
    )
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda *_args, **_kwargs: [
            _PrepExtractionRecord(adapter_key="reference_adapter", domain_key="disease"),
            _PrepExtractionRecord(
                extraction_result_id="extract-2",
                adapter_key="gene_expression",
                domain_key="gene_expression",
                payload_json={"run_summary": {"candidate_count": 1}},
            ),
        ],
    )

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, do it.",
    )

    payload = json.loads(response)
    assert payload["status"] == "scope_confirmation_required"
    assert payload["available_scope"]["adapter_keys"] == ["reference_adapter", "gene_expression"]


@pytest.mark.asyncio
async def test_dispatch_curation_prep_still_filters_loaded_document_before_running(monkeypatch):
    captured = {}

    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(supervisor_agent, "get_current_trace_id", lambda: "trace-2")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: {"id": "document-2"}),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "latest_assistant_message_for_session",
        lambda **_kwargs: "Ready to prepare these for curation?",
    )

    def _fake_list_extraction_results(*_args, **kwargs):
        captured["query_kwargs"] = kwargs
        assert kwargs["document_id"] == "document-2"
        return [
            _PrepExtractionRecord(
                extraction_result_id="extract-2",
                document_id="document-2",
                adapter_key="disease",
                domain_key="disease",
            )
        ]

    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        _fake_list_extraction_results,
    )
    async def _fake_run_curation_prep(
        extraction_results,
        *,
        scope_confirmation,
        persistence_context=None,
        db=None,
    ):
        captured["run_scope_confirmation"] = scope_confirmation
        captured["run_persistence_context"] = persistence_context
        return SimpleNamespace(
            candidates=[],
            envelope_refs=[SimpleNamespace(review_row_count=1)],
            review_row_count=1,
            run_metadata=SimpleNamespace(warnings=[], processing_notes=[]),
        )

    monkeypatch.setattr(supervisor_agent, "run_curation_prep", _fake_run_curation_prep)

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, prepare the disease findings in the loaded document.",
        adapter_keys=["disease"],
    )

    payload = json.loads(response)
    assert payload["status"] == "prepared"
    assert captured["query_kwargs"]["document_id"] == "document-2"
    assert captured["run_scope_confirmation"].adapter_keys == ["disease"]
    assert captured["run_persistence_context"].document_id == "document-2"
    assert captured["run_persistence_context"].trace_id == "trace-2"


@pytest.mark.asyncio
async def test_dispatch_curation_prep_does_not_fall_back_to_top_level_evidence_records(monkeypatch):
    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: None),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "latest_assistant_message_for_session",
        lambda **_kwargs: "Ready to prepare these for curation?",
    )
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda *_args, **_kwargs: [
            _PrepExtractionRecord(
                payload_json={
                    "items": [{"label": "APOE", "entity_type": "gene", "evidence": []}],
                    "evidence_records": [
                        {
                            "verified_quote": "APOE was associated with the disease phenotype.",
                            "page": 3,
                            "section": "Results",
                            "subsection": "Disease association",
                            "chunk_id": "chunk-apoe-1",
                        }
                    ],
                    "run_summary": {"candidate_count": 1},
                }
            )
        ],
    )

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, prepare them.",
        adapter_keys=["reference_adapter"],
    )

    payload = json.loads(response)
    assert payload["status"] == "unable_to_prepare"
    assert "No evidence-verified candidates were available" in payload["message"]


@pytest.mark.asyncio
async def test_dispatch_curation_prep_requires_document_narrowing_for_multi_document_session(monkeypatch):
    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        supervisor_agent,
        "document_state",
        SimpleNamespace(get_document=lambda _user_id: None),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "latest_assistant_message_for_session",
        lambda **_kwargs: "Ready to prepare these for curation?",
    )
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda *_args, **_kwargs: [
            _PrepExtractionRecord(
                extraction_result_id="extract-1",
                document_id="document-1",
                adapter_key="disease",
                domain_key="disease",
            ),
            _PrepExtractionRecord(
                extraction_result_id="extract-2",
                document_id="document-2",
                adapter_key="disease",
                domain_key="disease",
            ),
        ],
    )

    response = await supervisor_agent._dispatch_curation_prep_from_chat_context(
        user_confirmation="Yes, prepare them.",
    )

    payload = json.loads(response)
    assert payload["status"] == "scope_confirmation_required"
    assert payload["available_document_ids"] == ["document-1", "document-2"]
    assert "multiple documents" in payload["message"]
