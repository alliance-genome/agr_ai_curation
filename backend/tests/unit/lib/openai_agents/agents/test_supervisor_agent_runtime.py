"""Runtime-focused tests for supervisor agent helpers."""

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from src.lib.chat_history_repository import ChatMessageRecord
from src.lib.openai_agents import supervisor_context_tools
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
    assert "inspect_results(action=\"search\"" in prompt_text
    assert "extraction-result:<uuid>" in prompt_text
    assert (
        "Do not silently export a different result than the curator requested."
        in normalized_prompt
    )
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
            return {"requires_document": True, "category": "Extraction"}
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
    assert specs[0]["category"] == "Extraction"


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


def test_dynamic_tools_limit_current_request_to_extraction_specialists(monkeypatch):
    wrapped = {}

    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key, **_kwargs: SimpleNamespace(name=agent_key),
    )

    def _capture_streaming_tool(**kwargs):
        wrapped[kwargs["tool_name"]] = kwargs["authoritative_user_request"]
        return kwargs["tool_name"]

    monkeypatch.setattr(
        supervisor_agent,
        "_create_streaming_tool",
        _capture_streaming_tool,
    )

    request = "Use only curator-supplied tumor terms."
    tools = supervisor_agent._create_dynamic_specialist_tools(
        tool_specs=[
            {
                "tool_name": "ask_pdf_extraction_specialist",
                "agent_key": "pdf_extraction",
                "description": "Extract from the PDF",
                "requires_document": False,
                "category": "Extraction",
            },
            {
                "tool_name": "ask_gene_validation_specialist",
                "agent_key": "gene_validation",
                "description": "Validate a gene",
                "requires_document": False,
                "category": "Validation",
            },
        ],
        authoritative_user_request=request,
    )

    # Regression guard: forwarding every long chat prompt to unrelated lookup or
    # validation agents would broaden scope and waste their isolated context window.
    assert tools == [
        "ask_pdf_extraction_specialist",
        "ask_gene_validation_specialist",
    ]
    assert wrapped["ask_pdf_extraction_specialist"] == request
    assert wrapped["ask_gene_validation_specialist"] is None


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
    assert not any(getattr(tool, "name", "") == "export_to_file" for tool in created.tools)
    assert captured_pending["name"] == "Query Supervisor"
    assert captured_langfuse["metadata"]["specialist_count"] == len(created.tools)


