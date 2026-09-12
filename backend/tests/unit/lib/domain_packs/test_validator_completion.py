"""The runtime finalizer can end a run without a redundant model answer."""

import json
from types import SimpleNamespace

import pytest
from agents import Agent, AgentOutputSchema, RunConfig, Runner, function_tool
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall

from src.lib.domain_packs.validator_dispatch import (
    _ValidatorFinalizationState,
    _batch_output_schema_for_agent_output,
    _build_finalize_validator_result_tool,
    _configure_accepted_finalization_stop,
)
from src.schemas.domain_validator import DomainValidatorResultBase
from .test_validator_dispatch import _result_payload, _validation_request


class RepairThenFinalize(Model):
    def __init__(self, payload):
        self.calls = 0
        self.payload = payload

    async def get_response(self, *args, **kwargs):
        self.calls += 1
        assert self.calls <= 2, "Finalization must not require another model response"
        payload = {} if self.calls == 1 else self.payload
        return ModelResponse(
            output=[ResponseFunctionToolCall(
                type="function_call", name="finalize_validator_result",
                call_id=f"finalize-{self.calls}", arguments=json.dumps({"result": payload}),
            )], usage=Usage(requests=1), response_id=f"response-{self.calls}",
        )

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("Non-streaming validator expected")
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_rejected_finalizer_repairs_then_stops_with_accepted_result(monkeypatch):
    monkeypatch.setenv("VALIDATOR_STOP_AFTER_ACCEPTED_FINALIZATION", "true")
    request = _validation_request()
    model = RepairThenFinalize(_result_payload(request))
    state = _ValidatorFinalizationState()
    agent = Agent(
        name="validator", model=model,
        output_type=AgentOutputSchema(DomainValidatorResultBase, strict_json_schema=False),
        tools=[_build_finalize_validator_result_tool(
            request, finalization_state=state, function_tool_factory=function_tool,
        )],
    )
    _configure_accepted_finalization_stop(agent, state, batch=False)
    result = await Runner.run(agent, "validate", run_config=RunConfig(tracing_disabled=True))
    assert model.calls == 2
    assert result.final_output is state.accepted_result
    assert result.final_output.status == "resolved"


def test_batch_completion_preserves_typed_result_envelope(monkeypatch):
    monkeypatch.setenv("VALIDATOR_STOP_AFTER_ACCEPTED_FINALIZATION", "true")
    agent = SimpleNamespace(tool_use_behavior="run_llm_again")
    state = _ValidatorFinalizationState()
    schema = _batch_output_schema_for_agent_output(DomainValidatorResultBase)
    _configure_accepted_finalization_stop(agent, state, batch=True, batch_output_type=schema)
    assert not agent.tool_use_behavior(None, []).is_final_output
    state.accepted_results = (DomainValidatorResultBase.model_validate(_result_payload(_validation_request())),)
    result = agent.tool_use_behavior(None, [])
    assert result.is_final_output
    assert isinstance(result.final_output, schema)
    assert result.final_output.results == list(state.accepted_results)


def test_experiment_does_not_change_baseline_by_default(monkeypatch):
    monkeypatch.delenv("VALIDATOR_STOP_AFTER_ACCEPTED_FINALIZATION", raising=False)
    agent = SimpleNamespace(tool_use_behavior="run_llm_again")
    _configure_accepted_finalization_stop(agent, _ValidatorFinalizationState(), batch=False)
    assert agent.tool_use_behavior == "run_llm_again"
