"""No-network regression tests for immutable execution and SDK span identity."""
import asyncio
import contextvars
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.lib.observability.cost_context import (
    attach_agent_cost_identity, agent_identity, cost_scope, costed_stream,
    costed_call, costed_document_processing, current_cost_context, execution_context,
)


def test_verified_paper_uses_subject_ownership_and_preserves_artifact(monkeypatch):
    from src.models.sql import database
    db = MagicMock()
    db.__enter__.return_value = db
    query = db.query.return_value.join.return_value
    query.filter.return_value.one_or_none.return_value = SimpleNamespace(
        source_provider="example", source_provider_reference_curie="EX:paper-A",
        source_provider_reference_id="123", source_md5=None, file_hash="checksum",
    )
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    first = execution_context(activity="interactive_chat", document_id="artifact-A", user_id="auth-sub")
    second = execution_context(activity="extraction_flow", document_id="artifact-B", user_id="auth-sub")
    assert first["paper"] == second["paper"] == {"namespace": "example", "id": "EX:paper-A"}
    assert first["document_id"] != second["document_id"]
    assert first["artifact_revision"] == "checksum"
    predicate = query.filter.call_args.args[1]
    assert "auth_sub" in str(predicate)
    assert list(predicate.compile().params.values()) == ["auth-sub"]
    query.filter.return_value.one_or_none.return_value = None
    assert execution_context(activity="interactive_chat", document_id="unowned", user_id="auth-sub")["paper_category"] == "artifact_only"


@pytest.mark.asyncio
async def test_concurrent_papers_and_multi_trace_continuation_are_isolated():
    async def run(paper):
        data = {"paper": {"namespace": "test", "id": paper}, "run_id": "run-" + paper}
        with cost_scope(data):
            data["paper"]["id"] = "mutated"
            await asyncio.sleep(0)
            first = current_cost_context()
            copied = current_cost_context()
            copied["paper"]["id"] = "also-mutated"
            assert first == current_cost_context()
            # The subprocess protocol carries exactly this JSON snapshot.
            from src.lib.packages.package_runner_entrypoint import _apply_backend_request_context
            child = contextvars.Context()
            child.run(_apply_backend_request_context, {"cost_context": json.loads(json.dumps(first))})
            assert child.run(current_cost_context) == first
            assert first["paper"]["id"] == paper
            return first
    first, second = await asyncio.gather(run("A"), run("B"))
    assert first["run_id"] != second["run_id"]
    assert current_cost_context() == {}


@pytest.mark.asyncio
async def test_stream_does_not_leak_at_yield_or_error():
    @costed_stream
    async def stream(agent=None, turn_id=None, document_id=None, user_id=None):
        yield current_cost_context()
        raise RuntimeError("fixture")
    a, b = stream(turn_id="A"), stream(turn_id="B")
    assert (await anext(a))["run_id"] == "A"
    assert current_cost_context() == {}
    assert (await anext(b))["run_id"] == "B"
    with pytest.raises(RuntimeError):
        await anext(a)
    await b.aclose()
    assert current_cost_context() == {}


@pytest.mark.asyncio
async def test_standalone_validation_and_fresh_rerun():
    agent = SimpleNamespace(cost_identity={"agent_role": "validation"})
    @costed_call
    async def call(agent):
        return current_cost_context()
    first, rerun = await call(agent), await call(agent)
    assert first["activity"] == "standalone_validation"
    assert first["run_id"] != rerun["run_id"]
    with cost_scope({"run_id": "continued", "activity": "extraction_flow"}):
        assert (await call(agent))["run_id"] == "continued"
    assert execution_context(activity="authoring")["paper_category"] == "not_associated"
    assert execution_context(activity="background", document_id="artifact")["paper_category"] == "artifact_only"


@pytest.mark.asyncio
async def test_background_job_context_survives_children_but_new_job_is_separate():
    @costed_document_processing
    async def lower(document_id, user_id=None):
        @costed_call
        async def classifier(agent):
            return current_cost_context()
        return await classifier(SimpleNamespace())

    @costed_document_processing
    async def job(request):
        return await lower(request.document_id)

    first = await job(SimpleNamespace(job_id="job-1", document_id="doc-A"))
    resumed = await job(SimpleNamespace(job_id="job-1", document_id="doc-A"))
    with cost_scope(first):
        different = await job(SimpleNamespace(job_id="job-2", document_id="doc-B"))
    assert first == resumed
    assert first["run_id"] == "job-1"
    assert first["paper_category"] == "artifact_only"
    assert different["run_id"] == "job-2"
    assert different["document_id"] == "doc-B"
    assert current_cost_context() == {}


@pytest.mark.asyncio
async def test_pinned_sdk_processor_attaches_nearest_agent_and_exclusive_usage():
    from agents import Agent, agent_span, response_span, trace, set_trace_processors
    from openai.types.responses import Response
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from src.lib.observability.cost_tracing import CostTracingProcessor
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    processor = CostTracingProcessor(provider.get_tracer("cost-test"))
    set_trace_processors([processor])
    response = Response.model_validate({
        "id": "resp_fixture", "created_at": 1, "object": "response", "model": "fixture-model",
        "output": [], "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
        "usage": {"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200,
                  "input_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 300},
                  "output_tokens_details": {"reasoning_tokens": 150}},
    })
    agent = attach_agent_cost_identity(Agent(name="Validator"), {
        **agent_identity("validator", "Validator", "validation", "revision"), "provider": "openai",
    })
    try:
        with cost_scope({"run_id": "run-A", "paper": {"namespace": "test", "id": "A"}}):
            with trace("synthetic"):
                with agent_span("Validator"):
                    await agent.hooks.on_start(None, agent)
                    with response_span(response):
                        pass
        generations = [s for s in exporter.get_finished_spans() if s.name == "response"]
        assert len(generations) == 1
        metadata = json.loads(generations[0].attributes["metadata"])["cost_context"]
        assert metadata["agent_id"] == "validator"
        assert metadata["paper"]["id"] == "A"
        assert metadata["provider_response_id"] == "resp_fixture"
        usage = json.loads(generations[0].attributes["langfuse.observation.usage_details"])
        assert usage == {"input": 100, "input_cached_tokens": 600, "input_cache_creation": 300,
                         "output": 50, "output_reasoning_tokens": 150}
        assert sum(usage.values()) == 1200
    finally:
        set_trace_processors([])
        provider.shutdown()
