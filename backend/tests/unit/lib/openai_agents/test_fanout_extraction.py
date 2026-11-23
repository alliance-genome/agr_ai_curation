"""Fan-out builder extraction: plan contract, code counting, and a fake-model run.

The integration tests drive the real Agents SDK runner and ``run_specialist_with_events``
with a scripted provider model. Nothing here calls a paid model.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

import pytest
from agents import Agent, FunctionTool, ModelSettings, RunConfig
from agents.items import ModelResponse
from agents.strict_schema import ensure_strict_json_schema
from agr_ai_curation_runtime.function_tools import function_tool
from openai.types.responses import ResponseFunctionToolCall

from src.lib.openai_agents import extraction_builder_workspace as builder
from src.lib.openai_agents import fanout_extraction as fanout
from src.lib.openai_agents import streaming_tools
from src.lib.openai_agents.tools import evidence_workspace
from src.lib.packages import tool_roles
from tests.readable_files_support import single_file
from tests.unit.lib.openai_agents.test_model_request_measurement import (
    FakeResponsesHTTPModel,
    _final,
    _usage,
)

STAGE = "stage_test_object"
FINALIZE = "finalize_test_extraction"
DISCARD = "discard_test_object"
PATCH = "patch_test_object"
CATALOG = "list_test_record_types"


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


def test_writer_reruns_default_to_one_and_zero_turns_them_off(monkeypatch):
    from src.lib.openai_agents import config

    monkeypatch.delenv("EXTRACTION_FANOUT_WRITER_RERUNS", raising=False)
    assert config.get_extraction_fanout_writer_reruns() == 1
    monkeypatch.setenv("EXTRACTION_FANOUT_WRITER_RERUNS", "0")
    assert config.get_extraction_fanout_writer_reruns() == 0
    monkeypatch.setenv("EXTRACTION_FANOUT_WRITER_RERUNS", "-3")
    assert config.get_extraction_fanout_writer_reruns() == 0


def test_writer_store_responses_defaults_off_and_reads_overrides(monkeypatch):
    from src.lib.openai_agents import config

    monkeypatch.delenv("EXTRACTION_FANOUT_WRITER_STORE_RESPONSES", raising=False)
    assert config.get_extraction_fanout_writer_store_responses() is False
    monkeypatch.setenv("EXTRACTION_FANOUT_WRITER_STORE_RESPONSES", "true")
    assert config.get_extraction_fanout_writer_store_responses() is True
    monkeypatch.setenv("EXTRACTION_FANOUT_WRITER_STORE_RESPONSES", "false")
    assert config.get_extraction_fanout_writer_store_responses() is False


def test_stage_role_comes_from_package_binding_metadata():
    tool_roles.reset_cache()
    try:
        stage_tools = tool_roles.builder_stage_tool_names()
    finally:
        tool_roles.reset_cache()
    assert {"stage_generic_object", "stage_gene_expression_observation"} <= stage_tools
    assert not {name for name in stage_tools if not name.startswith("stage_")}


def test_catalog_role_comes_from_package_binding_metadata():
    tool_roles.reset_cache()
    try:
        catalog_tools = tool_roles.builder_catalog_tool_names()
        stage_tools = tool_roles.builder_stage_tool_names()
    finally:
        tool_roles.reset_cache()
    assert "list_generic_object_classes" in catalog_tools
    # A catalog only lists record types; it never stages one.
    assert not catalog_tools & stage_tools


def test_planning_message_plans_only_records_that_answer_the_request():
    message = fanout.planning_message(())

    assert message.startswith(
        "[Planning phase] Read what you need from the paper first, using the reading and search tools."
    )
    # Flow-step and custom-agent scope rules live in the instructions, not the request.
    assert (
        "Plan only records that answer the request under your instructions, include every one "
        "of them, and nothing else."
    ) in message
    assert (
        "Anything you will name only as a contrast or an exclusion, such as a control or other "
        "item outside the requested category, goes in your final explanation, not in items, "
        "even when you are asked to distinguish it."
    ) in message
    # Controls are not outside the category by default.
    assert "An item inside the requested category is planned even when the paper uses it as a control." in message
    assert "Include every record your instructions require" not in message
    assert "record types" not in message


def test_planning_message_has_a_planner_with_a_catalog_read_it_first():
    message = fanout.planning_message(("list_generic_object_classes",))

    assert message.startswith(
        "[Planning phase] First call list_generic_object_classes to see the record types you can "
        "stage, so you plan each item as one record of the type that fits. Then read what you need "
        "from the paper, using the reading and search tools."
    )
    assert "Read what you need from the paper first" not in message
    # The rest of the message is the same for every planner.
    assert message.endswith(fanout.PLANNING_INSTRUCTIONS)


# ---------------------------------------------------------------------------
# Fake tools and a scripted provider model
# ---------------------------------------------------------------------------


@function_tool(name_override=STAGE)
def _stage_test_object(label: str, evidence_record_ids: list[str], pending_ref_id: str | None = None) -> str:
    """Stage one record."""
    time.sleep(0.02)  # a measurable tool duration for the replayed audit
    workspace = builder.get_active_extraction_builder_workspace()
    candidate = workspace.stage_candidate(
        id_prefix="obj",
        pending_ref_id=pending_ref_id,
        staged_fields={"label": label},
        evidence_record_ids=evidence_record_ids,
    )
    return json.dumps({"status": "ok", "candidate_id": candidate.candidate_id})


@function_tool(name_override=PATCH)
def _patch_test_object(candidate_id: str, label: str) -> str:
    """Correct one record's label."""
    workspace = builder.get_active_extraction_builder_workspace()
    candidate = workspace.get_candidate(candidate_id)
    workspace.upsert_candidate(
        candidate_id=candidate_id,
        staged_fields={"label": label},
        pending_ref_ids=candidate.pending_ref_ids,
        evidence_record_ids=candidate.evidence_record_ids,
        status=candidate.status,
    )
    return json.dumps({"status": "ok"})


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


def _note_source_call(scenario, name):
    """The history invocation a source tool call ran under: the scope its reads record."""
    from src.lib.openai_agents.run_history import ACTIVE_HISTORY
    if scenario is not None:
        history = ACTIVE_HISTORY.get()
        scenario.setdefault("source_calls", []).append(
            (name, history.scope.invocation_id if history is not None else None))


