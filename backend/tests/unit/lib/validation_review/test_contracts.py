from unittest.mock import Mock

import pytest
from src.lib.validation_review.policy import ReviewLimits
from src.lib.validation_review.tools import create_tools
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_history.store import HistoryScope


def test_structural_limits_remain_but_token_pools_are_retired(monkeypatch):
    for key in ("REVIEWER_TURNS", "REVIEWER_TOOLS", "DEADLINE_SECONDS"):
        monkeypatch.delenv("VALIDATION_REVIEW_" + key, raising=False)
    policy = ReviewLimits.configured()
    allowances = policy.allowances(targets=32, episodes=1, extras=16)
    assert allowances == {"extra_attempts": 16, "reviewer_turns": None, "reviewer_tools": None}
    assert policy.attempts_per_target == policy.extraction_attempts_per_entry == 2
    monkeypatch.setenv("VALIDATION_REVIEW_TOKENS_PER_TARGET", "1")
    monkeypatch.setenv("VALIDATION_REVIEW_WARNING_PERCENT", "65")
    changed = ReviewLimits.configured()
    assert changed.allowances(targets=32, episodes=1, extras=16) == allowances
    assert changed.warning_percent == 65
    monkeypatch.setenv("VALIDATION_REVIEW_CRITICAL_PERCENT", "60")
    with pytest.raises(ValueError):
        ReviewLimits.configured()


def test_tool_schema_cannot_choose_actor_episode_or_validator():
    context = ReviewContext(
        HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
        "episode",
        "reviewer",
    )
    tools = create_tools(context, checkpoint_provider=Mock())
    assert len(tools) == 9
    for tool in tools:
        fields = tool.params_json_schema["properties"]
        assert not {
            "actor_id",
            "owner_id",
            "episode_id",
            "validator_agent",
            "receipt",
            "scope_id",
        } & set(fields)
        assert tool.params_json_schema["additionalProperties"] is False



def test_locked_review_protocol_is_separate_from_editable_policy():
    from src.lib.config.agent_loader import get_agent_definition
    from src.lib.prompts.assembly import _build_core_generated_content
    from src.lib.validation_review.protocol import REVIEW_PROTOCOL

    definition = get_agent_definition("validation_reviewer")
    assert definition is not None
    assert REVIEW_PROTOCOL.strip() in _build_core_generated_content(definition)


@pytest.mark.asyncio
async def test_record_source_tool_binds_server_scope_and_episode(monkeypatch):
    from agents.tool_context import ToolContext
    from src.lib.validation_review import record_source

    context = ReviewContext(
        HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
        "episode", "reviewer",
    )
    snapshot = Mock(return_value={"fact_id": "exact-source", "sha256": "digest"})
    monkeypatch.setattr(record_source, "snapshot", snapshot)
    tool = next(t for t in create_tools(context, checkpoint_provider=Mock())
                if t.name == "get_validation_record_source")
    result = await tool.on_invoke_tool(
        ToolContext(context=None, tool_name=tool.name, tool_call_id="read-source",
                    tool_arguments='{"target_id":"record"}'),
        '{"target_id":"record"}',
    )
    assert result == {"fact_id": "exact-source", "sha256": "digest"}
    snapshot.assert_called_once_with(context.history_scope, "episode", target_id="record")


def test_sentry_reports_only_sanitized_error_once(monkeypatch):
    from src.lib.validation_review.audit import report_operational_failure

    reports = []
    monkeypatch.setattr(
        "src.lib.observability.runtime.report_runtime_exception",
        lambda error, **kwargs: reports.append(error) or True,
    )
    original = RuntimeError("PRIVATE_PAPER_PROVIDER_MESSAGE")
    report_operational_failure(original)
    report_operational_failure(original)
    assert len(reports) == 1
    assert "PRIVATE" not in str(reports[0])
    assert reports[0].__cause__ is None and reports[0].__context__ is None


