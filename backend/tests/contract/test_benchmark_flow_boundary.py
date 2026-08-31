from types import SimpleNamespace
from uuid import uuid4

import src.lib.benchmarks.runtime as benchmark_runtime
from src.lib.benchmarks.models import BenchmarkRoute


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

    monkeypatch.setattr(benchmark_runtime, "_flow_from_recipe", lambda _target_id: flow)
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