def _source_tools(reads, scenario=None):
    async def read_chunk(_ctx, arguments):
        from src.lib.openai_agents.document_result_view import document_result_view

        index = json.loads(arguments)["chunk_index"]
        reads.append(index)
        _note_source_call(scenario, "read_chunk")
        text = f"passage {index}"
        canonical = {"chunk": {"chunk_id": f"uuid-{index}", "chunk_index": index, "content": text,
                               "evidence_spans": [{"span_id": f"uuid-{index}:s0", "span_index": 0, "text": text,
                                                   "char_start": 0, "char_end": len(text)}]}}
        # The inline catalog runner hands the agent the shared model view.
        return document_result_view("read_chunk", canonical)[0]

    async def search_document(_ctx, _arguments):
        _note_source_call(scenario, "search_document")
        return {"hits": []}

    async def record_evidence(_ctx, arguments):
        # Like the real tool: a new record joins the run's evidence list only when the
        # specialist stream loop handles its result; an update by ID looks the record up
        # in that list and changes it in place.
        args = json.loads(arguments)
        entity = args["entity"]
        if scenario is not None and entity == scenario.get("busy_tool_entity"):
            # The writer stays inside this tool call until it is cancelled.
            scenario.setdefault("other_busy", asyncio.Event()).set()
            await asyncio.wait_for(asyncio.Event().wait(), timeout=5)
        record = {"status": "verified", "evidence_record_id": f"ev-{entity}", "entity": entity,
                  "verified_quote": f"quote for {entity}", "page": 1, "section": "Methods", "chunk_id": "chunk-1"}
        if args.get("evidence_record_id"):
            records = evidence_workspace._ACTIVE_EVIDENCE_RECORDS.get()
            existing = next((item for item in records if item["evidence_record_id"] == args["evidence_record_id"]), None)
            if existing is None:
                return json.dumps({"status": "not_found", "entity": entity,
                                   "evidence_record_id": args["evidence_record_id"]})
            record.update(evidence_record_id=args["evidence_record_id"], verified_quote=f"updated quote for {entity}")
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
        self._agr_provider_id = "openai"
        self.requests = []
        self.writer_barrier = {"arrived": 0, "event": None}

    async def get_response(self, system_instructions, input, model_settings, tools, *args, **kwargs):
        writer = self._writer_index(input)
        fail_once = self.scenario.get("fail_once") or {}
        waits_for = fail_once.get("after_writer_done")
        turn = self._turn(input, developer=True)
        if waits_for and writer == fail_once["writer"] and turn > 0:
            # The failing attempt goes on only once the named writer has finished.
            done = self.scenario.setdefault("writer_done", {}).setdefault(waits_for, asyncio.Event())
            await asyncio.wait_for(done.wait(), timeout=5)
        busy = self.scenario.setdefault("other_busy", asyncio.Event())
        starts = self.scenario.get("writer_starts", {})
        if writer is not None and writer == self.scenario.get("converts_cancel_writer") and turn > 0:
            # As the SDK can: a cancellation arriving during the writer's run surfaces
            # as an ordinary error instead of CancelledError.
            busy.set()
            try:
                await asyncio.wait_for(asyncio.Event().wait(), timeout=5)
            except asyncio.CancelledError:
                raise RuntimeError("tool error from a cancellation") from None
        if writer is not None and writer == self.scenario.get("busy_rerun_writer") and turn > 0 \
                and starts.get(writer) == 2:
            # The re-run stays busy until it is cancelled.
            busy.set()
            await asyncio.wait_for(asyncio.Event().wait(), timeout=5)
        if writer is not None and writer == self.scenario.get("busy_model_writer") and turn == 0:
            # The writer's first model call stays in flight until it is cancelled.
            busy.set()
            await asyncio.wait_for(asyncio.Event().wait(), timeout=5)
        waits_for_busy = (self.scenario.get("busy_rerun_writer") or self.scenario.get("converts_cancel_writer")
                          or self.scenario.get("busy_model_writer") or self.scenario.get("busy_tool_entity"))
        if waits_for_busy and writer == self.scenario.get("failing_writer") and turn == 0 \
                and starts.get(writer) == 1:
            # The failing writer's last attempt starts once the other writer is busy.
            await asyncio.wait_for(busy.wait(), timeout=5)
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
        self.requests.append({"writer": writer, "tools": tool_names, "input": items,
                              "parallel": kwargs["model_settings"].parallel_tool_calls,
                              "store": kwargs["model_settings"].store})
        if writer is not None:
            return self._writer_reply(writer, items)
        return self._planner_reply(items)

    def _writer_reply(self, writer, items):
        turn = self._turn(items, developer=True)
        # A writer run starts with its turn-0 request; the count is that run's attempt.
        starts = self.scenario.setdefault("writer_starts", {})
        if turn == 0:
            starts[writer] = starts.get(writer, 0) + 1
        attempt = starts.get(writer, 1)
        if writer == self.scenario.get("failing_writer"):
            raise RuntimeError(f"writer provider failure (attempt {attempt})")
        if writer == self.scenario.get("cancelled_writer"):
            raise asyncio.CancelledError()
        if self.scenario.get("cancel_on_attempt", {}).get(writer) == attempt:
            raise asyncio.CancelledError()
        fail_once = self.scenario.get("fail_once") or {}
        fail_writers = fail_once.get("writer")
        fail_writers = fail_writers if isinstance(fail_writers, tuple) else (fail_writers,)
        failing_now = writer in fail_writers and attempt == 1
        if failing_now and fail_once.get("when", "start") == "start":
            raise fail_once.get("error", RuntimeError)("writer provider failure (attempt 1)")
        assignment = next(_text(item) for item in items if isinstance(item, dict) and item.get("role") == "developer")
        labels = re.findall(r"^P\d+\. (.+?) \| chunks:", assignment, flags=re.MULTILINE)
        extra_entities = [
            entity for attempts, entity in self.scenario.get("extra_evidence", {}).get(writer, [])
            if attempts is None or attempt in attempts
        ]
        # A separate call-ID series per attempt, as the provider issues fresh IDs.
        prefix = f"w{writer}" if attempt == 1 else f"w{writer}a{attempt}"
        if turn == 0:
            own_reads = ([("read_chunk", f"{prefix}-rd", {"chunk_index": 7}),
                          ("search_document", f"{prefix}-sr", {"query": "abc-1"})]
                         if self.scenario.get("writer_reads") else [])
            return _calls(f"{prefix}-0", *own_reads, *[
                ("record_evidence", f"{prefix}-ev-{n}", {"entity": label, "selections": [{"chunk_index": 1, "span_index": 0}]})
                for n, label in enumerate([*labels, *extra_entities])
            ])
        answered = {item.get("call_id") for item in items
                    if isinstance(item, dict) and item.get("type") == "function_call_output"}
        if failing_now and fail_once.get("when") == "after_staging" and any(
            str(call_id).startswith(f"{prefix}-st-") for call_id in answered
        ):
            raise fail_once.get("error", RuntimeError)("writer provider failure (attempt 1)")
        if writer == self.scenario.get("updating_writer") and f"w{writer}-upd" not in answered:
            return _calls(f"w{writer}-0u", ("record_evidence", f"w{writer}-upd", {
                "entity": labels[0], "evidence_record_id": f"ev-{labels[0]}",
                "selections": [{"chunk_index": 1, "span_index": 1}]}))
        if not any(str(call_id).startswith(f"{prefix}-st-") for call_id in answered):
            if writer == self.scenario.get("short_writer"):
                labels = labels[:1]
            pending = self.scenario.get("pending_refs")
            return _calls(f"{prefix}-1", *[
                (STAGE, f"{prefix}-st-{n}", {"label": label, "evidence_record_ids": [f"ev-{label}"],
                                              **({"pending_ref_id": f"ref-{label}"} if pending else {})})
                for n, label in enumerate(labels)
            ])
        self.scenario.setdefault("writer_done", {}).setdefault(writer, asyncio.Event()).set()
        return _final("done", response_id=f"{prefix}-final")

    def _planner_reply(self, items):
        message = next(_text(item) for item in reversed(items) if isinstance(item, dict) and item.get("role") == "user")
        turn = self._turn(items)
        plan = self.scenario.get("plan")
        if message.startswith("[Planning phase]") and "call submit_extraction_plan once" in message:
            if self.scenario.get("catalog_first"):
                answered = {item.get("call_id") for item in items
                            if isinstance(item, dict) and item.get("type") == "function_call_output"}
                if "plan-catalog" not in answered:
                    return _calls("plan-c", (CATALOG, "plan-catalog", {}))
                turn -= 1
            if turn == 0:
                planner_evidence = self.scenario.get("planner_evidence")
                return _calls("plan-0",
                              ("read_chunk", "plan-read", {"chunk_index": 1}),
                              (STAGE, "plan-early-stage", {"label": "too early", "evidence_record_ids": []}),
                              *([("record_evidence", "plan-ev", {"entity": planner_evidence,
                                                                  "selections": [{"chunk_index": 1, "span_index": 0}]})]
                                if planner_evidence else []))
            if plan is None or self.scenario.get("first_run_text"):
                return _final("I read the paper.", response_id="plan-text")
            submitted = {item.get("call_id") for item in items
                         if isinstance(item, dict) and item.get("type") == "function_call_output"}
            if self.scenario.get("first_plan") and "plan-submit-first" not in submitted:
                return _calls("plan-1a", (fanout.SUBMIT_PLAN_TOOL_NAME, "plan-submit-first", self.scenario["first_plan"]))
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
            calls = []
            missing = self.scenario.get("tidy_stages")
            if missing:
                calls += [
                    ("record_evidence", "check-ev", {"entity": missing, "selections": [{"chunk_index": 1, "span_index": 0}]}),
                    (STAGE, "check-st", {"label": missing, "evidence_record_ids": [f"ev-{missing}"]}),
                    (FINALIZE, "check-finalize", {"candidate_ids": []}),
                ]
            listing = dict(
                (label, candidate_id)
                for candidate_id, label in re.findall(r'^(obj-\d+): \{"label":"(.+)"\}$', message, flags=re.MULTILINE)
            )
            calls += [
                (PATCH, f"check-patch-{n}", {"candidate_id": listing[label], "label": f"{label} [fixed]"})
                for n, label in enumerate(self.scenario.get("tidy_patches", []))
            ]
            if turn == 0 and calls:
                return _calls("check-0", *calls)
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
    monkeypatch.setattr(tool_roles, "builder_catalog_tool_names", lambda: frozenset({CATALOG}))
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent: None)
    monkeypatch.setattr(streaming_tools, "capture_source_document", lambda *_: None)

    async def _echo_validation(serialized_payload, *_args, **_kwargs):
        return serialized_payload

    monkeypatch.setattr(streaming_tools, "_dispatch_domain_envelope_validators_for_chat", _echo_validation)
    events, trace = [], []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", events.append)
    monkeypatch.setattr(fanout, "write_extraction_trace_event", lambda **event: trace.append(event) or event)
    builder_trace = []
    monkeypatch.setattr(builder, "write_extraction_trace_event", lambda **event: builder_trace.append(event) or event)
    chunk_checks = []
    existing_chunks = {"indexes": set(range(50)), "error": None}

    async def _existing_chunk_indexes(*, chunk_indexes, user_id, document_id, readable):
        assert readable == single_file(document_id)
        chunk_checks.append({"chunk_indexes": list(chunk_indexes), "user_id": user_id, "document_id": document_id})
        if existing_chunks["error"] is not None:
            raise existing_chunks["error"]
        return {index for index in chunk_indexes if index in existing_chunks["indexes"]}

    monkeypatch.setattr("src.lib.weaviate_client.chunks.get_existing_chunk_indexes", _existing_chunk_indexes)
    monkeypatch.setattr("src.lib.document_files.readable.resolve_readable_files",
                        lambda document_id, user_id: single_file(document_id))

    class Harness:
        def __init__(self):
            self.events, self.trace, self.finalize_calls, self.reads = events, trace, [], []
            self.builder_trace = builder_trace
            self.workspaces = []
            self.chunk_checks, self.existing_chunks = chunk_checks, existing_chunks

        def agent(self, model, *, catalog=False):
            async def list_record_types(_ctx, _arguments):
                return {"classes": [{"class_key": "test:reagent"}, {"class_key": "test:claim"}]}

            agent = Agent(
                name="Test Builder",
                instructions="Extract every reagent the paper uses.",
                model=model,
                # Saved agents may leave parallel tool calls unset; fan-out turns them on.
                model_settings=ModelSettings(),
                tools=[*_source_tools(self.reads, getattr(model, "scenario", None)), _stage_test_object, _patch_test_object, _discard_test_object,
                       _finalize_tool(self.finalize_calls),
                       *([_tool(CATALOG, list_record_types)] if catalog else [])],
            )
            # The scope catalog_service records for the agent's document tools.
            agent.cost_boundary = {"document_id": "doc-1", "user_id": "curator-1"}
            return agent

        async def run(self, model, *, catalog=False):
            original = builder.ExtractionBuilderWorkspace.__init__
            workspaces = self.workspaces

            def capture(workspace, *args, **kwargs):
                original(workspace, *args, **kwargs)
                workspaces.append(workspace)

            monkeypatch.setattr(builder.ExtractionBuilderWorkspace, "__init__", capture)
            return await streaming_tools.run_specialist_with_events(
                agent=self.agent(model, catalog=catalog),
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
    # Prefetched chunks reach writers in the same numbered form every reading tool shows.
    assert "read_chunk(chunk_index=1) result:\n{'chunk': {'chunk_index': 1, 'text': '[0] passage 1'}}" in assignment
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
    assert [len(workspace.candidates_staged_by(f"writer-{n}/a1")) for n in (1, 2, 3)] == [3, 1, 2]
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
        # The staged count, not the planned count: writer 2 staged one record short.
        "Checking the 6 written records",
    ]
    # Replayed writer calls keep their recorded durations instead of about 0 ms.
    writer_stage_ids = {f"w{w}-st-{n}" for w in (1, 2, 3) for n in range(3)}
    writer_stage_durations = [
        event["details"]["durationMs"] for event in harness.events
        if event.get("type") == "TOOL_COMPLETE" and event["details"].get("toolCallId") in writer_stage_ids
    ]
    assert len(writer_stage_durations) == 6
    assert min(writer_stage_durations) >= 15
    payload = _internal_result(harness.events)["internal"]["canonical_payload"]
    assert payload["specialist_explanation"] == "Checked the records; one missing record was restored."