def test_review_budget_counts_are_not_redacted_as_secrets():
    from src.lib.openai_agents.extraction_trace_events import _is_secret_key

    for name in (
        "reviewer_tokens",
        "initial_tokens",
        "reconsideration_tokens",
        "reported_tokens",
        "tokens_per_target",
        "tokens_per_extra",
    ):
        assert not _is_secret_key(name)
    assert _is_secret_key("api_token") and _is_secret_key("lease_token")


@pytest.mark.asyncio
async def test_tool_conflicts_and_target_limits_are_recoverable(monkeypatch):
    import json
    from agents.tool_context import ToolContext
    from src.lib.validation_review import reads, scheduling
    from src.lib.validation_review.admission import ReviewStopped
    from src.lib.validation_history import store

    context = ReviewContext(
        HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
        "episode",
        "reviewer",
    )
    tools = {t.name: t for t in create_tools(context, checkpoint_provider=Mock())}
    monkeypatch.setattr(
        reads, "read", Mock(side_effect=store.HistoryConflict("stale SHA"))
    )
    result = await tools["read_validation_history"].on_invoke_tool(
        ToolContext(
            context=None,
            tool_name="review_tool",
            tool_call_id="test",
            tool_arguments="{}",
        ),
        json.dumps({"fact_id": "f", "sha256": "s", "offset": 0}),
    )
    assert result["state"] == "conflict" and not result["applied"]
    monkeypatch.setattr(
        scheduling,
        "reconsider",
        Mock(side_effect=ReviewStopped("scientific_attempts_exhausted", resource="t")),
    )
    args = json.dumps(
        {
            "operation_id": "op",
            "parent_decision_id": "d",
            "target_ids": ["t"],
            "instructions": "retry",
            "evidence_refs": [],
        }
    )
    result = await tools["request_validation_reconsideration"].on_invoke_tool(
        ToolContext(
            context=None,
            tool_name="review_tool",
            tool_call_id="test",
            tool_arguments="{}",
        ),
        args,
    )
    assert result["state"] == "not_applied" and result["targets"] == ["t"]
    monkeypatch.setattr(
        scheduling, "reconsider", Mock(side_effect=ReviewStopped("expired"))
    )
    with pytest.raises(ReviewStopped, match="expired"):
        await tools["request_validation_reconsideration"].on_invoke_tool(
            ToolContext(
                context=None,
                tool_name="review_tool",
                tool_call_id="test",
                tool_arguments="{}",
            ),
            args,
        )


@pytest.mark.asyncio
async def test_executor_renews_until_work_finishes_and_cancels_on_lost_ownership(
    monkeypatch,
):
    import asyncio
    from src.lib.validation_review.execution import _while_owned
    from src.lib.validation_review import work
    from src.lib.validation_review.admission import ReviewStopped

    pulses = []

    def heartbeat(*args):
        pulses.append(args)
        if len(pulses) == 2:
            raise ReviewStopped("cancelled")

    monkeypatch.setattr(work, "heartbeat", heartbeat)
    cancelled = asyncio.Event()

    async def long_call():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(ReviewStopped, match="cancelled"):
        await _while_owned(long_call(), "scope", "work", "fence", 0.01)
    assert cancelled.is_set() and len(pulses) == 2


def test_only_sdk_wrappers_are_transparent_to_expected_stops():
    from agents import UserError
    from src.lib.validation_review.admission import ReviewStopped
    from src.lib.validation_review.stops import review_stop

    stop = ReviewStopped("expired")
    wrapper = UserError("tool failed")
    wrapper.__cause__ = stop
    assert review_stop(wrapper) is stop
    bug = RuntimeError("database failed while handling stop")
    bug.__context__ = stop
    assert review_stop(bug) is None
    wrapper.__cause__ = wrapper
    assert review_stop(wrapper) is None


