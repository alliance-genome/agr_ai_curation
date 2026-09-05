from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import src.lib.benchmarks.runtime as benchmark_runtime
from src.lib.benchmarks.loader import BenchmarkCatalog
from src.lib.benchmarks.models import (
    BenchmarkExecutionTarget,
    BenchmarkInputReference,
    BenchmarkRoute,
    BenchmarkSuiteRoute,
    ResolvedBenchmarkCell,
)
from src.lib.benchmarks.scoring import score_case
from src.lib.openai_agents.provider_usage import ProviderUsageRecord, emit_provider_usage


async def test_canary_invokes_execute_flow_and_consumes_terminal_result(monkeypatch):
    flow = SimpleNamespace(id=uuid4(), name="Gene Curation")
    captured = {}

    async def fake_execute_flow(**kwargs):
        captured.update(kwargs)
        yield {"type": "FLOW_STARTED", "data": {"flow_id": str(flow.id)}}
        yield {
            "type": "FLOW_FINISHED",
            "data": {
                "status": "completed",
                "outputs": [{"type": "CHAT_OUTPUT_READY", "output": {"matches": []}}],
            },
        }

    monkeypatch.setattr(benchmark_runtime, "_flow_from_recipe", lambda _target_id, _groups: flow)
    monkeypatch.setattr(benchmark_runtime, "execute_flow", fake_execute_flow)

    result = await benchmark_runtime.execute_flow_case(
        "Gene Curation",
        {"user_query": "synthetic canary"},
        BenchmarkRoute(provider="openai", model="gpt-5.6-sol"),
        "benchmark-run",
    )

    assert captured["flow"] is flow
    assert captured["model_id_override"] == "gpt-5.6-sol"
    assert captured["model_provider_override"] == "openai"
    assert result.output["status"] == "completed"

    benchmark_root = (
        Path(__file__).resolve().parents[3] / "packages" / "alliance" / "benchmarks"
    )
    profile = BenchmarkCatalog(
        benchmark_root,
        agent_ids={"gene_validation", "ontology_term_validation"},
        flow_ids={"Gene Curation"},
        route_validator=lambda _model, _provider: None,
    ).get_profile("flow-canary-gene-curation-v1")
    score = score_case(
        scorer=profile.profile.scorers[0],
        expected=profile.cases[0].expected,
        actual=result.output,
    )

    assert score.scorer_id == "deterministic-v1"
    assert score.outcome == "pass"
    assert score.fields[0].path == "/status"


async def test_resolved_cell_passes_independent_routes_and_returns_every_invocation(
    monkeypatch,
):
    flow = SimpleNamespace(id=uuid4(), name="Gene Curation")
    routes = {
        "supervisor": BenchmarkSuiteRoute(
            provider="openai", model="supervisor-model", reasoning_effort="high"
        ),
        "agent:extractor": BenchmarkSuiteRoute(
            provider="openrouter", model="extractor-model", reasoning_effort="low"
        ),
        "validator:ontology": BenchmarkSuiteRoute(
            provider="openai", model="validator-model", reasoning_effort="medium"
        ),
    }
    cell = ResolvedBenchmarkCell(
        cell_id="sha256:" + "1" * 64,
        case_id="case-1",
        configuration_id="config-1",
        repetition=1,
        target=BenchmarkExecutionTarget(kind="flow", id="Gene Curation"),
        input=BenchmarkInputReference(
            resolver="fixture",
            reference="paper-1",
            version="1",
            digest="sha256:" + "2" * 64,
        ),
        routes=routes,
    )
    captured = {}

    async def fake_execute_flow(**kwargs):
        captured.update(kwargs)
        for sequence, (slot, route) in enumerate(routes.items(), 1):
            emit_provider_usage(
                ProviderUsageRecord(
                    route_slot=slot,
                    requested_provider=route.provider,
                    requested_model=route.model,
                    reasoning_effort=route.reasoning_effort,
                    actual_provider=route.provider,
                    actual_model=route.model,
                    routing_attempt=0,
                    latency_ms=sequence,
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                    billed_cost=None,
                    sequence=sequence,
                )
            )
        yield {"type": "FLOW_FINISHED", "data": {"status": "completed"}}

    monkeypatch.setattr(benchmark_runtime, "_flow_from_recipe", lambda _id, _groups: flow)
    monkeypatch.setattr(benchmark_runtime, "execute_flow", fake_execute_flow)

    document_id = str(uuid4())
    def load_result(receipt, **scope):
        assert receipt == {"status": "completed"}
        assert scope == {"document_id": document_id, "user_id": "synthetic-curator", "run_id": "run-1"}
        return {"records": []}
    monkeypatch.setattr(benchmark_runtime, "load_flow_extractions", load_result)
    result = await benchmark_runtime.execute_resolved_flow_cell(
        cell, {"document_id": document_id, "user_id": "synthetic-curator"}, "run-1",
    )

    assert captured["benchmark_routes"] == cell.routes
    assert [record.route_slot for record in result.invocations] == list(routes)
    assert [record.requested_model for record in result.invocations] == [
        "supervisor-model",
        "extractor-model",
        "validator-model",
    ]