@pytest.mark.asyncio
async def test_check_pass_applies_several_patches_from_one_turn(harness):
    labels = [f"reagent {n}" for n in range(1, 8)]
    fixes = ["reagent 2", "reagent 4", "reagent 6"]
    model = ScriptedFanoutModel({"plan": _plan(*labels), "tidy_patches": fixes})

    await harness.run(model)

    check_requests = [
        request for request in model.requests
        if request["writer"] is None and any(
            isinstance(item, dict) and _text(item).startswith("[Check phase]") for item in request["input"])
    ]
    # One response carried every patch; the next request is the closing reply.
    assert len(check_requests) == 2
    patch_outputs = [item for item in check_requests[1]["input"]
                     if isinstance(item, dict) and item.get("type") == "function_call_output"
                     and str(item.get("call_id", "")).startswith("check-patch-")]
    assert len(patch_outputs) == 3
    check_prompt = next(_text(item) for item in check_requests[0]["input"]
                        if isinstance(item, dict) and _text(item).startswith("[Check phase]"))
    assert "Make every fix in one response: call the correction and stage tools in parallel" in check_prompt
    finalized = sorted(_finalized_labels(harness))
    assert finalized == sorted(
        f"{label} [fixed]" if label in fixes else label for label in labels
    )
    # The planner thread and every writer run with parallel tool calls on.
    assert {request["parallel"] for request in model.requests} == {True}


@pytest.mark.asyncio
async def test_small_plan_is_staged_by_the_planner_thread(harness):
    model = ScriptedFanoutModel({"plan": _plan("reagent 1", "reagent 2")})

    await harness.run(model)

    assert all(request["writer"] is None for request in model.requests)
    # An agent without a record-type catalog gets the read-first planning message.
    first_request = model.requests[0]["input"]
    assert _text(first_request[-1]) == fanout.planning_message(())
    assert CATALOG not in model.requests[0]["tools"]
    stage_prompt = next(
        _text(item) for request in model.requests for item in request["input"]
        if isinstance(item, dict) and _text(item).startswith("[Writing phase]")
    )
    assert "Your plan has 2 records. Staging is now enabled." in stage_prompt
    # Same overrides as the check pass: no evidence review, no listing, no finalize.
    assert "override the evidence-review and extraction-completion steps" in stage_prompt
    assert "Do not review evidence records, do not list or find staged records, and do not finalize" in stage_prompt
    assert "from the chunks you already read in this conversation" in stage_prompt
    # Same thread: the resumed request still carries the plan submission.
    resumed = next(request["input"] for request in model.requests
                   if any(_text(item).startswith("[Writing phase]") for item in request["input"] if isinstance(item, dict)))
    assert any(item.get("call_id") == "plan-submit" for item in resumed if isinstance(item, dict))
    assert sorted(_finalized_labels(harness)) == ["reagent 1", "reagent 2"]
    assert harness.progress() == ["Reading the paper and planning the records", "Writing 2 records"]
    plan_event = next(event["output_summary"] for event in harness.trace if event["event_type"] == "extraction_fanout.plan_submitted")
    assert plan_event["path"] == "planner_stages"


@pytest.mark.asyncio
async def test_planner_with_a_catalog_is_told_to_read_it_before_planning(harness):
    labels = [f"reagent {n}" for n in range(1, 8)]
    model = ScriptedFanoutModel({"plan": _plan(*labels), "catalog_first": True})

    await harness.run(model, catalog=True)

    planner_requests = [request for request in model.requests if request["writer"] is None]
    # The planning message names the agent's own catalog tool, chosen by its binding role.
    assert _text(planner_requests[0]["input"][-1]) == fanout.planning_message((CATALOG,))
    assert _text(planner_requests[0]["input"][-1]).startswith(f"[Planning phase] First call {CATALOG} ")
    assert CATALOG in planner_requests[0]["tools"]
    # The planner looked at the catalog before it read the paper or submitted the plan.
    call_order = [
        item.get("call_id") for item in planner_requests[-1]["input"]
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]
    assert call_order.index("plan-catalog") < call_order.index("plan-read") < call_order.index("plan-submit")
    # Writers keep their four tools; the catalog stays with the planner.
    writer_tools = {tuple(request["tools"]) for request in model.requests if request["writer"] is not None}
    assert writer_tools == {("read_chunk", "record_evidence", "search_document", STAGE)}
    assert sorted(_finalized_labels(harness)) == sorted(labels)


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
async def test_validation_problems_go_back_to_the_planner_thread_until_success(harness, monkeypatch):
    monkeypatch.setenv("EXTRACTION_FANOUT_MAX_FINALIZE_CORRECTIONS", "3")
    model = ScriptedFanoutModel({"plan": _plan("reagent 1", "bad"), "fix_on_finalize_check": True})

    await harness.run(model)

    assert [sorted(call) for call in harness.finalize_calls] == [["obj-1", "obj-2"], [harness.finalize_calls[1][0]]]
    assert len(harness.finalize_calls[1]) == 1
    fix_prompt = next(
        _text(item) for request in model.requests for item in request["input"]
        if isinstance(item, dict) and _text(item).startswith("[Finalize check]")
    )
    assert "bad is not a record" in fix_prompt
    assert harness.progress()[-1] == "Fixing the problems validation found before saving"
    assert _finalized_labels(harness) == ["reagent 1"]
    attempts = [event["output_summary"]["finalized"] for event in harness.trace
                if event["event_type"] == "extraction_fanout.finalize_attempt"]
    assert attempts == [False, True]