@pytest.mark.asyncio
async def test_dynamic_formatter_specialist_binds_bundle_at_call_time(monkeypatch):
    captured_agent = {}
    captured_run = {}
    fake_bundle = SimpleNamespace(flow_name="Chat Extraction Results")
    fake_agent = SimpleNamespace(name="CSV File Formatter")

    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda agent_key, **kwargs: captured_agent.update(
            {"agent_key": agent_key, "kwargs": kwargs}
        )
        or fake_agent,
    )
    monkeypatch.setattr(
        supervisor_agent,
        "_build_chat_formatter_bundle",
        lambda **_kwargs: (
            fake_bundle,
            "FORMATTER SOURCE BUNDLE:\nlatest",
            "",
        ),
    )

    async def _fake_run_streaming_specialist_tool(**kwargs):
        captured_run.update(kwargs)
        return "formatter finished"

    monkeypatch.setattr(
        supervisor_agent,
        "_run_streaming_specialist_tool",
        _fake_run_streaming_specialist_tool,
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

    tools = supervisor_agent._create_dynamic_specialist_tools(
        tool_specs=[
            {
                "agent_key": "csv_formatter",
                "name": "CSV File Formatter",
                "description": "Create a CSV file",
                "tool_name": "ask_csv_formatter_specialist",
                "requires_document": False,
                "group_rules_enabled": False,
            }
        ],
        formatter_bundle=None,
        authoritative_user_request="Use only the supplied tumor vocabulary.",
    )

    assert [getattr(tool, "name", "") for tool in tools] == [
        "ask_csv_formatter_specialist"
    ]
    assert captured_agent == {}

    result = await tools[0](SimpleNamespace(run_config="run-config"), "Create a CSV")

    assert result == "formatter finished"
    assert captured_agent["agent_key"] == "csv_formatter"
    assert captured_agent["kwargs"]["formatter_bundle"] is fake_bundle
    assert captured_agent["kwargs"]["formatter_output_format"] == "csv"
    assert captured_agent["kwargs"]["formatter_agent_id"] == "csv_formatter"
    assert captured_agent["kwargs"]["additional_runtime_context"] == [
        "FORMATTER SOURCE BUNDLE:\nlatest"
    ]
    assert captured_run["agent"] is fake_agent
    assert captured_run["tool_name"] == "ask_csv_formatter_specialist"
    assert captured_run["specialist_name"] == "CSV File Formatter"
    assert captured_run["query"] == "Create a CSV"
    # The formatter needs the same exact vocabulary as the extractor; otherwise it
    # can reject or remap source objects using only a supervisor-authored summary.
    assert captured_run["authoritative_user_request"] == (
        "Use only the supplied tumor vocabulary."
    )
    assert captured_run["ctx"].run_config == "run-config"


@pytest.mark.asyncio
async def test_dynamic_formatter_specialist_reports_unavailable_without_bundle(monkeypatch):
    def _unexpected_get_agent(*_args, **_kwargs):
        raise AssertionError("formatter agent must not be constructed before a bundle exists")

    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        _unexpected_get_agent,
    )
    monkeypatch.setattr(
        supervisor_agent,
        "_build_chat_formatter_bundle",
        lambda **_kwargs: (None, "", "No saved extraction results are available yet."),
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

    tools = supervisor_agent._create_dynamic_specialist_tools(
        tool_specs=[
            {
                "agent_key": "csv_formatter",
                "name": "CSV File Formatter",
                "description": "Create a CSV file",
                "tool_name": "ask_csv_formatter_specialist",
                "requires_document": False,
                "group_rules_enabled": False,
            }
        ],
        formatter_bundle=None,
    )

    assert [getattr(tool, "name", "") for tool in tools] == [
        "ask_csv_formatter_specialist"
    ]
    response = json.loads(await tools[0](SimpleNamespace(run_config=None), "Create a CSV"))
    assert response["status"] == "unavailable"
    assert response["message"] == "No saved extraction results are available yet."


def test_create_supervisor_agent_exposes_formatter_with_saved_chat_bundle(monkeypatch):
    captured_bundle: dict[str, Any] = {}
    captured_langfuse: dict[str, Any] = {}
    fake_bundle = SimpleNamespace(flow_name="Chat Extraction Results")
    fake_record = SimpleNamespace(
        extraction_result_id="00000000-0000-4000-8000-000000000123",
        created_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        agent_key="generic_extractor",
        adapter_key="generic",
        source_kind=SimpleNamespace(value="chat"),
        candidate_count=9,
        document_id="doc-1",
        flow_run_id=None,
    )

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
        "_get_supervisor_specialist_specs",
        lambda: [
            {
                "agent_key": "csv_formatter",
                "name": "CSV File Formatter",
                "description": "Create a CSV file",
                "tool_name": "ask_csv_formatter_specialist",
                "requires_document": False,
                "group_rules_enabled": False,
            }
        ],
    )
    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        lambda **kwargs: [fake_record]
        if str(kwargs.get("source_kind")) == "CurationExtractionSourceKind.CHAT"
        or getattr(kwargs.get("source_kind"), "value", None) == "chat"
        else [],
    )

    def _fake_build_bundle(**kwargs):
        captured_bundle.update(kwargs)
        return fake_bundle

    monkeypatch.setattr(
        "src.lib.flows.output_projection.build_extraction_result_artifact_bundle",
        _fake_build_bundle,
    )
    monkeypatch.setattr(
        "src.lib.agent_studio.catalog_service.get_agent_by_id",
        lambda *_args, **_kwargs: SimpleNamespace(name="CSV File Formatter"),
    )
    monkeypatch.setattr(
        supervisor_agent,
        "_create_streaming_tool",
        lambda **kwargs: SimpleNamespace(name=kwargs["tool_name"]),
    )
    _patch_supervisor_prompt_bundle(monkeypatch, version=18)
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
    monkeypatch.setattr(supervisor_agent, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))

    created = supervisor_agent.create_supervisor_agent(user_id="user-1", document_id="doc-1")

    tool_names = [getattr(tool, "name", "") for tool in created.tools]
    assert "ask_csv_formatter_specialist" in tool_names
    assert "export_to_file" not in tool_names
    assert "EXPORT/DOWNLOAD ROUTING" in created.instructions
    assert "including results saved earlier in this same supervisor turn" in created.instructions
    assert "select only a result_ref listed in the runtime formatter bundle" in created.instructions
    assert "pass that exact source_ref into projection planning" in created.instructions
    assert "Formatter specialists are the only supported export path" in created.instructions
    assert captured_bundle["extraction_results"] == [fake_record]
    assert captured_langfuse["metadata"]["specialist_count"] == len(created.tools)