def test_extraction_followup_limit_is_separate_and_configurable(monkeypatch):
    monkeypatch.delenv("VALIDATION_REVIEW_EXTRACTION_ATTEMPTS_PER_ENTRY", raising=False)
    original = ReviewLimits.configured()
    assert original.extraction_attempts_per_entry == 2
    monkeypatch.setenv("VALIDATION_REVIEW_EXTRACTION_ATTEMPTS_PER_ENTRY", "3")
    changed = ReviewLimits.configured()
    assert changed.extraction_attempts_per_entry == 3
    assert changed.attempts_per_target == original.attempts_per_target
    assert changed.allowances(targets=2, episodes=1, extras=16) == original.allowances(
        targets=2, episodes=1, extras=16
    )


def test_review_tool_boundary_rejects_package_paper_capabilities(monkeypatch):
    from src.lib.packages import tool_roles

    monkeypatch.setattr(tool_roles, "validator_forbidden_tool_names", lambda: frozenset({"custom_paper_reader"}))
    with pytest.raises(ValueError, match="cannot directly read papers"):
        tool_roles.require_supervisor_review_tool_boundary(["read_validation_history", "custom_paper_reader"])
    tool_roles.require_supervisor_review_tool_boundary(["read_validation_history", "record_validation_review"])


@pytest.mark.asyncio
async def test_followup_tools_bind_runtime_authority_and_forward_exact_selection():
    import json
    from unittest.mock import AsyncMock
    context = ReviewContext(HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
                            "episode", "reviewer")
    extraction, selection, assessment = AsyncMock(), AsyncMock(), AsyncMock(return_value={"state": "completed"})
    tools = {tool.name: tool for tool in create_tools(context, checkpoint_provider=Mock(),
        request_extraction=extraction, select_source=selection, assess_source=assessment)}
    assert len(tools) == 13
    for tool in tools.values():
        assert not {"actor_id", "episode_id", "validator_agent", "receipt", "scope_id", "extractor_agent"}.intersection(
            tool.params_json_schema["properties"])
    arguments = {"operation_id": "retry", "selection_id": "approved-selection",
                 "target_ids": ["changed-target"], "instructions": "Assess the Methods clarification"}
    from agents.tool_context import ToolContext
    await tools["validate_selected_extraction_source"].on_invoke_tool(
        ToolContext(context=None, tool_name="validate_selected_extraction_source", tool_call_id="test",
                    tool_arguments=json.dumps(arguments)), json.dumps(arguments))
    assessment.assert_awaited_once_with(**arguments)
    retirement = {"operation_id": "retire", "contribution": {"fact_id": "proposal", "sha256": "exact"},
                  "decision": "approve", "expected_source_sha": "current", "reason": "Outside scope"}
    await tools["decide_record_retirement"].on_invoke_tool(
        ToolContext(context=None, tool_name="decide_record_retirement", tool_call_id="retire-test",
                    tool_arguments=json.dumps(retirement)), json.dumps(retirement))
    selection.assert_awaited_once_with(operation_id="retire", contribution_ref=retirement["contribution"],
        retirement_decision="approve", expected_source_sha="current", reason="Outside scope")
    with pytest.raises(ValueError, match="must be wired together"):
        create_tools(context, checkpoint_provider=Mock(), request_extraction=extraction, select_source=selection)


def test_review_usage_summary_remains_visible_without_exposing_nested_secrets():
    from src.lib.openai_agents.extraction_trace_events import _redact_value
    summary = _redact_value({"provider_usage": {"reported_tokens": {"initial_tokens": 17123, "api_token": "private"}}})
    assert summary["provider_usage"]["reported_tokens"]["initial_tokens"] == 17123
    assert summary["provider_usage"]["reported_tokens"]["api_token"] != "private"


def test_model_facing_decision_contract_explains_whole_target_and_component_ids():
    from src.lib.validation_review.tools import runtime_tool_contract
    from src.lib.validation_review.protocol import REVIEW_PROTOCOL

    tool = next(t for t in runtime_tool_contract() if t["name"] == "record_validation_review")
    schema = tool["parameters"]
    entries, = (variant for variant in schema["properties"]["entries"]["anyOf"] if variant.get("type") == "array")
    entry_ref = entries["items"]["$ref"]
    entry = schema["$defs"][entry_ref.rsplit("/", 1)[1]]["properties"]
    assert "input.target_id" in entry["target_id"]["description"]
    unit_description = entry["decision_unit_id"]["description"]
    for text in ("coverage", "decision_unit_id equals target_id", "whole-target", "covered child"):
        assert text in unit_description
    assert "manifest" in entry["expected_version"]["description"]
    assert "not its completion_ref" in entry["logical_attempt_id"]["description"]
    for text in ("decision_unit_id equals target_id", "not missing", "covered child"):
        assert text in tool["description"]
        assert text in REVIEW_PROTOCOL