@pytest.mark.asyncio
@pytest.mark.parametrize("configured, corrections", [(None, 1), ("0", 0), ("3", 3)])
async def test_finalize_failure_exhausts_correction_budget(harness, monkeypatch, configured, corrections):
    key = "EXTRACTION_FANOUT_MAX_FINALIZE_CORRECTIONS"
    if configured is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, configured)
    model = ScriptedFanoutModel({"plan": _plan("reagent 1", "bad")})

    with pytest.raises(streaming_tools.SpecialistOutputError, match="did not leave a finalized"):
        await harness.run(model)

    assert harness.progress().count("Fixing the problems validation found before saving") == corrections
    attempts = [event["output_summary"]["attempt"] for event in harness.trace
                if event["event_type"] == "extraction_fanout.finalize_attempt"]
    assert attempts == list(range(1, corrections + 2))
    assert len(harness.finalize_calls) == 1 + corrections
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


def _fanout_trace(harness, kind):
    return [event["output_summary"] for event in harness.trace
            if event["event_type"] == f"extraction_fanout.{kind}"]


def _live_evidence_by_id(harness):
    summary = next(event for event in harness.events if event.get("type") == "SPECIALIST_SUMMARY")["details"]
    records = summary["liveEvidenceRecords"]
    ids = [record["evidence_record_id"] for record in records]
    assert len(ids) == len(set(ids)), "one record per evidence ID"
    return {record["evidence_record_id"]: record for record in records}


def _writer_start_inputs(model, writer):
    return [request["input"] for request in model.requests
            if request["writer"] == writer
            and not any(isinstance(item, dict) and item.get("type") == "function_call_output"
                        for item in request["input"])]


_SEVEN = [f"reagent {n}" for n in range(1, 8)]  # writers: 1 -> P1-P3, 2 -> P4-P5, 3 -> P6-P7


@pytest.mark.asyncio
async def test_a_writer_that_failed_after_staging_is_cleared_and_run_again(harness, monkeypatch):
    from src.lib.openai_agents.evidence_summary import evidence_record_status

    runs = []
    original_init = fanout.FanoutExtraction.__init__

    def capture(run, *args, **kwargs):
        original_init(run, *args, **kwargs)
        runs.append(run)

    monkeypatch.setattr(fanout.FanoutExtraction, "__init__", capture)
    model = ScriptedFanoutModel({
        "plan": _plan(*_SEVEN),
        "fail_once": {"writer": 2, "when": "after_staging", "after_writer_done": 1},
        # Writer 2's failed attempt also records evidence its re-run does not, and
        # evidence writer 1 records too (the same content gives the same ID).
        "extra_evidence": {2: [({1}, "draft reagent 4"), ({1}, "shared note")], 1: [(None, "shared note")]},
    })

    await harness.run(model)

    workspace = harness.workspaces[-1]
    assert model.scenario["writer_starts"] == {1: 1, 2: 2, 3: 1}, "only writer 2 ran again, once"
    # The re-run started from the same assignment, with nothing from the failed attempt.
    first, second = _writer_start_inputs(model, 2)
    assert second == first

    # The trace keeps the history of the failure and the clean-up, as IDs and counts.
    failed, = _fanout_trace(harness, "writer_failed")
    assert failed == {"writer": 2, "writer_count": 3, "attempt": 1, "assigned_positions": [4, 5],
                      "error_type": "RuntimeError", "provider_error_type": None, "provider_error_code": None,
                      "failure_category": "unknown", "rerun": True, "store_off": True}
    started, = _fanout_trace(harness, "writer_rerun_started")
    removed_candidates = started.pop("removed_candidate_ids")
    assert sorted(started.pop("removed_evidence_ids")) == ["ev-draft reagent 4", "ev-reagent 4", "ev-reagent 5"]
    assert started == {"writer": 2, "attempt": 2, "assigned_positions": [4, 5],
                       "error_type": "RuntimeError", "provider_error_type": None, "provider_error_code": None,
                       "failure_category": "unknown", "removed_candidate_count": 2,
                       "removed_evidence_count": 3,
                       "kept_shared_evidence_count": 1}
    finished, = _fanout_trace(harness, "writer_rerun_finished")
    assert finished == {"writer": 2, "attempt": 2, "outcome": "succeeded"}
    completed = {event["writer"]: event for event in _fanout_trace(harness, "writer_completed")}
    assert (completed[2]["attempt"], completed[2]["staged_count"], completed[1]["attempt"]) == (2, 2, 1)
    order = harness.trace_types()
    assert (order.index("extraction_fanout.writer_failed") < order.index("extraction_fanout.writer_rerun_started")
            < order.index("extraction_fanout.writer_rerun_finished")
            < order.index("extraction_fanout.finalize_attempt"))

    # The failed attempt's records are gone; its IDs are not reused; one set per writer.
    assert len(removed_candidates) == 2 and not set(removed_candidates) & set(workspace.candidates)
    rerun = workspace.candidates_staged_by("writer-2/a2")
    assert len(rerun) == 2 and not set(rerun) & set(removed_candidates)
    assert [len(workspace.candidates_staged_by(f"writer-{n}/a1")) for n in (1, 3)] == [3, 2]
    assert workspace.candidates_staged_by("writer-2/a1") == []
    staged_labels = [candidate.staged_fields["label"] for candidate_id, candidate in workspace.candidates.items()
                     if candidate_id not in workspace.finalization.candidate_ids]
    assert sorted(staged_labels) == sorted(_SEVEN), "exactly one staged record per planned record"
    check = next(
        _text(item) for request in model.requests for item in request["input"]
        if request["writer"] is None and isinstance(item, dict) and _text(item).startswith("[Check phase]")
    )
    assert "Planned records: 7. Staged records: 7." in check
    assert sorted(_finalized_labels(harness)) == sorted(_SEVEN)

    # Evidence: one active record per ID; the failed attempt's own extra record is gone.
    records = _live_evidence_by_id(harness)
    assert set(records) == {f"ev-{label}" for label in _SEVEN} | {"ev-shared note"}
    assert all(evidence_record_status(record) != "discarded" for record in records.values())
    finalized_evidence = {
        evidence_id for candidate_id in workspace.finalization.source_candidate_ids
        for evidence_id in workspace.get_candidate(candidate_id).evidence_record_ids
    }
    assert finalized_evidence == {f"ev-{label}" for label in _SEVEN} <= set(records)

    # Only the successful attempts' calls reach the replayed audit, with their own times.
    completed_ids = [event["details"].get("toolCallId") for event in harness.events if event.get("type") == "TOOL_COMPLETE"]
    assert not [call_id for call_id in completed_ids if str(call_id).startswith("w2-")]
    assert len([call_id for call_id in completed_ids if str(call_id).startswith("w2a2-")]) == 4
    kept_timings = set(runs[-1]._tool_timings)
    assert not [call_id for call_id in kept_timings if call_id.startswith("w2-")], "failed attempt's times dropped"
    assert {f"w2a2-st-{n}" for n in range(2)} <= kept_timings
    # Removals reached the workspace's mutation stream like any other mutation.
    removals = [event for event in harness.builder_trace
                if event["event_type"] == "extraction_builder.candidate_mutation"
                and event["output_summary"].get("action") == "remove"]
    assert sorted(event["output_summary"]["candidate_id"] for event in removals) == sorted(removed_candidates)
    assert {event["output_summary"]["reason"] for event in removals} == {fanout.WRITER_RERUN_CLEANUP_REASON}


