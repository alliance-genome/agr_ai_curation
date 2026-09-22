"""Adapter defects terminate, while model decision errors remain repairable."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agents import function_tool
from agents.tool_context import ToolContext

from src.lib.domain_packs.compact_decisions import CompactValidatorDecision
from src.lib.domain_packs import validator_dispatch as dispatch
from src.lib.openai_agents import streaming_tools
from src.schemas.domain_validator import DomainValidationRequest, DomainValidatorResultBase


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "batch", "streaming"])
@pytest.mark.parametrize("error_type", [ValueError, TypeError, KeyError])
async def test_compact_failure_classification(monkeypatch, mode, error_type):
    capture = Mock(return_value=True)
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception", capture)
    runtime = SimpleNamespace(
        contracts={"r": SimpleNamespace(decision_schema=CompactValidatorDecision)},
        assemble=Mock(side_effect=error_type("private source value")),
        assemble_batch=Mock(side_effect=error_type("private source value")),
    )
    request = DomainValidationRequest(request_id="r", validator_binding_id="fixture",
        validator_agent={"package_id": "fixture", "agent_id": "fixture"},
        target={"domain_pack_id": "fixture"})
    state = dispatch._ValidatorFinalizationState(accepted_result=object(), accepted_results=(object(),))
    if mode == "single":
        tool = dispatch._build_finalize_validator_result_tool(request, finalization_state=state,
            function_tool_factory=function_tool, compact_runtime=runtime)
    elif mode == "batch":
        tool = dispatch._build_finalize_validator_batch_results_tool([], finalization_state=state,
            function_tool_factory=function_tool, compact_runtime=runtime)
    else:
        state = streaming_tools._StructuredSpecialistFinalizationState(required=True,
            tool_name="finalize_fixture", agent_name="Fixture", output_type_name="Fixture",
            accepted_payload={"old": True})
        tool = streaming_tools._build_structured_specialist_finalization_tool(
            expected_output_type=DomainValidatorResultBase, finalization_state=state,
            tool_calls=[], live_evidence_records=[], function_tool_factory=function_tool,
            compact_runtime=runtime)
    arguments = json.dumps({"results": [{}]} if mode == "batch" else {"result": {}})
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id="finalize",
        tool_arguments=arguments)
    if error_type is ValueError:
        result = await tool.on_invoke_tool(context, arguments)
        assert result["status"] == "rejected"
        capture.assert_not_called()
    else:
        with pytest.raises(RuntimeError, match="Compact validator assembly failed"):
            await tool.on_invoke_tool(context, arguments)
        capture.assert_called_once()
        reported = capture.call_args.args[0]
        assert str(reported) == "Compact validator assembly failed"
        assert reported.__cause__ is None
        assert "private source value" not in repr(capture.call_args.kwargs)
    if mode == "batch":
        assert state.accepted_results == ()
    elif mode == "single":
        assert state.accepted_result is None
    else:
        assert state.accepted_payload is None