@pytest.mark.parametrize("raw,expected", [("", None), ("  ", None), ("503", 503)])
def test_optional_orchestration_caps_preserve_scientific_attempts(monkeypatch, raw, expected):
    for name in ("REVIEWER_TURNS", "REVIEWER_TOOLS", "DEADLINE_SECONDS"):
        monkeypatch.setenv("VALIDATION_REVIEW_" + name, raw)
    policy = ReviewLimits.configured()
    assert policy.reviewer_turns == policy.reviewer_tools == policy.deadline_seconds == expected
    assert policy.attempts_per_target == policy.extraction_attempts_per_entry == 2
    assert policy.allowances(targets=82, episodes=2, extras=16) == {
        "extra_attempts": 16,
        "reviewer_turns": None if expected is None else expected * 2,
        "reviewer_tools": None if expected is None else expected * 2,
    }


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "1.5"])
@pytest.mark.parametrize("name", ["REVIEWER_TURNS", "REVIEWER_TOOLS", "DEADLINE_SECONDS"])
def test_operator_caps_reject_invalid_values(monkeypatch, name, raw):
    monkeypatch.setenv("VALIDATION_REVIEW_" + name, raw)
    with pytest.raises(ValueError):
        ReviewLimits.configured()


@pytest.mark.parametrize("value,expected", [("0", 0), ("1", 1), ("3", 3)])
def test_corrective_nudges_are_not_frozen_scientific_policy(monkeypatch, value, expected):
    from src.lib.openai_agents.config import get_validation_review_initial_corrective_continuations

    before = ReviewLimits.configured().snapshot()
    monkeypatch.setenv("VALIDATION_REVIEW_INITIAL_CORRECTIVE_CONTINUATIONS", value)
    assert get_validation_review_initial_corrective_continuations() == expected
    assert ReviewLimits.configured().snapshot() == before


@pytest.mark.parametrize("value", ["-1", "nonsense", "1.5"])
def test_invalid_corrective_nudge_setting_is_rejected(monkeypatch, value):
    from src.lib.openai_agents.config import get_validation_review_initial_corrective_continuations

    monkeypatch.setenv("VALIDATION_REVIEW_INITIAL_CORRECTIVE_CONTINUATIONS", value)
    with pytest.raises(ValueError):
        get_validation_review_initial_corrective_continuations()


@pytest.mark.asyncio
async def test_source_prerequisite_retry_is_recoverable_at_bound_tool_boundary():
    import json
    from unittest.mock import AsyncMock
    from agents.tool_context import ToolContext
    from src.lib.validation_review.admission import ReviewStopped

    context = ReviewContext(HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
                            "episode", "reviewer")
    assessment = AsyncMock(side_effect=[
        ReviewStopped("prerequisite_reconsideration_required", resource="dependent"),
        {"state": "completed"},
    ])
    tools = {tool.name: tool for tool in create_tools(context, checkpoint_provider=Mock(),
        request_extraction=AsyncMock(), select_source=AsyncMock(), assess_source=assessment)}
    tool = tools["validate_selected_extraction_source"]
    arguments = {"operation_id": "retry", "selection_id": "selection",
                 "target_ids": ["dependent"], "instructions": "Assess selected source"}

    async def invoke():
        encoded = json.dumps(arguments)
        return await tool.on_invoke_tool(ToolContext(context=None, tool_name=tool.name,
            tool_call_id=arguments["operation_id"], tool_arguments=encoded), encoded)

    result = await invoke()
    assert result == {"state": "not_applied", "code": "prerequisite_reconsideration_required",
                      "resource": "dependent", "applied": [], "targets": ["dependent"]}
    arguments.update(operation_id="independent", target_ids=["independent"])
    assert await invoke() == {"state": "completed"}
    assert assessment.await_count == 2