@pytest.mark.asyncio
async def test_a_writer_that_failed_before_staging_runs_again_with_nothing_to_remove(harness):
    model = ScriptedFanoutModel({"plan": _plan(*_SEVEN), "fail_once": {"writer": 3}})

    await harness.run(model)

    assert model.scenario["writer_starts"] == {1: 1, 2: 1, 3: 2}
    assert sorted(_finalized_labels(harness)) == sorted(_SEVEN)
    started, = _fanout_trace(harness, "writer_rerun_started")
    assert (started["removed_candidate_ids"], started["removed_evidence_ids"]) == ([], [])
    assert len(_live_evidence_by_id(harness)) == 7


@pytest.mark.asyncio
async def test_a_rerun_staging_the_same_pending_ref_ids_gets_new_records(harness):
    """A removed first-attempt record is neither revived nor treated as another writer's."""
    model = ScriptedFanoutModel({
        "plan": _plan(*_SEVEN), "pending_refs": True, "fail_once": {"writer": 2, "when": "after_staging"},
    })

    await harness.run(model)

    workspace = harness.workspaces[-1]
    removed = _fanout_trace(harness, "writer_rerun_started")[0]["removed_candidate_ids"]
    assert len(removed) == 2 and not set(removed) & set(workspace.candidates)
    rerun = workspace.candidates_staged_by("writer-2/a2")
    assert len(rerun) == 2 and not set(rerun) & set(removed)
    assert sorted(workspace.get_candidate(cid).pending_ref_ids[0] for cid in rerun) == ["ref-reagent 4", "ref-reagent 5"]
    stage_outputs = [json.loads(item["output"]) for request in model.requests for item in request["input"]
                     if isinstance(item, dict) and str(item.get("call_id", "")).startswith("w2a2-st-")
                     and item.get("type") == "function_call_output"]
    assert stage_outputs and all(output["status"] == "ok" for output in stage_outputs)
    assert sorted(_finalized_labels(harness)) == sorted(_SEVEN)


@pytest.mark.asyncio
async def test_a_writer_that_fails_twice_fails_the_run_with_its_first_error(harness):
    model = ScriptedFanoutModel({"plan": _plan(*_SEVEN), "failing_writer": 2})

    with pytest.raises(RuntimeError, match=r"writer provider failure \(attempt 1\)"):
        await harness.run(model)

    assert model.scenario["writer_starts"][2] == 2
    assert [(event["attempt"], event["rerun"]) for event in _fanout_trace(harness, "writer_failed")] == [
        (1, True), (2, False)]
    finished, = _fanout_trace(harness, "writer_rerun_finished")
    assert finished == {"writer": 2, "attempt": 2, "outcome": "failed"}
    assert harness.workspaces[-1].state == builder.BUILDER_STATE_ABORTED
    assert harness.finalize_calls == []


@pytest.mark.asyncio
async def test_writer_reruns_set_to_zero_fail_the_run_at_the_first_failure(harness, monkeypatch):
    monkeypatch.setenv("EXTRACTION_FANOUT_WRITER_RERUNS", "0")
    model = ScriptedFanoutModel({"plan": _plan(*_SEVEN), "fail_once": {"writer": 2}})

    with pytest.raises(RuntimeError, match=r"\(attempt 1\)"):
        await harness.run(model)

    assert model.scenario["writer_starts"][2] == 1
    failed, = _fanout_trace(harness, "writer_failed")
    assert failed["rerun"] is False
    assert _fanout_trace(harness, "writer_rerun_started") == []


@pytest.mark.asyncio
async def test_a_cancelled_writer_is_not_run_again(harness):
    model = ScriptedFanoutModel({"plan": _plan(*_SEVEN), "cancelled_writer": 2})

    with pytest.raises(asyncio.CancelledError):
        await harness.run(model)

    assert model.scenario["writer_starts"][2] == 1
    assert _fanout_trace(harness, "writer_failed") == []
    assert _fanout_trace(harness, "writer_rerun_started") == []


@pytest.mark.asyncio
async def test_a_writer_refusal_that_outlives_its_resend_is_traced_by_typed_fields(harness, monkeypatch):
    from agents.models.openai_responses import ResponsesWebSocketError

    monkeypatch.setenv("OPENAI_RESPONSES_INPUT_ITEM_ID_RETRIES", "0")

    def refusal(_message):
        return ResponsesWebSocketError({"type": "error", "status": 400, "error": {
            "type": "invalid_request_error", "code": "unsupported_parameter", "param": "input[2]",
            "message": "PRIVATE reasoning input in 'input[2]' with 'store=true'."}})

    model = ScriptedFanoutModel({"plan": _plan(*_SEVEN), "fail_once": {"writer": 1, "error": refusal}})

    await harness.run(model)

    failed, = _fanout_trace(harness, "writer_failed")
    assert (failed["provider_error_type"], failed["provider_error_code"], failed["failure_category"],
            failed["rerun"]) == ("invalid_request_error", "unsupported_parameter", "provider_unavailable", True)
    assert model.scenario["writer_starts"][1] == 2, "the refusal is worth a re-run"
    assert "PRIVATE" not in json.dumps(harness.trace, default=str)
    assert sorted(_finalized_labels(harness)) == sorted(_SEVEN)


@pytest.mark.asyncio
async def test_a_writer_cancelled_into_an_ordinary_error_is_not_run_again(harness):
    """A cancellation the SDK surfaces as a normal error is still a cancellation."""
    model = ScriptedFanoutModel({"plan": _plan(*_SEVEN), "failing_writer": 1, "converts_cancel_writer": 2})

    with pytest.raises(RuntimeError, match=r"writer provider failure \(attempt 1\)"):
        await harness.run(model)

    assert model.scenario["writer_starts"][1] == 2
    assert model.scenario["writer_starts"][2] == 1, "the cancelled writer is not run again"
    assert [event["writer"] for event in _fanout_trace(harness, "writer_failed")] == [1, 1]
    assert [event["writer"] for event in _fanout_trace(harness, "writer_rerun_started")] == [1]


@pytest.mark.asyncio
async def test_a_failure_not_worth_repeating_is_not_run_again(harness):
    from agents.models.openai_responses import ResponsesWebSocketError

    def context_limit(_message):
        return ResponsesWebSocketError({"type": "error", "status": 400, "error": {
            "type": "invalid_request_error", "code": "context_length_exceeded", "param": None,
            "message": "PRIVATE"}})

    model = ScriptedFanoutModel({"plan": _plan(*_SEVEN), "fail_once": {"writer": 2, "error": context_limit}})

    with pytest.raises(ResponsesWebSocketError):
        await harness.run(model)

    assert model.scenario["writer_starts"][2] == 1
    failed, = _fanout_trace(harness, "writer_failed")
    assert (failed["failure_category"], failed["rerun"]) == ("context_limit", False)
    assert _fanout_trace(harness, "writer_rerun_started") == []
    assert "PRIVATE" not in json.dumps(harness.trace, default=str)


@pytest.mark.asyncio
async def test_evidence_the_planner_recorded_first_survives_a_writer_clean_up(harness):
    model = ScriptedFanoutModel({
        "plan": _plan(*_SEVEN),
        "planner_evidence": "planner note",
        "extra_evidence": {2: [({1}, "planner note")]},
        "fail_once": {"writer": 2, "when": "after_staging"},
    })

    await harness.run(model)

    started, = _fanout_trace(harness, "writer_rerun_started")
    assert "ev-planner note" not in started["removed_evidence_ids"]
    assert started["kept_shared_evidence_count"] == 1
    note = _live_evidence_by_id(harness)["ev-planner note"]
    assert (note["entity"], note["verified_quote"]) == ("planner note", "quote for planner note")
    assert sorted(_finalized_labels(harness)) == sorted(_SEVEN)


@pytest.mark.asyncio
async def test_a_clean_up_failure_keeps_the_writer_error_as_its_cause(harness, monkeypatch):
    def broken(*_args, **_kwargs):
        raise ValueError("bookkeeping failed")

    monkeypatch.setattr(fanout, "remove_evidence_records", broken)
    model = ScriptedFanoutModel({"plan": _plan(*_SEVEN), "fail_once": {"writer": 2, "when": "after_staging"}})

    with pytest.raises(ValueError, match="bookkeeping failed") as raised:
        await harness.run(model)

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert str(raised.value.__cause__) == "writer provider failure (attempt 1)"
    assert model.scenario["writer_starts"][2] == 1