def test_build_formatter_bundle_uses_only_active_session_document_results(monkeypatch):
    captured_bundle: dict[str, Any] = {}
    list_calls: list[dict[str, Any]] = []
    fake_bundle = SimpleNamespace(flow_name="Chat Extraction Results")
    chat_record = SimpleNamespace(
        extraction_result_id="00000000-0000-4000-8000-000000000123",
        created_at=datetime(2026, 6, 17, tzinfo=timezone.utc),
        agent_key="generic_extractor",
        adapter_key="generic",
        source_kind=SimpleNamespace(value="chat"),
        candidate_count=9,
        document_id="doc-1",
        flow_run_id=None,
    )
    document_record = SimpleNamespace(
        extraction_result_id="00000000-0000-4000-8000-000000000456",
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        agent_key="generic_extractor",
        adapter_key="generic",
        source_kind=SimpleNamespace(value="chat"),
        candidate_count=3,
        document_id="doc-1",
        flow_run_id=None,
    )
    flow_record = SimpleNamespace(
        extraction_result_id="00000000-0000-4000-8000-000000000789",
        created_at=datetime(2026, 6, 16, tzinfo=timezone.utc),
        agent_key="flow_extractor",
        adapter_key="generic",
        source_kind=SimpleNamespace(value="flow"),
        candidate_count=2,
        document_id="doc-1",
        flow_run_id="flow-run-1",
    )

    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")

    def _fake_list_extraction_results(**kwargs):
        list_calls.append(kwargs)
        source_kind = kwargs.get("source_kind")
        if getattr(source_kind, "value", None) == "chat":
            return [chat_record]
        if getattr(source_kind, "value", None) == "flow":
            return [flow_record]
        if source_kind is not None:
            return []
        if kwargs.get("document_id") == "doc-1":
            return [document_record, chat_record]
        return []

    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        _fake_list_extraction_results,
    )

    def _fake_build_bundle(**kwargs):
        captured_bundle.update(kwargs)
        return fake_bundle

    monkeypatch.setattr(
        "src.lib.flows.output_projection.build_extraction_result_artifact_bundle",
        _fake_build_bundle,
    )
    bundle, runtime_context, unavailable_note = supervisor_agent._build_chat_formatter_bundle(
        user_id="user-1",
        document_id="doc-1",
    )

    assert bundle is fake_bundle
    assert bundle is not None
    assert unavailable_note == ""
    assert captured_bundle["extraction_results"] == [chat_record, flow_record]
    assert captured_bundle["document_id"] == "doc-1"
    assert bundle.default_source_extraction_result_id == chat_record.extraction_result_id
    assert len(list_calls) == 2
    assert all(call["origin_session_id"] == "session-1" for call in list_calls)
    assert all(call["document_id"] == "doc-1" for call in list_calls)
    assert all(call["user_id"] == "user-1" for call in list_calls)
    assert all(
        call["exclude_agent_keys"] == (supervisor_agent.CURATION_PREP_AGENT_ID,)
        for call in list_calls
    )
    assert (
        'source_ref="extraction-result:00000000-0000-4000-8000-000000000123"'
        in runtime_context
    )
    assert "extraction-result:00000000-0000-4000-8000-000000000456" not in runtime_context
    assert flow_record.extraction_result_id in runtime_context
    assert "active session and loaded document" in runtime_context


def test_build_formatter_bundle_fails_closed_without_loaded_document(monkeypatch):
    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")

    def _unexpected_list_extraction_results(**_kwargs):
        raise AssertionError("missing document scope must not issue an extraction query")

    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        _unexpected_list_extraction_results,
    )

    bundle, runtime_context, unavailable_note = supervisor_agent._build_chat_formatter_bundle(
        user_id="user-1",
        document_id=None,
    )

    assert bundle is None
    assert runtime_context == ""
    assert "no document is loaded in this active session" in unavailable_note