def test_protocol_v6_reads_the_packet_once_and_keeps_record_specific_reasons():
    from src.lib.validation_review.protocol import REVIEW_PROTOCOL
    from src.lib.validation_review.tools import runtime_tool_contract

    tools = {tool["name"]: tool for tool in runtime_tool_contract()}
    assert 'version="6"' in REVIEW_PROTOCOL
    assert tools["read_review_packet"]["parameters"]["required"] == ["packet_id", "page"]
    protocol = " ".join(REVIEW_PROTOCOL.split())
    for text in ("Call read_review_packet with no packet_id", "read every page of that packet before deciding",
                 "do not read those records or answers again", "Decide every D handle",
                 "in one record_validation_review call with the packet_id and packet_decisions",
                 "split only when they exceed decision_entries", "reason specific to each record",
                 "never a generic reason", "because its neighbors were acceptable",
                 "nothing in that call applies", "Never accept an answer you have not read",
                 "Matching text is not enough", "records that differ from the rest",
                 "defer and investigate with targeted follow-up", "These are cues, not exclusions",
                 "one request_targeted_extraction covering all of them",
                 "do not leave a record deferred over remaining uncertainty",
                 "a correct identity does not make a record that is out of scope acceptable",
                 "using the exact-history tools and explicit entries"):
        assert text in protocol, text
    assert "group_limit" not in protocol and "evidence_group" not in protocol
    for text in ("inspect previous decisions and selected prerequisites", "earlier_decisions per component",
                 "each selection it used and the answer or source that selection chose",
                 "need not trigger another validation", "judge its record on the evidence"):
        assert text in protocol, text
    assert "Accept caveated or unresolved answers when appropriate" not in protocol
    save = tools["record_validation_review"]
    assert "reason specific to each record" in save["description"]
    assert "packet_id" in save["description"] and "packet_decisions" in save["description"]
    assert set(save["parameters"]["required"]) == {"operation_id", "packet_id", "packet_decisions", "entries"}
    decision = save["parameters"]["$defs"]["PacketDecision"]
    assert set(decision["properties"]) == {"handle", "action", "reason", "evidence", "absence_assessment",
                                           "unresolved_fields"}
    assert decision["additionalProperties"] is False
    assert "read_validation_history" in tools["get_outstanding_validation_reviews"]["description"]


@pytest.mark.asyncio
async def test_save_takes_exactly_one_decision_form(monkeypatch):
    import json
    from agents.tool_context import ToolContext
    from src.lib.validation_review import decisions

    context = ReviewContext(HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
                            "episode", "reviewer")
    called = Mock(side_effect=AssertionError("must not save"))
    monkeypatch.setattr(decisions, "record_decisions", called)
    monkeypatch.setattr(decisions, "record_packet_decisions", called)
    tool = next(t for t in create_tools(context, checkpoint_provider=Mock())
                if t.name == "record_validation_review")
    decision = {"handle": "D1", "action": "accept", "reason": "Supported.",
                "absence_assessment": None, "unresolved_fields": None}
    entry = {"target_id": "t", "decision_unit_id": "t", "expected_version": 0, "action": "accept",
             "reason": "Supported.", "logical_attempt_id": None, "extraction_ref": "b",
             "applicability_ref": None, "absence_assessment": None, "unresolved_fields": None}
    for arguments in ({"packet_id": "p", "packet_decisions": [decision], "entries": [entry]},
                      {"packet_id": None, "packet_decisions": [decision], "entries": None},
                      {"packet_id": "p", "packet_decisions": None, "entries": [entry]},
                      {"packet_id": None, "packet_decisions": None, "entries": None}):
        encoded = json.dumps({"operation_id": "op", **arguments})
        result = await tool.on_invoke_tool(ToolContext(context=None, tool_name=tool.name, tool_call_id="c",
                                                       tool_arguments=encoded), encoded)
        assert result["state"] == "conflict" and "Send either" in result["message"], arguments
    assert not called.called