@pytest.mark.asyncio
async def test_a_cancelled_rerun_is_traced_and_stays_cancelled(harness):
    model = ScriptedFanoutModel({"plan": _plan(*_SEVEN), "fail_once": {"writer": 2}, "cancel_on_attempt": {2: 2}})

    with pytest.raises(asyncio.CancelledError):
        await harness.run(model)

    finished, = _fanout_trace(harness, "writer_rerun_finished")
    assert finished == {"writer": 2, "attempt": 2, "outcome": "cancelled"}


@pytest.mark.asyncio
async def test_two_writers_that_fail_once_both_run_again_and_succeed(harness):
    model = ScriptedFanoutModel({"plan": _plan(*_SEVEN), "fail_once": {"writer": (1, 3), "when": "after_staging"}})

    await harness.run(model)

    assert model.scenario["writer_starts"] == {1: 2, 2: 1, 3: 2}
    assert sorted((event["writer"], event["outcome"]) for event in _fanout_trace(harness, "writer_rerun_finished")) == [
        (1, "succeeded"), (3, "succeeded")]
    assert sorted(_finalized_labels(harness)) == sorted(_SEVEN)
    assert len(_live_evidence_by_id(harness)) == 7


@pytest.mark.asyncio
async def test_a_writer_failing_for_good_cancels_another_writer_mid_rerun(harness):
    model = ScriptedFanoutModel({
        "plan": _plan(*_SEVEN), "fail_once": {"writer": 1}, "failing_writer": 2, "busy_rerun_writer": 1,
    })

    with pytest.raises(RuntimeError, match=r"writer provider failure \(attempt 1\)"):
        await harness.run(model)

    assert (model.scenario["writer_starts"][1], model.scenario["writer_starts"][2]) == (2, 2)
    assert sorted((event["writer"], event["outcome"]) for event in _fanout_trace(harness, "writer_rerun_finished")) == [
        (1, "cancelled"), (2, "failed")]
    assert harness.workspaces[-1].state == builder.BUILDER_STATE_ABORTED
    assert harness.finalize_calls == []


_SIX = _SEVEN[:6]  # writers: 1 -> P1-P3, 2 -> P4-P6


@pytest.mark.asyncio
@pytest.mark.parametrize("busy, requests_before_failure", [
    ({"busy_model_writer": 2}, 0),
    ({"busy_tool_entity": "reagent 4"}, 1),
])
async def test_a_failed_run_traces_how_long_the_other_writers_took_to_stop(harness, busy, requests_before_failure):
    # Writer 2 is inside a model call or a tool call when writer 1 fails for good; each
    # stand-in waits 5 s unless it is cancelled.
    model = ScriptedFanoutModel({"plan": _plan(*_SIX), "failing_writer": 1, **busy})
    started = time.monotonic()

    with pytest.raises(RuntimeError, match=r"writer provider failure \(attempt 1\)"):
        await harness.run(model)

    assert time.monotonic() - started < 4, "the failure does not wait for the busy writer"
    assert model.scenario["writer_starts"][1] == 2, "the failed writer ran again once first"
    stopped, = _fanout_trace(harness, "writers_stopped")
    assert stopped["reason"] == "writer_failed"
    assert stopped["failed_writer"] == 1
    assert stopped["writers"] == [{"writer": 2, "outcome": "cancelled"}]
    assert 0 <= stopped["stop_ms"] < 1000
    assert sum(1 for request in model.requests if request["writer"] == 2) == requests_before_failure
    assert harness.workspaces[-1].candidates_staged_by(fanout._staging_owner(2, 1)) == []
    order = harness.trace_types()
    assert order.index("extraction_fanout.writer_rerun_finished") < order.index("extraction_fanout.writers_stopped")


def _writers_under_test(harness, monkeypatch, writer):
    agent = harness.agent(ScriptedFanoutModel({}))
    extraction = fanout.prepare_fanout_extraction(
        agent, builder_workspace=builder.ExtractionBuilderWorkspace(run_id="run-1"),
        specialist_name="Test Builder", input_text="Extract the reagents.",
        emit_event=harness.events.append, record_live_evidence=lambda record: None,
        evidence_records=[],
    )
    monkeypatch.setattr(extraction, "_run_writer", writer)
    return extraction


@pytest.mark.asyncio
async def test_a_writer_that_completes_after_the_failure_is_traced_as_completed(harness, monkeypatch):
    # The overrun signature: a writer that does not stop on cancel finishes after the run failed.
    # Writer 3 finished before the failure, so it is not listed.
    ready, finished = asyncio.Event(), asyncio.Event()

    async def writer(index, count, positions, *, attempt=1):
        if index == 1:
            await ready.wait()
            await finished.wait()
            raise ValueError("writer failure")
        if index == 2:
            ready.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)
                return "kept running"
        finished.set()
        return "finished early"

    extraction = _writers_under_test(harness, monkeypatch, writer)
    monkeypatch.setattr(extraction, "writer_reruns", 0)

    with pytest.raises(ValueError, match="writer failure"):
        await extraction._run_writers([(0,), (1,), (2,)])

    stopped, = _fanout_trace(harness, "writers_stopped")
    assert stopped["reason"] == "writer_failed" and stopped["failed_writer"] == 1
    assert stopped["writers"] == [{"writer": 2, "outcome": "completed"}]
    assert stopped["stop_ms"] >= 50


@pytest.mark.asyncio
async def test_a_cancelled_fanout_traces_how_the_writers_stopped(harness, monkeypatch):
    started = asyncio.Event()

    async def writer(index, count, positions, *, attempt=1):
        if index == count:
            started.set()
        await asyncio.Future()

    extraction = _writers_under_test(harness, monkeypatch, writer)
    parent = asyncio.create_task(extraction._run_writers([(0,), (1,)]))
    await started.wait()
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent

    stopped, = _fanout_trace(harness, "writers_stopped")
    assert stopped["reason"] == "cancelled" and stopped["failed_writer"] is None
    assert stopped["writers"] == [{"writer": 1, "outcome": "cancelled"}, {"writer": 2, "outcome": "cancelled"}]
    assert stopped["stop_ms"] < 1000


@pytest.mark.asyncio
async def test_an_interrupted_fanout_is_not_traced_as_cancelled(harness, monkeypatch):
    started = asyncio.Event()

    async def writer(index, count, positions, *, attempt=1):
        started.set()
        await asyncio.Future()

    class Interrupted(BaseException):
        pass

    extraction = _writers_under_test(harness, monkeypatch, writer)
    original_wait = asyncio.wait

    async def interrupted_wait(tasks, **kwargs):
        await started.wait()
        raise Interrupted

    # asyncio.wait is replaced only while the writers' wait runs.
    asyncio.wait = interrupted_wait
    try:
        with pytest.raises(Interrupted):
            await extraction._run_writers([(0,)])
    finally:
        asyncio.wait = original_wait

    stopped, = _fanout_trace(harness, "writers_stopped")
    assert stopped["reason"] == "interrupted" and stopped["failed_writer"] is None
    assert stopped["writers"] == [{"writer": 1, "outcome": "cancelled"}]


@pytest.mark.asyncio
async def test_a_run_whose_writers_all_succeed_traces_no_stop(harness):
    await harness.run(ScriptedFanoutModel({"plan": _plan(*_SEVEN)}))

    assert _fanout_trace(harness, "writers_stopped") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_parent", [False, True])
