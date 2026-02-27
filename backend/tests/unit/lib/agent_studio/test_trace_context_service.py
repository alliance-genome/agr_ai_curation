"""Unit tests for trace context service."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import src.lib.agent_studio.trace_context_service as trace_context_service


def _obs(**kwargs):
    return SimpleNamespace(**kwargs)


@pytest.mark.asyncio
async def test_get_trace_context_for_explorer_success(monkeypatch):
    now = datetime.utcnow()
    trace = SimpleNamespace(
        session_id="session-1",
        timestamp=now,
        input={"message": "What genes are mentioned?"},
        output={"response": "Found gene xyz"},
        start_time=now,
        end_time=now + timedelta(seconds=2),
    )
    observations = [
        _obs(
            type="GENERATION",
            name="ask_gene_extractor_specialist",
            input={"messages": [{"role": "system", "content": "system prompt body"}]},
            output={"content": "assistant output"},
            model="gpt-4o",
            usage=SimpleNamespace(total=111),
            metadata={"active_groups": "WB"},
        ),
        _obs(
            type="SPAN",
            name="transfer_to_gene_extractor",
            start_time=now,
        ),
        _obs(
            type="SPAN",
            name="search_document",
            input={"query": "gene xyz"},
            output={"hits": 2},
            start_time=now,
            end_time=now + timedelta(milliseconds=350),
        ),
    ]

    class _FakeLangfuse:
        def __init__(self):
            self.api = SimpleNamespace(
                trace=SimpleNamespace(get=lambda _trace_id: trace),
                observations=SimpleNamespace(get_many=lambda **_kwargs: SimpleNamespace(data=observations)),
            )

    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=_FakeLangfuse))
    context = await trace_context_service.get_trace_context_for_explorer("trace-1")

    assert context.trace_id == "trace-1"
    assert context.session_id == "session-1"
    assert context.user_query == "What genes are mentioned?"
    assert context.final_response_preview.startswith("Found gene")
    assert context.total_duration_ms == 2000
    assert context.total_tokens == 111
    assert context.agent_count == 1
    assert len(context.prompts_executed) == 1
    assert context.prompts_executed[0].agent_id == "gene_extractor"
    assert len(context.routing_decisions) == 1
    assert context.routing_decisions[0].to_agent == "gene_extractor"
    assert len(context.tool_calls) == 1
    assert context.tool_calls[0].name == "search_document"


@pytest.mark.asyncio
async def test_get_trace_context_for_explorer_import_error(monkeypatch):
    # Simulate from langfuse import Langfuse failing
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace())
    with pytest.raises(trace_context_service.LangfuseUnavailableError):
        await trace_context_service.get_trace_context_for_explorer("trace-1")


@pytest.mark.asyncio
async def test_get_trace_context_for_explorer_init_error(monkeypatch):
    class _BrokenLangfuse:
        def __init__(self):
            raise RuntimeError("cannot init")

    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=_BrokenLangfuse))
    with pytest.raises(trace_context_service.LangfuseUnavailableError):
        await trace_context_service.get_trace_context_for_explorer("trace-1")


@pytest.mark.asyncio
async def test_get_trace_context_for_explorer_trace_not_found(monkeypatch):
    class _FakeLangfuse:
        def __init__(self):
            self.api = SimpleNamespace(
                trace=SimpleNamespace(get=lambda _trace_id: None),
                observations=SimpleNamespace(get_many=lambda **_kwargs: SimpleNamespace(data=[])),
            )

    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=_FakeLangfuse))
    with pytest.raises(trace_context_service.TraceNotFoundError):
        await trace_context_service.get_trace_context_for_explorer("trace-1")


@pytest.mark.asyncio
async def test_get_trace_context_for_explorer_wraps_unexpected_errors(monkeypatch):
    class _FakeLangfuse:
        def __init__(self):
            self.api = SimpleNamespace(
                trace=SimpleNamespace(get=lambda _trace_id: SimpleNamespace(session_id="s", timestamp=datetime.utcnow())),
                observations=SimpleNamespace(get_many=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))),
            )

    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=_FakeLangfuse))
    with pytest.raises(trace_context_service.TraceContextError, match="Failed to extract trace context"):
        await trace_context_service.get_trace_context_for_explorer("trace-1")


def test_extract_and_normalize_helpers():
    assert trace_context_service._normalize_agent_id("pdf_specialist") == "pdf"
    assert trace_context_service._normalize_agent_id("gene_extractor") == "gene_extractor"

    obs_name = _obs(name="ask_disease_extractor_specialist", metadata=None)
    assert trace_context_service._identify_agent_from_observation(obs_name) == "disease_extractor"

    obs_meta = _obs(name="unknown", metadata={"agent": "pdf_specialist"})
    assert trace_context_service._identify_agent_from_observation(obs_meta) == "pdf"

    assert trace_context_service._agent_id_to_name("pdf") == "General PDF Extraction Agent"
    assert trace_context_service._agent_id_to_name("made_up_agent") == "Made Up Agent"

    obs_group_new = _obs(metadata={"active_groups": "WB"})
    obs_group_old = _obs(metadata={"active_mods": "RGD"})
    obs_group_legacy = _obs(metadata={"mod": "SGD"})
    assert trace_context_service._extract_group_from_observation(obs_group_new) == "WB"
    assert trace_context_service._extract_group_from_observation(obs_group_old) == "RGD"
    assert trace_context_service._extract_group_from_observation(obs_group_legacy) == "SGD"


def test_extract_user_query_final_response_and_duration():
    now = datetime.utcnow()
    trace = SimpleNamespace(
        input={"query": "Query text"},
        output={"content": "Response text"},
        start_time=now,
        end_time=now + timedelta(milliseconds=1500),
    )
    observations = [
        _obs(
            type="GENERATION",
            input={"messages": [{"role": "user", "content": "fallback user"}]},
            output={"content": "fallback response"},
        )
    ]

    assert trace_context_service._extract_user_query(trace, observations) == "Query text"
    assert trace_context_service._extract_final_response(trace, observations) == "Response text"
    assert trace_context_service._calculate_duration_ms(trace) == 1500

    blank_trace = SimpleNamespace(input=None, output=None, start_time=None, end_time=None)
    assert trace_context_service._extract_user_query(blank_trace, observations) == "fallback user"
    assert trace_context_service._extract_final_response(blank_trace, observations) == "fallback response"
    assert trace_context_service._calculate_duration_ms(blank_trace) is None