def test_source_group_limit_is_configured_and_must_be_positive(monkeypatch):
    from src.lib.openai_agents.config import get_validation_review_source_group_records

    monkeypatch.delenv("VALIDATION_REVIEW_SOURCE_GROUP_RECORDS", raising=False)
    assert get_validation_review_source_group_records() == 12
    monkeypatch.setenv("VALIDATION_REVIEW_SOURCE_GROUP_RECORDS", "7")
    assert get_validation_review_source_group_records() == 7
    monkeypatch.setenv("VALIDATION_REVIEW_SOURCE_GROUP_RECORDS", "0")
    with pytest.raises(ValueError):
        get_validation_review_source_group_records()


def test_source_group_limit_reaches_every_deployed_service_that_sets_review_limits():
    from pathlib import Path

    root = Path(__file__).resolve().parents[5]
    composes = [path for path in (root / name for name in ("docker-compose.yml", "docker-compose.production.yml"))
                if path.is_file()]
    if not composes:
        pytest.skip("Compose files are not mounted in this test image")
    for path in composes:
        text = path.read_text()
        assert text.count("VALIDATION_REVIEW_SOURCE_GROUP_RECORDS") == text.count("VALIDATION_REVIEW_DECISION_ENTRIES") > 0


# v0.11.1 to v0.11.3 targeted follow-up protocol text, pinned in saved extractor contracts.
TARGETED_PROTOCOL_V2_SHA256 = "0308d8113148d065863b5c67f5bc6f0f0a82876d458ea1f4aac558bfd647ce0e"


def test_targeted_follow_up_protocol_keeps_the_saved_extractor_contract_text():
    """Saved extractor contracts pin this text; changing it would end older sessions' follow-ups."""
    import hashlib
    from src.lib.validation_review.protocol import TARGETED_EXTRACTION_PROTOCOL

    assert 'version="2"' in TARGETED_EXTRACTION_PROTOCOL
    assert hashlib.sha256(TARGETED_EXTRACTION_PROTOCOL.encode()).hexdigest() == TARGETED_PROTOCOL_V2_SHA256


def test_review_and_followup_text_state_reject_retire_and_follow_up_rules():
    from src.lib.validation_review.protocol import REVIEW_PROTOCOL
    from src.lib.validation_review.tools import runtime_tool_contract

    protocol = " ".join(REVIEW_PROTOCOL.split())
    for text in ("Each record ends accepted, retired or left pending",
                 "A record must be shown in the paper to answer the question",
                 "Reject records an opinion and does not remove an original record; it stays pending",
                 "retire it with a reason", "a curator can restore it",
                 "Each record gets one targeted follow-up per review round; a curator-requested round grants a fresh one",
                 "retire on any of its handles retires the whole record",
                 "Each packet record shows follow_up_available; check it before deferring for a follow-up",
                 "If nothing in the paper, including the record's original evidence and any follow-up, "
                 "shows that it answers the question, retire it",
                 "A follow-up that finds nothing new about a field is not a finding about the record's scope",
                 "In a curator-requested round, do not retire the records the curator asked to re-review",
                 "lists what is open and what clears it under blocking"):
        assert text in protocol, text
    assert "If a follow-up finds nothing showing" not in protocol
    tools = {tool["name"]: tool for tool in runtime_tool_contract()}
    save = " ".join(tools["record_validation_review"]["description"].split())
    for text in ("Each record ends accepted, retired or left pending",
                 "it does not remove an original record, which stays pending",
                 "use action retire on any of its handles with a reason", "a curator can restore it", "blocking",
                 "assess them with validate_selected_extraction_source and the returned selection_id",
                 "Send all retirements from a packet in one save; a retirement changes the packet's source",
                 "Retire a record before deciding validator answers whose inputs include it; "
                 "read a fresh packet after the retirement"):
        assert text in save, text
    status = " ".join(tools["get_validation_review_status"]["description"].split())
    assert "blocking lists what still blocks readiness" in status
    decision = tools["record_validation_review"]["parameters"]["$defs"]["PacketDecision"]["properties"]
    assert "retire" in decision["action"]["enum"]
    assert "does not remove an original record" in decision["action"]["description"]
    assert "Only with retire" in decision["evidence"]["description"]
    assert "classification_notes[1]" in decision["unresolved_fields"]["description"]
    assessment = tools["record_validation_review"]["parameters"]["$defs"]["UnresolvedFieldAssessment"]
    assert "classification_notes[1]" in assessment["properties"]["field_path"]["description"]
    assert "record_validation_review" in tools["decide_record_retirement"]["description"]
    followup_tool = " ".join(tools["request_targeted_extraction"]["description"].split())
    assert "Each record gets one follow-up per review round; a curator-requested round grants a fresh one" in followup_tool
    assert "exhausted" in followup_tool


