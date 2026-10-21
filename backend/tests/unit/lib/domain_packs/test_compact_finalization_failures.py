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
        contracts={"r": SimpleNamespace(decision_schema=CompactValidatorDecision, result_schema=DomainValidatorResultBase)},
        assemble=Mock(side_effect=error_type("private source value")),
    )
    request = DomainValidationRequest(request_id="r", validator_binding_id="fixture",
        validator_agent={"package_id": "fixture", "agent_id": "fixture"},
        target={"domain_pack_id": "fixture"})
    state = dispatch._ValidatorFinalizationState(accepted_result=object(), accepted_results=(object(),))
    if mode == "single":
        tool = dispatch._build_finalize_validator_result_tool(request, finalization_state=state,
            function_tool_factory=function_tool, compact_runtime=runtime)
    elif mode == "batch":
        jobs = [SimpleNamespace(request=request, match=SimpleNamespace(binding=SimpleNamespace(raw={})))]
        tool = dispatch._build_finalize_validator_batch_results_tool(jobs, finalization_state=state,
            function_tool_factory=function_tool, compact_runtime=runtime)
    else:
        state = streaming_tools._StructuredSpecialistFinalizationState(required=True,
            tool_name="finalize_fixture", agent_name="Fixture", output_type_name="Fixture",
            accepted_payload={"old": True})
        tool = streaming_tools._build_structured_specialist_finalization_tool(
            expected_output_type=DomainValidatorResultBase, finalization_state=state,
            tool_calls=[], live_evidence_records=[], function_tool_factory=function_tool,
            compact_runtime=runtime)
    arguments = json.dumps({"results": [{"request_id": "r"}]} if mode == "batch" else {"result": {}})
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


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["single", "batch", "streaming"])
@pytest.mark.parametrize("profile_mapped", [False, True])
async def test_real_compact_repair_preserves_judgment_questions_and_ambiguous_audit(mode, profile_mapped):
    """Synthetic identity fixture: model judgment is input, never computed by this test."""
    from src.lib.domain_packs.compact_decisions import CanonicalValidatorRecord, DecisionContract
    from src.lib.domain_packs.compact_runtime import CompactValidatorRuntime
    from src.schemas.domain_validator import ValidatorCandidate, ValidatorLookupAttempt

    request = DomainValidationRequest(request_id="r", validator_binding_id="fixture",
        validator_agent={"package_id": "fixture", "agent_id": "fixture"},
        target={"domain_pack_id": "fixture"}, expected_result_fields={"identifier": "identity.id"})
    runtime = CompactValidatorRuntime([DecisionContract(request, profile_mapped=profile_mapped)],
                                     adapter=Mock(side_effect=AssertionError("no redundant lookup")))
    ref = runtime.workspace.record_lookup("r", call_id="search", attempt=ValidatorLookupAttempt(
        provider="fixture", method="search", query={"mention": "example"}, result_count=2,
        outcome="ambiguous", coverage={"returned_count": 2}), records=[
            CanonicalValidatorRecord(candidate=ValidatorCandidate(value="EX:1"),
                                     values={"identifier": "EX:1"}),
            CanonicalValidatorRecord(candidate=ValidatorCandidate(value="EX:2"),
                                     values={"identifier": "EX:2"}),
        ])[0]
    decision = {"request_id": "r", "status": "resolved", "explanation": "The supplied context supports this record.",
                "unresolved_questions": ["Could supplementary context clarify the supplier?"],
                "candidates": [{"record_ref": ref, "disposition": "selected", "explanation": "Source context."}],
                "slots": {}}
    state = dispatch._ValidatorFinalizationState()
    if mode == "single":
        tool = dispatch._build_finalize_validator_result_tool(request, finalization_state=state,
            function_tool_factory=function_tool, compact_runtime=runtime, profile_mapped=profile_mapped)
    elif mode == "batch":
        jobs = [SimpleNamespace(request=request, match=SimpleNamespace(binding=SimpleNamespace(
            raw={"profile_validation": profile_mapped}))) ]
        tool = dispatch._build_finalize_validator_batch_results_tool(jobs, finalization_state=state,
            function_tool_factory=function_tool, compact_runtime=runtime)
    else:
        state = streaming_tools._StructuredSpecialistFinalizationState(required=True,
            tool_name="finalize_fixture", agent_name="Fixture", output_type_name="Fixture")
        tool = streaming_tools._build_structured_specialist_finalization_tool(
            expected_output_type=DomainValidatorResultBase, finalization_state=state,
            tool_calls=[], live_evidence_records=[], function_tool_factory=function_tool,
            compact_runtime=runtime)

    async def submit(value):
        arguments = json.dumps({"results": [value]} if mode == "batch" else {"result": value})
        context = ToolContext(context=None, tool_name=tool.name, tool_call_id="finalize", tool_arguments=arguments)
        return await tool.on_invoke_tool(context, arguments)

    rejected = await submit(decision)
    assert rejected["status"] == "rejected"
    guidance = " ".join(rejected["repair_instructions"])
    assert "Permitted decision fields" in guidance
    assert "Preserve your scientific judgment" in guidance
    assert "Return exactly the DomainValidatorResultBase" not in guidance
    assert "Copy request_id, validator_binding_id" not in guidance
    incomplete = (state.incomplete_payload if mode == "streaming" else
                  (state.incomplete_results[0].model_dump() if mode == "batch" else state.incomplete_result.model_dump()))
    assert incomplete["status"] == "resolved"
    assert incomplete["unresolved_questions"] == decision["unresolved_questions"]
    assert incomplete["output_issues"]
    decision["slots"] = {"identifier": {"kind": "record", "record_ref": ref, "field": "identifier"}}
    accepted = await submit(decision)
    assert accepted["status"] == "accepted"
    result = (state.accepted_payload if mode == "streaming" else
              (state.accepted_results[0].model_dump() if mode == "batch" else state.accepted_result.model_dump()))
    assert result["status"] == "resolved"
    assert result["output_issues"] == []
    assert result["unresolved_questions"] == decision["unresolved_questions"]
    assert result["resolved_values"] == {"identifier": "EX:1"}
    assert result["lookup_attempts"][0]["outcome"] == "ambiguous"
    assert result["lookup_attempts"][0]["result_count"] == 2
    assert result["lookup_attempts"][0]["coverage"] == {"returned_count": 2}
    if profile_mapped:
        assert result["resolved_objects"] == []
    summary = streaming_tools._domain_validator_supervisor_summary(result,
        expected_output_type=DomainValidatorResultBase, result_ref="validator-result:fixture")
    handoff = json.loads(summary.split("\n", 1)[1])
    assert handoff["follow_up_question_count"] == 1
    assert handoff["output_complete"] is True
    assert handoff["read"]["detail_path"] == "original.result"
    restored = DomainValidatorResultBase.model_validate_json(json.dumps(result))
    assert restored.unresolved_questions == decision["unresolved_questions"]