def test_build_formatter_bundle_ignores_legacy_results_from_prior_sessions(monkeypatch):
    current_record = SimpleNamespace(
        extraction_result_id="e00f32a1-4f9a-409b-abd9-a2b72e6c9f92",
        created_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        agent_key="pdf_extraction",
        adapter_key="generic",
        source_kind=SimpleNamespace(value="chat"),
        candidate_count=1,
        document_id="doc-1",
        flow_run_id=None,
        conversation_summary="Extracted one fly strain.",
        metadata={},
        payload_json={
            "envelope_id": "generic-envelope-current",
            "domain_pack_id": "generic",
            "domain_pack_version": "0.1.0",
            "status": "extracted",
            "extracted_objects": [
                {
                    "object_type": "generic_object",
                    "pending_ref_id": "generic-object-1",
                    "payload": {
                        "class_key": "generic:generic_object",
                        "label": "Oregon R",
                        "attributes": {"strain_name": "Oregon R"},
                    },
                }
            ],
            "history": [],
            "validation_findings": [],
            "metadata": {},
        },
    )
    legacy_record = SimpleNamespace(
        extraction_result_id="209ab952-e02a-4b66-9197-b9441047cbed",
        created_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
        agent_key="gene_extractor",
        adapter_key="gene",
        source_kind=SimpleNamespace(value="chat"),
        candidate_count=1,
        document_id="doc-1",
        flow_run_id=None,
        conversation_summary="Legacy gene extraction.",
        metadata={},
        payload_json={
            "summary": "Legacy gene extraction",
            "genes": [{"mention": "crb"}],
            "items": [{"mention": "crb"}],
            "raw_mentions": [],
            "exclusions": [],
            "ambiguities": [],
            "normalization_notes": [],
            "run_summary": {},
        },
    )

    monkeypatch.setattr(supervisor_agent, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(supervisor_agent, "get_current_user_id", lambda: "user-1")

    list_calls: list[dict[str, Any]] = []

    def _fake_list_extraction_results(**kwargs):
        list_calls.append(kwargs)
        source_kind = kwargs.get("source_kind")
        if (
            kwargs.get("origin_session_id") == "session-1"
            and kwargs.get("document_id") == "doc-1"
            and getattr(source_kind, "value", None) == "chat"
        ):
            return [current_record]
        if source_kind is not None:
            return []
        if kwargs.get("document_id") == "doc-1":
            return [current_record, legacy_record]
        return []

    monkeypatch.setattr(
        supervisor_agent,
        "list_extraction_results",
        _fake_list_extraction_results,
    )

    bundle, runtime_context, unavailable_note = supervisor_agent._build_chat_formatter_bundle(
        user_id="user-1",
        document_id="doc-1",
    )

    assert bundle is not None
    assert unavailable_note == ""
    assert [artifact.extraction_result_id for artifact in bundle.artifacts] == [
        current_record.extraction_result_id
    ]
    assert bundle.rows_for_source("object")[0]["object.attribute.strain_name"] == "Oregon R"
    assert bundle.warnings == []
    assert len(list_calls) == 2
    assert all(call.get("origin_session_id") == "session-1" for call in list_calls)
    assert current_record.extraction_result_id in runtime_context
    assert legacy_record.extraction_result_id not in runtime_context


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
        "recall_chat_history",
    ]
    inspect_tool = next(
        tool
        for tool in created.tools
        if getattr(tool, "name", "") == "inspect_results"
    )
    inspect_params = inspect.signature(inspect_tool).parameters
    assert "action" in inspect_params
    assert "query" in inspect_params
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
    assert "action=\"search\"" in tools_by_name["inspect_results"].description
    assert "export_to_file" not in tools_by_name
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
        current_user_request="Use this exact controlled vocabulary.",
    )

    assert "DOCUMENT CONTEXT: A PDF document is loaded." in created.instructions
    assert "RUNTIME TOOL DESCRIPTIONS ARE AUTHORITATIVE" in created.instructions
    assert created.input_guardrails == ["safety"]
    assert captured_dynamic["sections"] == ["Introduction", "Methods"]
    assert captured_dynamic["authoritative_user_request"] == (
        "Use this exact controlled vocabulary."
    )


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
