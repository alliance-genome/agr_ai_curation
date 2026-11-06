"""Fan-out builder extraction: plan contract, code counting, and a fake-model run.

The integration tests drive the real Agents SDK runner and ``run_specialist_with_events``
with a scripted provider model. Nothing here calls a paid model.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from agents import Agent, FunctionTool, ModelSettings, RunConfig, function_tool
from agents.items import ModelResponse
from agents.strict_schema import ensure_strict_json_schema
from openai.types.responses import ResponseFunctionToolCall

from src.lib.openai_agents import extraction_builder_workspace as builder
from src.lib.openai_agents import fanout_extraction as fanout
from src.lib.openai_agents import streaming_tools
from src.lib.openai_agents.tools import evidence_workspace
from src.lib.packages import tool_roles
from tests.unit.lib.openai_agents.test_model_request_measurement import (
    FakeResponsesHTTPModel,
    _final,
    _usage,
)

STAGE = "stage_test_object"
FINALIZE = "finalize_test_extraction"
DISCARD = "discard_test_object"


# ---------------------------------------------------------------------------
# Plan contract and code counting
# ---------------------------------------------------------------------------


def _walk_objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from _walk_objects(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _walk_objects(value)


def test_plan_tool_schema_is_strict_and_carries_no_field_values():
    schema = fanout.submit_plan_parameters_schema()

    assert ensure_strict_json_schema(json.loads(json.dumps(schema))) == schema
    for obj in _walk_objects(schema):
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])
    assert set(schema["properties"]) == {"notes", "items"}
    item = schema["properties"]["items"]["items"]
    assert set(item["properties"]) == {"label", "chunks"}
    assert item["properties"]["chunks"]["items"] == {"type": "integer"}


def test_parse_plan_keeps_model_words_and_checks_only_structure():
    plan = fanout.parse_plan_arguments(json.dumps({
        "notes": ["  Lines came from the authors' lab. ", ""],
        "items": [{"label": " Act5C-GAL4 ", "chunks": [4, 9, 4]}, {"label": "UAS-GFP", "chunks": []}],
    }))

    assert plan.notes == ("Lines came from the authors' lab.",)
    assert plan.items == (
        fanout.PlanItem(label="Act5C-GAL4", chunks=(4, 9)),
        fanout.PlanItem(label="UAS-GFP", chunks=()),
    )
    assert fanout.parse_plan_arguments('{"notes": [], "items": []}').items == ()


@pytest.mark.parametrize("arguments", [
    "not json",
    json.dumps({"items": []}),
    json.dumps({"notes": [], "items": [], "extra": 1}),
    json.dumps({"notes": "one", "items": []}),
    json.dumps({"notes": [], "items": [{"label": "", "chunks": [1]}]}),
    json.dumps({"notes": [], "items": [{"label": "a", "chunks": [-1]}]}),
    json.dumps({"notes": [], "items": [{"label": "a", "chunks": [True]}]}),
    json.dumps({"notes": [], "items": [{"label": "a", "chunks": [1], "source": "x"}]}),
])
def test_parse_plan_rejects_structural_problems(arguments):
    with pytest.raises(fanout.PlanFormatError):
        fanout.parse_plan_arguments(arguments)


@pytest.mark.parametrize("count,per,maximum,sizes", [
    (0, 5, 8, []),
    (6, 5, 8, [3, 3]),
    (7, 3, 8, [3, 2, 2]),
    (38, 5, 8, [5, 5, 5, 5, 5, 5, 4, 4]),
    (41, 5, 8, [6, 5, 5, 5, 5, 5, 5, 5]),
    (100, 5, 8, [13, 13, 13, 13, 12, 12, 12, 12]),
    (4, 5, 8, [4]),
])
def test_writer_slices_are_contiguous_balanced_and_capped(count, per, maximum, sizes):
    slices = fanout.writer_slices(count, items_per_writer=per, max_writers=maximum)

    assert [len(piece) for piece in slices] == sizes
    assert [position for piece in slices for position in piece] == list(range(count))


def test_fanout_settings_default_off_and_read_overrides(monkeypatch):
    from src.lib.openai_agents import config

    for key in ("EXTRACTION_FANOUT_MIN_ITEMS", "EXTRACTION_FANOUT_ITEMS_PER_WRITER", "EXTRACTION_FANOUT_MAX_WRITERS"):
        monkeypatch.delenv(key, raising=False)
    assert config.get_extraction_fanout_min_items() == 0
    assert config.get_extraction_fanout_items_per_writer() == 5
    assert config.get_extraction_fanout_max_writers() == 8

    monkeypatch.setenv("EXTRACTION_FANOUT_MIN_ITEMS", "6")
    monkeypatch.setenv("EXTRACTION_FANOUT_ITEMS_PER_WRITER", "3")
    monkeypatch.setenv("EXTRACTION_FANOUT_MAX_WRITERS", "12")
    assert config.get_extraction_fanout_min_items() == 6
    assert config.get_extraction_fanout_items_per_writer() == 3
    assert config.get_extraction_fanout_max_writers() == 12


def test_stage_role_comes_from_package_binding_metadata():
    tool_roles.reset_cache()
    try:
        stage_tools = tool_roles.builder_stage_tool_names()
    finally:
        tool_roles.reset_cache()
    assert {"stage_generic_object", "stage_gene_expression_observation"} <= stage_tools
    assert not {name for name in stage_tools if not name.startswith("stage_")}


# ---------------------------------------------------------------------------
# Fake tools and a scripted provider model
# ---------------------------------------------------------------------------


@function_tool(name_override=STAGE)
def _stage_test_object(label: str, evidence_record_ids: list[str], pending_ref_id: str | None = None) -> str:
    """Stage one record."""
    workspace = builder.get_active_extraction_builder_workspace()
    candidate = workspace.stage_candidate(
        id_prefix="obj",
        pending_ref_id=pending_ref_id,
        staged_fields={"label": label},
        evidence_record_ids=evidence_record_ids,
    )
    return json.dumps({"status": "ok", "candidate_id": candidate.candidate_id})


@function_tool(name_override=DISCARD)
def _discard_test_object(candidate_id: str) -> str:
    """Discard one record."""
    builder.get_active_extraction_builder_workspace().discard_candidate(candidate_id, reason="model")
    return json.dumps({"status": "ok"})


def _finalize_tool(calls):
    @function_tool(name_override=FINALIZE)
    def _finalize_test_extraction(candidate_ids: list[str]) -> str:
        """Finalize records (mirrors the shared materialize-then-finalize flow)."""
        workspace = builder.get_active_extraction_builder_workspace()
        calls.append(list(candidate_ids))
        bad = [cid for cid in candidate_ids if workspace.get_candidate(cid).staged_fields["label"] == "bad"]
        if bad:
            issues = [{"reason": "not_a_record", "candidate_id": bad[0], "message": "bad is not a record"}]
            workspace.record_validation_failure(errors=issues, candidate_ids=bad)
            return json.dumps({"status": "error", "issues": issues})
        final_id = f"final-{len(calls)}"
        workspace.upsert_candidate(
            candidate_id=final_id,
            staged_fields={"items": [workspace.get_candidate(cid).staged_fields for cid in candidate_ids]},
            evidence_record_ids=[eid for cid in candidate_ids for eid in workspace.get_candidate(cid).evidence_record_ids],
            status=builder.CANDIDATE_STATUS_VALID,
        )
        workspace.finalize(candidate_ids=[final_id], source_candidate_ids=candidate_ids)
        return json.dumps({"status": "ok"})

    return _finalize_test_extraction


def _tool(name, invoke, *, schema=None):
    return FunctionTool(
        name=name,
        description=f"{name} test tool",
        params_json_schema=schema or {"type": "object", "properties": {}, "additionalProperties": True},
        on_invoke_tool=invoke,
        strict_json_schema=False,
    )


def _source_tools(reads):
    async def read_chunk(_ctx, arguments):
        index = json.loads(arguments)["chunk_index"]
        reads.append(index)
        return {"chunk": {"chunk_index": index, "content": f"passage {index}",
                          "evidence_spans": [{"span_index": 0, "text": f"passage {index}"}]}}

    async def search_document(_ctx, _arguments):
        return {"hits": []}

    async def record_evidence(_ctx, arguments):
        entity = json.loads(arguments)["entity"]
        record = {"status": "verified", "evidence_record_id": f"ev-{entity}", "entity": entity,
                  "verified_quote": f"quote for {entity}", "page": 1, "section": "Methods", "chunk_id": "chunk-1"}
        records = evidence_workspace._ACTIVE_EVIDENCE_RECORDS.get()
        # Like the real tool, re-recording the same evidence updates it in place.
        existing = next((item for item in records if item["evidence_record_id"] == record["evidence_record_id"]), None)
        if existing is None:
            records.append(dict(record))
        else:
            existing.update(record)
        return json.dumps(record)

    async def list_recorded_evidence(_ctx, _arguments):
        return {"records": []}

    return [
        _tool("search_document", search_document),
        _tool("read_chunk", read_chunk),
        _tool("record_evidence", record_evidence),
        _tool("list_recorded_evidence", list_recorded_evidence),
    ]


def _calls(response_id, *calls):
    return ModelResponse(
        output=[
            ResponseFunctionToolCall(id=f"fc_{call_id}", call_id=call_id, name=name,
                                     arguments=json.dumps(arguments), type="function_call")
            for name, call_id, arguments in calls
        ],
        usage=_usage(),
        response_id=response_id,
    )


def _text(item):
    content = item.get("content")
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


class ScriptedFanoutModel(FakeResponsesHTTPModel):
    """Answers as planner, writer or checker from the request it receives.

    State lives in shared containers: the owned writer runner shallow-copies models.
    """

    def __init__(self, scenario):
        super().__init__()
        self.scenario = scenario
        self.requests = []
        self.writer_barrier = {"arrived": 0, "event": None}

    async def get_response(self, system_instructions, input, model_settings, tools, *args, **kwargs):
        writer = self._writer_index(input)
        expected = self.scenario.get("concurrent_writers")
        if writer is not None and expected and self._turn(input, developer=True) == 0:
            barrier = self.writer_barrier
            if barrier["event"] is None:
                barrier["event"] = asyncio.Event()
            barrier["arrived"] += 1
            if barrier["arrived"] == expected:
                barrier["event"].set()
            # Every writer's first call is in flight before any writer continues.
            await asyncio.wait_for(barrier["event"].wait(), timeout=5)
        return await super().get_response(system_instructions, input, model_settings, tools, *args, **kwargs)

    @staticmethod
    def _writer_index(items):
        for item in items:
            if isinstance(item, dict) and item.get("role") == "developer":
                match = re.search(r"you are writer (\d+) of (\d+)", _text(item))
                if match:
                    return int(match.group(1))
        return None

    @staticmethod
    def _turn(items, *, developer=False):
        last = 0
        for position, item in enumerate(items):
            if isinstance(item, dict) and item.get("role") == ("developer" if developer else "user"):
                last = position
        return sum(1 for item in items[last:] if isinstance(item, dict) and item.get("type") == "function_call_output")

    def _next(self, kwargs):
        items = kwargs["input"]
        tool_names = sorted(getattr(tool, "name", "") for tool in kwargs["tools"])
        writer = self._writer_index(items)
        self.requests.append({"writer": writer, "tools": tool_names, "input": items})
        if writer is not None:
            return self._writer_reply(writer, items)
        return self._planner_reply(items)

    def _writer_reply(self, writer, items):
        if writer == self.scenario.get("failing_writer"):
            raise RuntimeError("writer provider failure")
        assignment = next(_text(item) for item in items if isinstance(item, dict) and item.get("role") == "developer")
        labels = re.findall(r"^P\d+\. (.+?) \| chunks:", assignment, flags=re.MULTILINE)
        turn = self._turn(items, developer=True)
        if turn == 0:
            return _calls(f"w{writer}-0", *[
                ("record_evidence", f"w{writer}-ev-{n}", {"entity": label, "selections": [{"chunk_index": 1, "span_index": 0}]})
                for n, label in enumerate(labels)
            ])
        if turn == len(labels):
            if writer == self.scenario.get("short_writer"):
                labels = labels[:1]
            return _calls(f"w{writer}-1", *[
                (STAGE, f"w{writer}-st-{n}", {"label": label, "evidence_record_ids": [f"ev-{label}"]})
                for n, label in enumerate(labels)
            ])
        return _final("done", response_id=f"w{writer}-final")

    def _planner_reply(self, items):
        message = next(_text(item) for item in reversed(items) if isinstance(item, dict) and item.get("role") == "user")
        turn = self._turn(items)
        plan = self.scenario.get("plan")
        if message.startswith("[Planning phase] Read"):
            if turn == 0:
                return _calls("plan-0",
                              ("read_chunk", "plan-read", {"chunk_index": 1}),
                              (STAGE, "plan-early-stage", {"label": "too early", "evidence_record_ids": []}))
            if plan is None or self.scenario.get("first_run_text"):
                return _final("I read the paper.", response_id="plan-text")
            return _calls("plan-1", (fanout.SUBMIT_PLAN_TOOL_NAME, "plan-submit", plan))
        if message.startswith("[Planning phase] You have not"):
            if self.scenario.get("submit_on_reminder") and plan is not None:
                return _calls("plan-2", (fanout.SUBMIT_PLAN_TOOL_NAME, "plan-submit-2", plan))
            return _final("Still no plan.", response_id="plan-text-2")
        if message.startswith("[Plan received]"):
            return _final("Nothing in this paper meets the instructions.", response_id="explain")
        if message.startswith("[Writing phase]"):
            labels = [item["label"] for item in plan["items"]]
            if turn == 0:
                return _calls("stage-0", *[
                    ("record_evidence", f"p-ev-{n}", {"entity": label, "selections": [{"chunk_index": 1, "span_index": 0}]})
                    for n, label in enumerate(labels)
                ])
            if turn == len(labels):
                return _calls("stage-1", *[
                    (STAGE, f"p-st-{n}", {"label": label, "evidence_record_ids": [f"ev-{label}"]})
                    for n, label in enumerate(labels)
                ])
            return _final(f"Staged {len(labels)} records from the paper.", response_id="stage-final")
        if message.startswith("[Check phase]"):
            missing = self.scenario.get("tidy_stages")
            if turn == 0 and missing:
                return _calls("check-0",
                              ("record_evidence", "check-ev", {"entity": missing, "selections": [{"chunk_index": 1, "span_index": 0}]}),
                              (STAGE, "check-st", {"label": missing, "evidence_record_ids": [f"ev-{missing}"]}),
                              (FINALIZE, "check-finalize", {"candidate_ids": []}))
            return _final("Checked the records; one missing record was restored.", response_id="check-final")
        if message.startswith("[Finalize check]"):
            if turn == 0 and self.scenario.get("fix_on_finalize_check"):
                bad = re.search(r'"candidate_id": "(obj-\d+)"', message).group(1)
                return _calls("fix-0", (DISCARD, "fix-discard", {"candidate_id": bad}))
            return _final("Removed the record that was not a reagent.", response_id="fix-final")
        raise AssertionError(f"unexpected planner message: {message[:80]}")


# ---------------------------------------------------------------------------
# Integration harness
# ---------------------------------------------------------------------------


@pytest.fixture
def harness(monkeypatch):
    from unittest.mock import AsyncMock

    # The scripted model has catalog capacities and never calls the token counter.
    monkeypatch.setenv(
        "AGENT_MODEL_CAPACITY_OVERRIDES_JSON",
        '{"gpt-test":{"context_window_tokens":1050000,"max_input_tokens":922000,"max_output_tokens":128000}}',
    )
    monkeypatch.setattr(
        "src.lib.openai_agents.context_capacity.NativeCapacityRequest.count", AsyncMock(return_value=1000)
    )
    monkeypatch.setenv("EXTRACTION_FANOUT_MIN_ITEMS", "6")
    monkeypatch.setenv("EXTRACTION_FANOUT_ITEMS_PER_WRITER", "3")
    monkeypatch.setenv("EXTRACTION_FANOUT_MAX_WRITERS", "8")
    monkeypatch.setattr(tool_roles, "builder_finalization_tool_names", lambda: frozenset({FINALIZE}))
    monkeypatch.setattr(tool_roles, "builder_stage_tool_names", lambda: frozenset({STAGE}))
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent: None)
    monkeypatch.setattr(streaming_tools, "capture_source_document", lambda *_: None)

    async def _echo_validation(serialized_payload, *_args, **_kwargs):
        return serialized_payload

    monkeypatch.setattr(streaming_tools, "_dispatch_domain_envelope_validators_for_chat", _echo_validation)
    events, trace = [], []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", events.append)
    monkeypatch.setattr(fanout, "write_extraction_trace_event", lambda **event: trace.append(event) or event)
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **event: event)

    class Harness:
        def __init__(self):
            self.events, self.trace, self.finalize_calls, self.reads = events, trace, [], []
            self.workspaces = []

        def agent(self, model):
            return Agent(
                name="Test Builder",
                instructions="Extract every reagent the paper uses.",
                model=model,
                model_settings=ModelSettings(parallel_tool_calls=True),
                tools=[*_source_tools(self.reads), _stage_test_object, _discard_test_object,
                       _finalize_tool(self.finalize_calls)],
            )

        async def run(self, model):
            original = builder.ExtractionBuilderWorkspace.__init__
            workspaces = self.workspaces

            def capture(workspace, *args, **kwargs):
                original(workspace, *args, **kwargs)
                workspaces.append(workspace)

            monkeypatch.setattr(builder.ExtractionBuilderWorkspace, "__init__", capture)
            return await streaming_tools.run_specialist_with_events(
                agent=self.agent(model),
                input_text="Extract the reagents from this paper.",
                specialist_name="Test Builder",
                run_config=RunConfig(tracing_disabled=True),
                max_turns=12,
                tool_name="ask_test_builder_specialist",
                inline_chat_persistence=False,
            )

        def trace_types(self):
            return [event["event_type"] for event in self.trace]

        def progress(self):
            return [event["details"]["message"] for event in self.events
                    if event.get("type") == "AGENT_GENERATING" and event["details"].get("extractionPhase")]

    return Harness()


def _plan(*labels):
    return {"notes": ["Lines were generated in this study."],
            "items": [{"label": label, "chunks": [1, 2]} for label in labels]}


def _internal_result(events):
    return next(event for event in events if event.get("type") == "INTERNAL_EXTRACTION_RESULT")


def _finalized_labels(harness):
    payload = _internal_result(harness.events)["internal"]["canonical_payload"]
    return [item["label"] for item in payload["items"]]


@pytest.mark.asyncio
async def test_planner_three_writers_tidy_and_code_finalize(harness):
    labels = [f"reagent {n}" for n in range(1, 8)]
    model = ScriptedFanoutModel({
        "plan": _plan(*labels),
        "short_writer": 2,
        "tidy_stages": "reagent 5",
        "concurrent_writers": 3,
    })

    await harness.run(model)

    workspace = harness.workspaces[-1]
    # Staging was refused while the planner read.
    planning_output = next(
        item for request in model.requests for item in request["input"]
        if isinstance(item, dict) and item.get("call_id") == "plan-early-stage" and item.get("type") == "function_call_output"
    )
    assert "disabled during planning" in planning_output["output"]

    # Three writers ran concurrently (the fake model's barrier), each with four tools.
    writer_requests = [request for request in model.requests if request["writer"] is not None]
    assert {request["writer"] for request in writer_requests} == {1, 2, 3}
    assert {tuple(request["tools"]) for request in writer_requests} == {
        ("read_chunk", "record_evidence", "search_document", STAGE)
    }
    first_writer_input = next(request["input"] for request in writer_requests if request["writer"] == 1)
    assignment = next(_text(item) for item in first_writer_input if item.get("role") == "developer")
    assert "Your items:\nP1. reagent 1 | chunks: 1, 2\nP2. reagent 2 | chunks: 1, 2\nP3. reagent 3 | chunks: 1, 2" in assignment
    assert "Lines were generated in this study." in assignment
    assert "read_chunk(chunk_index=1) result:\n{'chunk': {'chunk_index': 1, 'content': 'passage 1'" in assignment
    # Each cited chunk was read once for all writers (plus the planner's own read).
    assert sorted(harness.reads) == [1, 1, 2]

    # The planner thread was resumed for the check with the listing and the shortfall.
    check = next(
        _text(item) for request in model.requests for item in request["input"]
        if request["writer"] is None and isinstance(item, dict) and _text(item).startswith("[Check phase]")
    )
    assert "Planned records: 7. Staged records: 6." in check
    assert "Writer 2 was assigned 2 records and staged 1. Assigned: P4. reagent 4; P5. reagent 5." in check
    assert "Its final reply: done" in check
    assert re.search(r'^obj-\d+: \{"label":"reagent 1"\}$', check, flags=re.MULTILINE)
    # The checker's own finalize call was refused; code finalized once, in plan order.
    refused = next(
        item for request in model.requests for item in request["input"]
        if isinstance(item, dict) and item.get("call_id") == "check-finalize" and item.get("type") == "function_call_output"
    )
    assert refused["output"] == fanout.FINALIZE_REFUSAL
    assert len(harness.finalize_calls) == 1
    assert workspace.finalization is not None
    finalized = _finalized_labels(harness)
    # Writers' records in plan order (a writer's parallel stage calls may land in any
    # order), then the record the checker restored.
    assert [set(finalized[:3]), finalized[3], set(finalized[4:6]), finalized[6]] == [
        {"reagent 1", "reagent 2", "reagent 3"}, "reagent 4", {"reagent 6", "reagent 7"}, "reagent 5"]

    # Writer attribution and code counting.
    assert [len(workspace.candidates_staged_by(f"writer-{n}")) for n in (1, 2, 3)] == [3, 1, 2]
    writer_events = [event["output_summary"] for event in harness.trace if event["event_type"] == "extraction_fanout.writer_completed"]
    assert sorted((event["writer"], event["assigned_count"], event["staged_count"]) for event in writer_events) == [
        (1, 3, 3), (2, 2, 1), (3, 2, 2)]
    plan_event = next(event["output_summary"] for event in harness.trace if event["event_type"] == "extraction_fanout.plan_submitted")
    assert (plan_event["path"], plan_event["planned_records"], plan_event["writer_count"]) == ("writers", 7, 3)

    # Writers' recorded calls reached the audit stream; evidence merged without duplicates.
    tool_starts = [event["details"]["toolName"] for event in harness.events if event.get("type") == "TOOL_START"]
    assert tool_starts.count(STAGE) == 1 + 6 + 1  # refused planning try, writers, checker
    summary = next(event for event in harness.events if event.get("type") == "SPECIALIST_SUMMARY")["details"]
    evidence_ids = [record["evidence_record_id"] for record in summary["liveEvidenceRecords"]]
    assert len(evidence_ids) == len(set(evidence_ids)) == 7

    assert harness.progress() == [
        "Reading the paper and planning the records",
        "Writing 7 records in 3 parallel batches",
        "Checking the 7 written records",
    ]
    payload = _internal_result(harness.events)["internal"]["canonical_payload"]
    assert payload["specialist_explanation"] == "Checked the records; one missing record was restored."


@pytest.mark.asyncio
async def test_small_plan_is_staged_by_the_planner_thread(harness):
    model = ScriptedFanoutModel({"plan": _plan("reagent 1", "reagent 2")})

    await harness.run(model)

    assert all(request["writer"] is None for request in model.requests)
    stage_prompt = next(
        _text(item) for request in model.requests for item in request["input"]
        if isinstance(item, dict) and _text(item).startswith("[Writing phase]")
    )
    assert "Your plan has 2 records. Staging is now enabled." in stage_prompt
    # Same thread: the resumed request still carries the plan submission.
    resumed = next(request["input"] for request in model.requests
                   if any(_text(item).startswith("[Writing phase]") for item in request["input"] if isinstance(item, dict)))
    assert any(item.get("call_id") == "plan-submit" for item in resumed if isinstance(item, dict))
    assert sorted(_finalized_labels(harness)) == ["reagent 1", "reagent 2"]
    assert harness.progress() == ["Reading the paper and planning the records", "Writing 2 records"]
    plan_event = next(event["output_summary"] for event in harness.trace if event["event_type"] == "extraction_fanout.plan_submitted")
    assert plan_event["path"] == "planner_stages"


@pytest.mark.asyncio
async def test_empty_plan_takes_the_no_results_path(harness):
    model = ScriptedFanoutModel({"plan": {"notes": [], "items": []}})

    await harness.run(model)

    workspace = harness.workspaces[-1]
    assert harness.finalize_calls == [[]]
    assert workspace.finalization is not None
    assert workspace.finalization.source_candidate_ids == ()
    payload = _internal_result(harness.events)["internal"]["canonical_payload"]
    assert payload["items"] == []
    assert payload["specialist_explanation"] == "Nothing in this paper meets the instructions."
    assert not any(request["writer"] for request in model.requests)


@pytest.mark.asyncio
async def test_validation_problems_go_back_to_the_planner_thread_once(harness):
    model = ScriptedFanoutModel({"plan": _plan("reagent 1", "bad"), "fix_on_finalize_check": True})

    await harness.run(model)

    assert [sorted(call) for call in harness.finalize_calls] == [["obj-1", "obj-2"], [harness.finalize_calls[1][0]]]
    assert len(harness.finalize_calls[1]) == 1
    fix_prompt = next(
        _text(item) for request in model.requests for item in request["input"]
        if isinstance(item, dict) and _text(item).startswith("[Finalize check]")
    )
    assert "bad is not a record" in fix_prompt
    assert _finalized_labels(harness) == ["reagent 1"]
    attempts = [event["output_summary"]["finalized"] for event in harness.trace
                if event["event_type"] == "extraction_fanout.finalize_attempt"]
    assert attempts == [False, True]


@pytest.mark.asyncio
async def test_second_finalize_failure_fails_the_step_as_today(harness):
    model = ScriptedFanoutModel({"plan": _plan("reagent 1", "bad")})

    with pytest.raises(streaming_tools.SpecialistOutputError, match="did not leave a finalized"):
        await harness.run(model)

    assert len(harness.finalize_calls) == 2
    assert harness.workspaces[-1].finalization is None


@pytest.mark.asyncio
async def test_a_writer_provider_failure_fails_the_step(harness):
    labels = [f"reagent {n}" for n in range(1, 8)]
    model = ScriptedFanoutModel({"plan": _plan(*labels), "failing_writer": 2})

    with pytest.raises(RuntimeError, match="writer provider failure"):
        await harness.run(model)

    workspace = harness.workspaces[-1]
    assert workspace.finalization is None
    assert workspace.state == builder.BUILDER_STATE_ABORTED
    assert harness.finalize_calls == []


@pytest.mark.asyncio
async def test_missing_plan_gets_one_reminder_then_fails(harness):
    model = ScriptedFanoutModel({"plan": None})

    with pytest.raises(streaming_tools.SpecialistOutputError, match="did not submit an extraction plan"):
        await harness.run(model)

    planner_messages = [
        _text(item) for request in model.requests for item in request["input"]
        if isinstance(item, dict) and item.get("role") == "user" and _text(item).startswith("[Planning phase]")
    ]
    assert any(message.startswith("[Planning phase] You have not") for message in planner_messages)
    assert harness.finalize_calls == []
    assert any(event.get("details", {}).get("reason") == "extraction_plan_missing" for event in harness.events)


@pytest.mark.asyncio
async def test_reminded_plan_is_accepted(harness):
    # The first planning run ends in text; the reminder run submits.
    model = ScriptedFanoutModel({"plan": _plan("reagent 1"), "first_run_text": True, "submit_on_reminder": True})

    await harness.run(model)

    assert _finalized_labels(harness) == ["reagent 1"]


@pytest.mark.asyncio
async def test_fanout_off_keeps_the_single_writer_request(harness, monkeypatch):
    monkeypatch.setenv("EXTRACTION_FANOUT_MIN_ITEMS", "0")
    calls = []

    class SingleWriter(FakeResponsesHTTPModel):
        def _next(self, kwargs):
            calls.append(kwargs)
            turn = sum(1 for item in kwargs["input"] if isinstance(item, dict) and item.get("type") == "function_call_output")
            if turn == 0:
                return _calls("s-0", ("read_chunk", "s-read", {"chunk_index": 1}))
            if turn == 1:
                return _calls("s-1", ("record_evidence", "s-ev", {"entity": "reagent 1", "selections": []}))
            if turn == 2:
                return _calls("s-2", (STAGE, "s-st", {"label": "reagent 1", "evidence_record_ids": ["ev-reagent 1"]}))
            if turn == 3:
                return _calls("s-3", (FINALIZE, "s-fin", {"candidate_ids": ["obj-1"]}))
            return _final("Finalized one reagent.", response_id="s-final")

    await harness.run(SingleWriter())

    assert calls[0]["input"] == [{"content": "Extract the reagents from this paper.", "role": "user"}]
    assert fanout.SUBMIT_PLAN_TOOL_NAME not in {getattr(tool, "name", "") for tool in calls[0]["tools"]}
    assert harness.finalize_calls == [["obj-1"]]
    assert harness.progress() == []
    assert not [event for event in harness.trace if event["event_type"].startswith("extraction_fanout.")]


@pytest.mark.asyncio
async def test_fanout_does_not_apply_without_exactly_one_finalize_tool(harness, monkeypatch):
    monkeypatch.setattr(tool_roles, "builder_finalization_tool_names", lambda: frozenset({FINALIZE, DISCARD}))
    agent = harness.agent(ScriptedFanoutModel({}))
    workspace = builder.ExtractionBuilderWorkspace(run_id="trace-na")
    tools_before = list(agent.tools)

    prepared = fanout.prepare_fanout_extraction(
        agent, builder_workspace=workspace, specialist_name="Test Builder",
        input_text="x", emit_event=harness.events.append,
    )

    assert prepared is None
    assert agent.tools == tools_before
    assert harness.trace_types() == ["extraction_fanout.not_applicable"]