@pytest.mark.parametrize("capture_fails", [False, True])
async def test_writer_drain_reports_discarded_failures(harness, monkeypatch, caplog, cancel_parent, capture_fails):
    from src.lib.observability import runtime

    agent = harness.agent(ScriptedFanoutModel({}))
    extraction = fanout.prepare_fanout_extraction(
        agent, builder_workspace=builder.ExtractionBuilderWorkspace(run_id="fake-private-run-id"),
        specialist_name="fake-private-specialist", input_text="fake-private-prompt",
        emit_event=harness.events.append, record_live_evidence=lambda record: None,
        evidence_records=[],
    )
    ready = asyncio.Event()
    primary = RuntimeError("fake-private-provider-message")
    secondary = ValueError("fake-private-document-content")
    captured = []

    def capture(exc, **metadata):
        captured.append((exc, metadata))
        if capture_fails:
            raise RuntimeError("fake-private-telemetry-message")
        return True

    async def writer(index, count, positions, *, attempt=1):
        if index == count:
            ready.set()
        if not cancel_parent and index in (1, 2):
            await ready.wait()
            raise primary if index == 1 else secondary
        try:
            await asyncio.Future()
        finally:
            if index == 3:
                raise secondary
            if index == 4:
                owned = RuntimeError("fake-private-owned")
                owned._ai_curation_sentry_captured = True
                raise owned

    monkeypatch.setattr(runtime, "report_runtime_exception", capture)
    monkeypatch.setattr(extraction, "_run_writer", writer)
    parent = asyncio.create_task(extraction._run_writers([(i,) for i in range(5)]))
    await ready.wait()
    if cancel_parent:
        parent.cancel("caller stopped")
        with pytest.raises(asyncio.CancelledError, match="caller stopped"):
            await parent
    else:
        with pytest.raises(RuntimeError) as raised:
            await parent
        assert raised.value is primary

    (safe, metadata), = captured
    assert safe is not secondary
    assert safe.__cause__ is safe.__context__ is None
    assert metadata["operation"] == "extraction_fanout_additional_writer_failure"
    assert metadata["context"] == {"type": "ValueError"}
    assert getattr(secondary, "_ai_curation_sentry_captured", False) is (not capture_fails)
    assert "fake-private" not in repr((safe, metadata))
    assert "fake-private" not in caplog.text


def test_discarded_writer_sdk_capture_failure_is_private(monkeypatch, caplog):
    import sentry_sdk

    def fail_capture(exc):
        raise RuntimeError("fake-private-sdk-detail")

    monkeypatch.setattr(sentry_sdk, "capture_exception", fail_capture)
    secondary = ValueError("fake-private-writer-detail")
    fanout._report_discarded_writer_failure(secondary)

    assert not getattr(secondary, "_ai_curation_sentry_captured", False)
    assert "fake-private" not in caplog.text
    warning, = caplog.records
    assert warning.getMessage() == "Failed to capture runtime exception in Sentry"
    assert warning.sentry_skip_event is True


@pytest.mark.asyncio
@pytest.mark.parametrize("configured, reminders", [(None, 1), ("0", 0), ("3", 3)])
async def test_missing_plan_exhausts_reminder_budget(harness, monkeypatch, configured, reminders):
    key = "EXTRACTION_FANOUT_MAX_PLAN_REMINDERS"
    if configured is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, configured)
    model = ScriptedFanoutModel({"plan": None})

    with pytest.raises(streaming_tools.SpecialistOutputError, match="did not submit an extraction plan"):
        await harness.run(model)

    assert sum(_text(request["input"][-1]) == fanout.PLAN_REMINDER_MESSAGE
               for request in model.requests) == reminders
    assert harness.finalize_calls == []
    assert any(event.get("details", {}).get("reason") == "extraction_plan_missing" for event in harness.events)


@pytest.mark.asyncio
async def test_a_run_keeping_more_than_fifty_records_finalizes_all_of_them_in_one_call(harness):
    """KANBAN-2100 / ALL-1583: code finalizes every retained record at once, in plan order."""
    labels = [f"reagent {n}" for n in range(1, 56)]
    model = ScriptedFanoutModel({"plan": _plan(*labels)})

    await harness.run(model)

    workspace = harness.workspaces[-1]
    assert workspace.finalization is not None
    call, = harness.finalize_calls
    assert len(call) == 55 and len(set(call)) == 55
    assert workspace.finalization.source_candidate_ids == tuple(call)
    assert sorted(_finalized_labels(harness)) == sorted(labels)
    attempt, = [event["output_summary"] for event in harness.trace
                if event["event_type"] == "extraction_fanout.finalize_attempt"]
    assert (attempt["candidate_count"], attempt["finalized"]) == (55, True)


@pytest.mark.asyncio
async def test_reminded_plan_is_accepted(harness, monkeypatch):
    monkeypatch.setenv("EXTRACTION_FANOUT_MAX_PLAN_REMINDERS", "3")
    # The first planning run ends in text; the reminder run submits.
    model = ScriptedFanoutModel({"plan": _plan("reagent 1"), "first_run_text": True, "submit_on_reminder": True})

    await harness.run(model)

    assert sum(_text(request["input"][-1]) == fanout.PLAN_REMINDER_MESSAGE
               for request in model.requests) == 1
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
        record_live_evidence=lambda record: None, evidence_records=[],
    )

    assert prepared is None
    assert agent.tools == tools_before
    assert harness.trace_types() == ["extraction_fanout.not_applicable"]


@pytest.mark.asyncio
async def test_plan_citing_a_missing_chunk_is_rejected_then_corrected(harness):
    model = ScriptedFanoutModel({
        "first_plan": {"notes": [], "items": [{"label": "reagent 1", "chunks": [1, 99]},
                                              {"label": "reagent 2", "chunks": [2]}]},
        "plan": _plan("reagent 1", "reagent 2"),
    })

    await harness.run(model)

    rejected = next(
        item["output"] for request in model.requests for item in request["input"]
        if isinstance(item, dict) and item.get("call_id") == "plan-submit-first"
        and item.get("type") == "function_call_output"
    )
    assert json.loads(rejected)["status"] == "rejected"
    assert json.loads(rejected)["missing_chunk_indexes"] == [99]
    assert "do not exist in this document: 99." in json.loads(rejected)["message"]
    assert harness.chunk_checks == [
        {"chunk_indexes": [1, 2, 99], "user_id": "curator-1", "document_id": "doc-1"},
        {"chunk_indexes": [1, 2], "user_id": "curator-1", "document_id": "doc-1"},
    ]
    plan_event = next(event["output_summary"] for event in harness.trace
                      if event["event_type"] == "extraction_fanout.plan_submitted")
    assert [item["chunks"] for item in plan_event["items"]] == [[1, 2], [1, 2]]
    assert sorted(_finalized_labels(harness)) == ["reagent 1", "reagent 2"]
    # Nothing tried to read the missing chunk.
    assert 99 not in harness.reads


@pytest.mark.asyncio
async def test_chunk_check_infrastructure_failure_fails_the_step(harness):
    from agents.exceptions import UserError

    harness.existing_chunks["error"] = RuntimeError("chunk store unavailable")
    model = ScriptedFanoutModel({"plan": _plan("reagent 1", "reagent 2")})

    with pytest.raises(UserError, match="chunk store unavailable"):
        await harness.run(model)

    assert harness.finalize_calls == []
    assert harness.workspaces[-1].state == builder.BUILDER_STATE_ABORTED
    assert not [event for event in harness.trace if event["event_type"] == "extraction_fanout.plan_submitted"]


def test_fanout_needs_the_document_tools_scope(harness):
    agent = harness.agent(ScriptedFanoutModel({}))
    agent.cost_boundary = {"document_id": None, "user_id": "curator-1"}
    tools_before = list(agent.tools)

    prepared = fanout.prepare_fanout_extraction(
        agent, builder_workspace=builder.ExtractionBuilderWorkspace(run_id="trace-scope"),
        specialist_name="Test Builder", input_text="x", emit_event=harness.events.append,
        record_live_evidence=lambda record: None, evidence_records=[],
    )

    assert prepared is None
    assert agent.tools == tools_before
    assert [event["output_summary"]["reason"] for event in harness.trace] == ["no_document_scope"]


@pytest.mark.parametrize("provider,groq_enabled,expected", [
    ("openai", None, True),
    ("groq", None, None),
    ("groq", "true", True),
])
def test_parallel_tool_calls_follow_the_provider_gate(harness, monkeypatch, provider, groq_enabled, expected):
    from types import SimpleNamespace

    if groq_enabled is None:
        monkeypatch.delenv("GROQ_PARALLEL_TOOL_CALLS_ENABLED", raising=False)
    else:
        monkeypatch.setenv("GROQ_PARALLEL_TOOL_CALLS_ENABLED", groq_enabled)
    model = ScriptedFanoutModel({})
    model._agr_provider_id = provider
    agent = harness.agent(model)

    prepared = fanout.prepare_fanout_extraction(
        agent, builder_workspace=builder.ExtractionBuilderWorkspace(run_id="trace-gate"),
        specialist_name="Test Builder", input_text="x", emit_event=harness.events.append,
        record_live_evidence=lambda record: None, evidence_records=[],
    )
    prepared.start(planner_agent=agent, max_turns=4, run_config=None, base_run_config=None,
                   surface=SimpleNamespace(runtime="specialist"))

    assert agent.model_settings.parallel_tool_calls is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("store_responses,writer_store,store_off", [(None, False, True), ("true", None, False)])
