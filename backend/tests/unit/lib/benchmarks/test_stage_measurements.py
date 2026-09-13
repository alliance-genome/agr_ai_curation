import asyncio
from unittest.mock import Mock

import pytest

from src.lib.benchmarks.stage_measurements import (
    StageIdentity, current_stage, measure_stage, observe_stages,
)


def test_unobserved_runtime_is_unchanged():
    with measure_stage(StageIdentity("node", "other")) as stage:
        assert stage is None
        assert current_stage() is None


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("with_runtime_context", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_validator_thread_preserves_stage_context(monkeypatch, batch, with_runtime_context, fails):
    from contextvars import ContextVar
    from src.lib.domain_packs import validator_dispatch

    marker = ContextVar("validator_test_marker", default="outside")
    observer = Mock()
    binding = object()
    payload = [] if batch else object()
    runtime_context = object() if with_runtime_context else None
    runner_name = "run_package_scoped_validator_agent" + ("_batch" if batch else "")

    def runner(received, **kwargs):
        assert received is payload
        assert kwargs == {"binding": binding, **(
            {"runtime_context": runtime_context} if with_runtime_context else {}
        )}
        assert marker.get() == "benchmark"
        assert current_stage() is parent
        # Thread-local writes must not replace the caller's context.
        marker.set("worker")
        with measure_stage(StageIdentity("validator", "validation")) as child:
            assert child is not None and parent is not None
            assert child.parent_execution_id == parent.execution_id
            if fails:
                raise ValueError("validator failed")
        return "validated"

    monkeypatch.setattr(validator_dispatch, runner_name, runner)
    marker_token = marker.set("benchmark")
    try:
        with observe_stages(observer), measure_stage(StageIdentity("extractor", "extraction")) as parent:
            invoke = getattr(validator_dispatch, runner_name + "_in_worker_thread")
            if fails:
                with pytest.raises(ValueError, match="validator failed"):
                    invoke(payload, binding=binding, runtime_context=runtime_context)
            else:
                assert invoke(payload, binding=binding, runtime_context=runtime_context) == "validated"
            assert current_stage() is parent
            assert marker.get() == "benchmark"
    finally:
        marker.reset(marker_token)
    child_finish = observer.completed.call_args_list[0].args[0]
    assert child_finish.status == ("failed" if fails else "succeeded")
    assert observer.started.call_count == observer.completed.call_count == 2


def test_repeated_agent_nodes_have_distinct_occurrences_and_explicit_roles():
    observer = Mock()
    with observe_stages(observer):
        with measure_stage(StageIdentity("first", "extraction", node_id="a", agent_id="shared")) as first:
            assert first is not None
            assert current_stage() is first
        with measure_stage(StageIdentity("second", "validation", node_id="b", agent_id="shared")) as second:
            assert second is not None
            assert second.execution_id != first.execution_id
        assert current_stage() is None
    assert observer.started.call_count == observer.completed.call_count == 2
    assert observer.completed.call_args.args[0].status == "succeeded"


@pytest.mark.asyncio
async def test_parallel_children_keep_parent_without_inheriting_sibling_context():
    observer = Mock()
    ready = asyncio.Event()
    entered = []

    async def child(name):
        with measure_stage(StageIdentity(name, "validation", binding_id=name)) as active:
            entered.append(active)
            if len(entered) == 2:
                ready.set()
            await ready.wait()
            assert current_stage() is active

    with observe_stages(observer), measure_stage(StageIdentity("source", "extraction")) as parent:
        assert parent is not None
        await asyncio.gather(child("one"), child("two"))
        assert current_stage() is parent
    assert len({stage.execution_id for stage in entered}) == 2
    assert {stage.parent_execution_id for stage in entered} == {parent.execution_id}
    assert observer.completed.call_count == 3


@pytest.mark.parametrize("error,status", [(ValueError("private payload"), "failed"), (asyncio.CancelledError(), "interrupted")])
def test_failure_and_cancellation_are_recorded_without_content(error, status):
    observer = Mock()
    with observe_stages(observer), pytest.raises(type(error)):
        with measure_stage(StageIdentity("node", "other")):
            raise error
    finished = observer.completed.call_args.args[0]
    assert finished.status == status
    assert finished.failure_type == type(error).__name__
    assert "private payload" not in repr(finished)
    assert current_stage() is None


def test_start_failure_prevents_work_and_nested_observers_restore_context():
    outer, inner = Mock(), Mock()
    inner.started.side_effect = RuntimeError("checkpoint unavailable")
    with observe_stages(outer), measure_stage(StageIdentity("outer", "other")) as parent:
        with observe_stages(inner), pytest.raises(RuntimeError):
            with measure_stage(StageIdentity("inner", "other")):
                pytest.fail("Must not execute after failed durable start")
        assert current_stage() is parent
    inner.completed.assert_not_called()


def test_elapsed_is_monotonic_interval_not_sum_of_child_work(monkeypatch):
    observer = Mock()
    ticks = iter([10.0, 10.1, 10.3, 10.5])
    monkeypatch.setattr("src.lib.benchmarks.stage_measurements.monotonic", lambda: next(ticks))
    with observe_stages(observer), measure_stage(StageIdentity("parent", "supervisor")):
        with measure_stage(StageIdentity("child", "extraction"), parent_invocation_sequence=4):
            pass
    child, parent = [call.args[0] for call in observer.completed.call_args_list]
    assert child.elapsed_ms == 200
    assert parent.elapsed_ms == 500
    assert child.start.parent_invocation_sequence == 4


@pytest.mark.asyncio
async def test_provider_calls_retain_dispatch_stage_after_context_changes(monkeypatch):
    from src.lib.openai_agents.provider_usage import (
        begin_provider_invocation, capture_provider_usage, complete_generic_provider_invocation,
        fail_provider_invocation, provider_usage_metadata,
    )
    from src.lib.benchmarks.models import ProviderUsage

    monkeypatch.setattr("src.lib.openai_agents.provider_usage._emit_provider_usage_trace_event", lambda record: None)
    observer = Mock()
    pending_by_node = {}

    async def invoke(node):
        with measure_stage(StageIdentity(node, "validation", node_id=node, agent_id="same")) as stage:
            pending = begin_provider_invocation(
                requested_provider="fixture", requested_model="fixture", route_slot="agent:same", started_at=1.0,
            )
            pending_by_node[node] = (pending, stage)
            await asyncio.sleep(0)
            assert pending is not None and stage is not None
            assert current_stage() is stage
            assert pending.stage_execution_id == str(stage.execution_id)

    with observe_stages(observer), capture_provider_usage(max_records=2, max_failure_detail_chars=40) as records:
        await asyncio.gather(invoke("one"), invoke("two"))
        assert current_stage() is None
        complete_generic_provider_invocation(pending_by_node["one"][0], {"usage": {"input_tokens": 2}}, latency_ms=5)
        fail_provider_invocation(pending_by_node["two"][0], ValueError("secret payload"), latency_ms=8)
    assert [record.stage_execution_id for record in records] == [
        str(pending_by_node[node][1].execution_id) for node in ("one", "two")
    ]
    assert len({record.sequence for record in records}) == 2
    assert records[1].status == "failed"
    for record in records:
        parsed = ProviderUsage.model_validate(provider_usage_metadata(record))
        assert str(parsed.stage_execution_id) == record.stage_execution_id
        assert parsed.billed_cost is None