@pytest.mark.asyncio
async def test_a_save_with_retire_goes_through_the_bound_source_boundary():
    import json
    from unittest.mock import AsyncMock
    from agents.tool_context import ToolContext

    context = ReviewContext(HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
                            "episode", "reviewer")
    selection = AsyncMock(return_value={"ready": True, "applied": []})
    checkpoint = Mock(side_effect=AssertionError("the source boundary loads its own checkpoint"))
    tool = next(t for t in create_tools(context, checkpoint_provider=checkpoint, request_extraction=AsyncMock(),
                                        select_source=selection, assess_source=AsyncMock())
                if t.name == "record_validation_review")
    decisions = [{"handle": "D1", "action": "retire", "reason": "A control, not asked for.", "evidence": ["E2"],
                  "absence_assessment": None, "unresolved_fields": None},
                 {"handle": "D2", "action": "accept", "reason": "Shown in Methods.", "evidence": None,
                  "absence_assessment": None, "unresolved_fields": None}]
    encoded = json.dumps({"operation_id": "op", "packet_id": "p", "packet_decisions": decisions, "entries": None})
    result = await tool.on_invoke_tool(ToolContext(context=None, tool_name=tool.name, tool_call_id="c",
                                                   tool_arguments=encoded), encoded)
    assert result == {"ready": True, "applied": []}
    selection.assert_awaited_once_with(operation_id="op", packet_id="p", packet_decisions=decisions)


def test_packet_retire_names_the_whole_record_and_keeps_cited_quotes():
    from src.lib.validation_review.decisions import PacketDecision, _expand

    packet = {"handles": {
        "D1": {"record": "R1", "target_id": "r1", "decision_unit_id": "r1", "expected_version": 2,
               "extraction_ref": "baseline-sha"},
        "D2": {"record": "R2", "target_id": "b2", "decision_unit_id": "b2", "expected_version": 0,
               "logical_attempt_id": "attempt"},
        "D3": {"record": "R2", "target_id": "b3", "decision_unit_id": "b3-identity", "expected_version": 1,
               "logical_attempt_id": "other-attempt"},
        "D4": {"record": None, "target_id": "envelope", "decision_unit_id": "envelope", "expected_version": 0,
               "logical_attempt_id": "envelope-attempt"}},
        "records": [{"record": "R1", "target_id": "r1"}, {"record": "R2", "target_id": "r2"},
                    {"record": None}],
        "evidence": {"E1": {"verified_quote": "The genetic control were white-eyed flies.", "page": 8},
                     "E2": {"same_quote_as": "E1", "entity": "white control"}}}
    retire = PacketDecision(handle="D1", action="retire", reason="A control, not a transgenic fly.", evidence=["E2"])
    handles, entries, retirements = _expand(packet, [retire])
    assert handles == ["D1"] and entries == [{"target_id": "r1", "decision_unit_id": "r1", "action": "retire"}]
    assert retirements == [{"target_id": "r1", "expected_version": 2, "reason": "A control, not a transgenic fly.",
                            "evidence": {"E2": packet["evidence"]["E2"], "E1": packet["evidence"]["E1"]}}]
    validated = [PacketDecision(handle="D2", action="retire", reason="A mutant, not transgenic."),
                 PacketDecision(handle="D3", action="retire", reason="A mutant, not transgenic.")]
    _, entries, retirements = _expand(packet, validated)
    assert [entry["target_id"] for entry in entries] == ["r2", "r2"], "every handle names the record root"
    assert retirements == [{"target_id": "r2", "expected_version": None, "reason": "A mutant, not transgenic.",
                            "evidence": {}}], "one retirement per record; the source digest pins its lifecycle"
    with pytest.raises(ValueError, match="D3: retire removes the whole record R2"):
        _expand(packet, [validated[0], PacketDecision(handle="D3", action="accept", reason="Shown.")])
    with pytest.raises(ValueError, match="D4: this item belongs to no record"):
        _expand(packet, [PacketDecision(handle="D4", action="retire", reason="Outside the question.")])