async def test_writers_send_store_false_unless_the_setting_keeps_responses(
    harness, monkeypatch, store_responses, writer_store, store_off,
):
    if store_responses is None:
        monkeypatch.delenv("EXTRACTION_FANOUT_WRITER_STORE_RESPONSES", raising=False)
    else:
        monkeypatch.setenv("EXTRACTION_FANOUT_WRITER_STORE_RESPONSES", store_responses)
    model = ScriptedFanoutModel({"plan": _plan(*_SEVEN), "fail_once": {"writer": 3}})

    await harness.run(model)

    writer_requests = [request for request in model.requests if request["writer"] is not None]
    planner_requests = [request for request in model.requests if request["writer"] is None]
    assert {request["writer"] for request in writer_requests} == {1, 2, 3}
    assert {request["store"] for request in writer_requests} == {writer_store}
    # The planner thread and the check pass keep the provider default (unset).
    assert planner_requests and {request["store"] for request in planner_requests} == {None}
    # Every writer attempt records whether it sent store=false, the re-run included.
    completed = _fanout_trace(harness, "writer_completed")
    failed = _fanout_trace(harness, "writer_failed")
    assert sorted((event["writer"], event["attempt"]) for event in completed) == [(1, 1), (2, 1), (3, 2)]
    assert [(event["writer"], event["attempt"]) for event in failed] == [(3, 1)]
    assert {event["store_off"] for event in completed + failed} == {store_off}


@pytest.mark.parametrize("provider,store_responses,writer_store,store_off", [
    ("openai", None, False, True),
    ("openai", "true", None, False),
    # store is an OpenAI request field; other providers never receive it.
    ("groq", None, None, False),
])
def test_writer_store_is_set_on_the_writer_copy_only(
    harness, monkeypatch, provider, store_responses, writer_store, store_off,
):
    from types import SimpleNamespace

    if store_responses is None:
        monkeypatch.delenv("EXTRACTION_FANOUT_WRITER_STORE_RESPONSES", raising=False)
    else:
        monkeypatch.setenv("EXTRACTION_FANOUT_WRITER_STORE_RESPONSES", store_responses)
    model = ScriptedFanoutModel({})
    model._agr_provider_id = provider
    agent = harness.agent(model)
    prepared = fanout.prepare_fanout_extraction(
        agent, builder_workspace=builder.ExtractionBuilderWorkspace(run_id="trace-store"),
        specialist_name="Test Builder", input_text="x", emit_event=harness.events.append,
        record_live_evidence=lambda record: None, evidence_records=[],
    )
    prepared.start(planner_agent=agent, max_turns=4, run_config=None, base_run_config=None,
                   surface=SimpleNamespace(runtime="specialist"))
    planner_settings = agent.model_settings

    writer = prepared._writer_agent((1, 1))

    assert prepared.writer_store_off is store_off
    assert writer.model_settings.store is writer_store
    assert agent.model_settings is planner_settings
    assert planner_settings.store is None
    # The writer's copy keeps the planner's other settings.
    assert writer.model_settings.parallel_tool_calls == planner_settings.parallel_tool_calls


# ---------------------------------------------------------------------------
# Chunk existence query
# ---------------------------------------------------------------------------


def _chunk_store(monkeypatch, objects):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from src.lib.weaviate_client import chunks

    collection = MagicMock()
    collection.query.fetch_objects.return_value = SimpleNamespace(objects=[
        SimpleNamespace(properties={"documentId": document_id, "chunkIndex": index})
        for document_id, index in objects
    ])

    @contextmanager
    def session():
        yield MagicMock()

    connection = MagicMock()
    connection.session.side_effect = session
    monkeypatch.setattr(chunks, "get_connection", lambda: connection)
    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", MagicMock(return_value=(collection, None)))
    return chunks, collection


@pytest.mark.asyncio
async def test_existing_chunk_indexes_uses_one_tenant_scoped_query(monkeypatch):
    chunks, collection = _chunk_store(monkeypatch, [("doc-a", 1), ("doc-a", 3)])

    found = await chunks.get_existing_chunk_indexes(chunk_indexes=[3, 1, 5, 1], user_id="alice", document_id="doc-a", readable=single_file("doc-a"))

    assert found == {1, 3}
    collection.query.fetch_objects.assert_called_once()
    arguments = collection.query.fetch_objects.call_args.kwargs
    assert arguments["limit"] == 6
    assert arguments["return_properties"] == ["documentId", "chunkIndex"]


@pytest.mark.asyncio
async def test_existing_chunk_indexes_rejects_inconsistent_results_and_bad_input(monkeypatch):
    chunks, collection = _chunk_store(monkeypatch, [("doc-b", 1)])

    with pytest.raises(ValueError, match="inconsistent"):
        await chunks.get_existing_chunk_indexes(chunk_indexes=[1], user_id="alice", document_id="doc-a", readable=single_file("doc-a"))
    with pytest.raises(ValueError, match="non-negative"):
        await chunks.get_existing_chunk_indexes(chunk_indexes=[-1], user_id="alice", document_id="doc-a", readable=single_file("doc-a"))
    assert await chunks.get_existing_chunk_indexes(chunk_indexes=[], user_id="alice", document_id="doc-a", readable=single_file("doc-a")) == set()
    assert collection.query.fetch_objects.call_count == 1


def test_provider_gate_matches_model_settings_gates(monkeypatch):
    from src.lib.openai_agents import config

    monkeypatch.delenv("GROQ_PARALLEL_TOOL_CALLS_ENABLED", raising=False)
    assert config.provider_allows_parallel_tool_calls("gpt-test", "openai") is True
    assert config.provider_allows_parallel_tool_calls("gpt-test", "groq") is False




@pytest.mark.asyncio
async def test_writer_updates_its_own_new_evidence_record_by_id(harness):
    labels = [f"reagent {n}" for n in range(1, 8)]
    model = ScriptedFanoutModel({"plan": _plan(*labels), "updating_writer": 1})

    await harness.run(model)

    update = next(
        item["output"] for request in model.requests for item in request["input"]
        if isinstance(item, dict) and item.get("call_id") == "w1-upd" and item.get("type") == "function_call_output"
    )
    assert json.loads(update)["status"] == "verified"
    summary = next(event for event in harness.events if event.get("type") == "SPECIALIST_SUMMARY")["details"]
    records = {record["evidence_record_id"]: record for record in summary["liveEvidenceRecords"]}
    assert len(records) == 7
    assert records["ev-reagent 1"]["verified_quote"] == "updated quote for reagent 1"


@pytest.mark.asyncio
async def test_the_result_names_the_planner_and_every_writer_attempt_whose_reads_count(harness, monkeypatch):
    # ALL-1460 fix 3: writers read and search under their own history invocations;
    # the saved result's files read must cover them, re-run attempts included.
    from uuid import uuid4
    from src.lib.context import set_current_trace_id, set_current_user_id
    from src.lib.openai_agents import run_history

    # Real history invocations; their rows are not stored (the lookup by these ids
    # is covered on PostgreSQL in test_extraction_result_files_read.py).
    monkeypatch.setattr(run_history.HistoryStore, "append", lambda self, *_args, **_kwargs: str(uuid4()))
    set_current_user_id("curator-1")
    set_current_trace_id("trace-fanout")
    labels = [f"reagent {n}" for n in range(1, 7)]
    model = ScriptedFanoutModel({"plan": _plan(*labels), "writer_reads": True,
                                 "fail_once": {"writer": 2, "when": "after_staging"}})

    await harness.run(model)

    calls = model.scenario["source_calls"]
    scope = _internal_result(harness.events)["internal"]
    planner, *writers = scope["history_invocation_ids"]
    # Each writer attempt (writer 2 ran twice) made its own search under its own invocation.
    searches = [invocation for name, invocation in calls if name == "search_document"]
    assert len(searches) == 3 and len(set(searches)) == 3
    assert set(searches) == set(writers)
    # Every source call, the planner's reads and the prefetch for writers included,
    # ran under an invocation the result names.
    assert {invocation for _, invocation in calls} == {planner, *writers}
    assert "files_read_unrecorded" not in scope
