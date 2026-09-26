"""Ordinary request hooks: real SDK boundaries, synthetic providers only."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock
from decimal import Decimal

from agents import Agent, RunConfig, Runner
import pytest

from src.lib.cost_ledger import runtime_writes
from src.lib.cost_ledger.runtime_context import (
    RuntimeCostContext, current_runtime_cost_context, runtime_cost_scope,
)
from src.lib.observability.cost_context import costed_stream
from src.lib.openai_agents.provider_usage import observe_provider_invocations
from tests.unit.lib.openai_agents.test_model_request_measurement import (
    FakeResponsesHTTPModel, _final,
)


@pytest.mark.parametrize("streamed", [False, True])
def test_sdk_runtime_attempt_preserves_usage_identity(monkeypatch, streamed):
    sink = Mock()
    reservations = []
    def reserve(measurement):
        reservations.append((measurement['measurement_id'], current_runtime_cost_context()))
        return sink
    monkeypatch.setattr(runtime_writes, "reserve_runtime_request", reserve)
    agent = Agent(name="synthetic", model=FakeResponsesHTTPModel([_final()]))
    context = RuntimeCostContext('owner', 'session', 'turn', 'interactive_chat')
    async def run():
        with runtime_cost_scope(context):
            if streamed:
                result = Runner.run_streamed(agent, 'synthetic', run_config=RunConfig(tracing_disabled=True))
                async for _ in result.stream_events():
                    pass
            else:
                await Runner.run(agent, 'synthetic', run_config=RunConfig(tracing_disabled=True))
    asyncio.run(run())
    assert len(reservations) == 1 and reservations[0][1] == context
    sink.finish.assert_called_once()
    facts = sink.finish.call_args.kwargs
    assert facts['usage'].input_tokens == (900 if streamed else 1200)
    assert facts['charge'] is None
    assert current_runtime_cost_context() is None


def test_benchmark_observer_is_not_given_runtime_writer(monkeypatch):
    monkeypatch.setenv('COST_LEDGER_RUNTIME_ENABLED', 'true')
    with runtime_cost_scope(RuntimeCostContext('owner', 'session', 'turn', 'extraction_flow')):
        with observe_provider_invocations(Mock()):
            assert runtime_writes.reserve_runtime_request({}) is None


def test_package_worker_restores_then_clears_attribution():
    from dataclasses import asdict
    from src.lib.packages.package_runner_entrypoint import _apply_backend_request_context
    context = RuntimeCostContext('owner', None, 'job', 'background', document_id='doc', job_id='job')
    with runtime_cost_scope(None):
        _apply_backend_request_context({'runtime_cost_context': asdict(context)})
        assert current_runtime_cost_context() == context
        _apply_backend_request_context({})
        assert current_runtime_cost_context() is None


def test_missing_runtime_scope_fails_before_provider(monkeypatch):
    monkeypatch.setenv('COST_LEDGER_RUNTIME_ENABLED', 'true')
    monkeypatch.delenv('COST_LEDGER_DEPLOYMENT_ID', raising=False)
    with runtime_cost_scope(RuntimeCostContext('owner', 'session', 'turn', 'interactive_chat')):
        with pytest.raises(RuntimeError, match='scope'):
            runtime_writes.reserve_runtime_request({})


def test_stream_context_isolated_and_flow_identity_preserved():
    observed = []
    @costed_stream
    async def stream(*, agent=None, user_id=None, session_id=None, turn_id=None):
        try:
            observed.append(current_runtime_cost_context())
            yield 'value'
        finally:
            observed.append(current_runtime_cost_context())
    agent = SimpleNamespace(cost_execution_context={
        'run_id': 'flow-turn', 'activity': 'extraction_flow', 'workflow_id': 'flow', 'job_id': 'execution',
    })
    async def run():
        first = stream(user_id='a', session_id='session-a', turn_id='turn-a')
        second = stream(agent=agent, user_id='b', session_id='session-b')
        await anext(first)
        assert current_runtime_cost_context() is None
        await anext(second)
        assert current_runtime_cost_context() is None
        await first.aclose()
        await second.aclose()
    asyncio.run(run())
    assert [c.owner_subject for c in observed] == ['a', 'b', 'a', 'b']
    assert observed[1].workflow_id == 'flow' and observed[1].flow_run_id == 'execution'
    assert observed[1].job_id is None  # Trace job_id is the flow-run ID, not a document job.


@pytest.mark.asyncio
@pytest.mark.parametrize('streamed', [False, True])
async def test_raw_openrouter_charge_reaches_same_measured_attempt(monkeypatch, streamed):
    import httpx
    import json
    from openai import AsyncOpenAI
    from src.lib.openai_agents.provider_model import ProviderConfiguredChatCompletionsModel
    monkeypatch.setenv('LLM_DISABLED_PROVIDERS', '')
    sink = Mock()
    reserve = Mock(return_value=sink)
    monkeypatch.setattr(runtime_writes, 'reserve_runtime_request', reserve)
    def respond(request):
        if streamed:
            chunk = {
                'id': 'synthetic', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'test-model',
                'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'done'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 8, 'total_tokens': 15, 'cost': '0.000000000000000123'},
            }
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                  content='data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={
            'id': 'synthetic', 'object': 'chat.completion', 'created': 1, 'model': 'test-model',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'done'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 7, 'completion_tokens': 8, 'total_tokens': 15, 'cost': '0.000000000000000123'},
        })
    async with AsyncOpenAI(api_key='test-key', base_url='https://provider.invalid/v1',
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
        model = ProviderConfiguredChatCompletionsModel(
            model='test-model', openai_client=client, provider_id='openrouter',
            request_extra_body={}, request_headers={}, forbidden_request_fields=(),
            omit_usage_request=True, telemetry_adapter='openrouter', disable_model_retries=True,
        )
        agent = Agent(name='synthetic', model=model)
        if streamed:
            result = Runner.run_streamed(agent, 'synthetic', run_config=RunConfig(tracing_disabled=True))
            async for _ in result.stream_events():
                pass
        else:
            await Runner.run(agent, 'synthetic', run_config=RunConfig(tracing_disabled=True))
    reserve.assert_called_once()
    charges = [call.kwargs['charge'] for call in sink.finish.call_args_list if call.kwargs['charge'] is not None]
    assert len(charges) == 1 and charges[0].amount == Decimal('0.000000000000000123')
    assert charges[0].unit == 'credits'


def test_failed_model_request_retains_unknown_attempt(monkeypatch):
    sink = Mock()
    monkeypatch.setattr(runtime_writes, 'reserve_runtime_request', Mock(return_value=sink))
    model = FakeResponsesHTTPModel([ValueError('synthetic provider failure')])
    with pytest.raises(ValueError, match='synthetic provider failure'):
        asyncio.run(Runner.run(Agent(name='synthetic', model=model), 'synthetic', run_config=RunConfig(tracing_disabled=True)))
    sink.finish.assert_called_once()
    assert sink.finish.call_args.kwargs['usage'].status == 'missing'
    assert sink.finish.call_args.kwargs['outcome'] == 'provider_error'


@pytest.mark.parametrize('operation', ['usage_unavailable', 'completion_failed'])
def test_accounting_failure_alert_is_sanitized(monkeypatch, caplog, operation):
    from uuid import uuid4
    from src.lib.cost_ledger.facts import TokenUsage
    attempt = runtime_writes.RuntimeAccountingAttempt('deployment', 'namespace', 'owner', uuid4(), 'run')
    capture = Mock()
    monkeypatch.setattr(runtime_writes, 'report_runtime_exception', capture)
    def fail(*args, **kwargs):
        raise ValueError('secret SQL curator text')
    if operation == 'completion_failed':
        monkeypatch.setattr(runtime_writes, 'SessionLocal', fail)
        attempt.finish(usage=TokenUsage(), charge=None, outcome='completed')
    else:
        from src.lib.openai_agents import provider_usage
        monkeypatch.setattr(provider_usage, '_accounting_usage', fail)
        runtime_writes.finish_runtime_request(attempt, raw_usage={}, provider='fixture',
                                              sdk_normalized=False, outcome='completed')
    capture.assert_called_once()
    error = capture.call_args.args[0]
    assert error.__context__ is None and error.__cause__ is None
    assert 'secret SQL' not in str(error) + caplog.text
    assert capture.call_args.kwargs['operation'] == operation
    assert capture.call_args.kwargs['context'] == {'run_id': 'run'}
    assert caplog.records[-1].sentry_skip_event is True