@pytest.mark.asyncio
async def test_follow_up_results_name_records_with_no_follow_up_left():
    import json
    from unittest.mock import AsyncMock
    from agents.tool_context import ToolContext
    from src.lib.validation_review.admission import ReviewStopped

    context = ReviewContext(HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
                            "episode", "reviewer")
    spent = ReviewStopped("extraction_attempts_exhausted", resource="a")
    spent.exhausted = ["a", "b", "c"]
    partial = {"state": "completed", "work_id": "w", "contributions": [{"fact_id": "f", "sha256": "s"}],
               "exhausted": ["a"], "exhausted_note": "decide them now"}
    extraction = AsyncMock(side_effect=[spent, partial])
    tool = next(t for t in create_tools(context, checkpoint_provider=Mock(), request_extraction=extraction,
                                        select_source=AsyncMock(), assess_source=AsyncMock())
                if t.name == "request_targeted_extraction")
    arguments = {"operation_id": "op", "parent_decision_id": "decision", "entry_ids": ["a", "b", "c"],
                 "instructions": "Check Methods."}
    encoded = json.dumps(arguments)

    async def invoke():
        return await tool.on_invoke_tool(ToolContext(context=None, tool_name=tool.name, tool_call_id="c",
                                                     tool_arguments=encoded), encoded)

    assert await invoke() == {"state": "not_applied", "code": "extraction_attempts_exhausted", "resource": "a",
                              "applied": [], "targets": [], "exhausted": ["a", "b", "c"]}
    assert await invoke() == partial


def test_every_manifest_blocker_code_says_what_clears_it():
    """Read the codes the manifest can emit from its source, so a new code fails here."""
    import ast
    import inspect
    from typing import get_args
    from src.lib.validation_history import selections
    from src.lib.validation_review.decisions import _CLEARED_BY
    from src.schemas.validation_history import ReviewObligation

    tree = ast.parse(inspect.getsource(selections))
    codes, reasons = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(value, ast.Constant):
                    if key.value == "code":
                        codes.add(value.value)
                    elif key.value == "reason" and isinstance(value.value, str):
                        reasons.add(value.value)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "reason" and isinstance(node.value.value, str)):
                    reasons.add(node.value.value)
    reasons.discard("Explicit review of this validation run")  # An episode reason, not a unit reason.
    assert {"unreviewed", "stale", "incomplete", "retired", "historical_review_unknown"} <= reasons
    assert {"episode_review_pending", "execution_pending", "answer_review_pending",
            "configured_validator_unavailable", "required_validator_unavailable", "document_retired"} <= codes
    modes = get_args(ReviewObligation.model_fields["mode"].annotation)
    for code in codes | reasons:
        for mode in modes:
            assert (code, mode) in _CLEARED_BY or code in _CLEARED_BY, (code, mode)
    assert all("nothing" not in text for key, text in _CLEARED_BY.items() if key != "document_retired")
    assert "acknowledge_failure" in _CLEARED_BY["execution_pending"]
