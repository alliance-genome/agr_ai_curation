"""Shadow Decisions quick check: W5 input, threshold routes, and an observer with no effect on review."""
import asyncio
import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import AsyncOpenAI

from src.lib.validation_history.store import HistoryScope
from src.lib.validation_review import quick_check
from src.lib.validation_review.decisions import ReviewContext

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "validation_review"
QUESTION = "What transgenic flies are used in the publication?"
SCOPE = HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session")


def _packet():
    return json.loads((FIXTURES / "quick_check_packet_t58.json").read_text())


def _answer_all(questions, probability=1.0, overrides=None):
    overrides = overrides or {}
    return [{"type": "predicate", "name": question["name"], "probability": overrides.get(question["name"], probability)}
            for question in questions]


class FakeDecisions:
    """The Decisions endpoint behind a mocked HTTP transport; records every request body."""

    def __init__(self, respond=None):
        self.requests = []
        self.respond = respond or (lambda body: {
            "model": body["model"], "answers": _answer_all(body["questions"]),
            "usage": {"input_tokens": 3458, "input_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 1267},
                      "output_tokens": 0, "total_tokens": 3458}})

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append({"url": str(request.url), "body": body})
        response = self.respond(body)
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, json=response)

    def client(self):
        return AsyncOpenAI(api_key="test-placeholder", base_url="https://decisions.test/v1", max_retries=0,
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))


@pytest.fixture
def decisions(monkeypatch):
    fake = FakeDecisions()
    monkeypatch.setattr(quick_check, "_decisions_client", fake.client)
    return fake


@pytest.fixture
def captured(monkeypatch):
    sink = SimpleNamespace(events=[], reports=[])
    monkeypatch.setattr("src.lib.openai_agents.extraction_trace_events.write_extraction_trace_event",
                        lambda **kwargs: sink.events.append(kwargs))
    monkeypatch.setattr("src.lib.observability.runtime.report_runtime_exception",
                        lambda exc, **kwargs: sink.reports.append((exc, kwargs)) or True)
    return sink


async def _drain():
    while quick_check._RUNNING:
        await asyncio.gather(*list(quick_check._RUNNING))


def _record(rid, *, role=None, validators=(), **probabilities):
    record = {"record": rid, "target_id": "target-" + rid, "type": "generic_object", "role": role,
              "top_level": role in quick_check.TOP_LEVEL_ROLES, "source_available": True,
              "values": {}, "evidence": [],
              "validators": [{"validator": "gene", "field": "gene", "status": status, "values": None,
                              "explanation": None} for status in validators]}
    names = {f"{name}__{rid}": value for name, value in probabilities.items() if not name.startswith("V")}
    names.update({f"validator__{rid}{name}": value for name, value in probabilities.items() if name.startswith("V")})
    return record, names


def test_input_and_questions_match_the_measured_w5_wording():
    records = quick_check.asked_records(quick_check.packet_records(_packet()))
    assert [record["record"] for record in records] == ["R1", "R2", "R3", "R4", "R5", "R6"]
    assert quick_check.build_input(QUESTION, records) == (FIXTURES / "quick_check_t58_w5_input.txt").read_text()
    questions = [question for record in records for question in quick_check.record_questions(record)]
    assert questions == json.loads((FIXTURES / "quick_check_t58_w5_questions.json").read_text())


def test_validator_answers_with_a_resolved_answer_get_one_question_each():
    packet = _packet()
    packet["records"][0]["review"] += [
        {"review": "validator_answer", "handle": "D7", "validator": "gene_validator", "field": "gene",
         "outcome": "failed"},
        {"review": "validator_answer", "handle": "D8", "validator": "gene_validator", "field": "gene",
         "status": "resolved", "values": {"curie": "FB:FBgn0259685"}, "explanation": "Exact symbol match."},
        {"review": "waiting", "codes": ["attempt_running"], "target_id": "t"},
    ]
    record = quick_check.packet_records(packet)[0]
    text = quick_check.build_input(QUESTION, [record])
    assert "validator answer R1V1 (gene_validator, field gene): status failed" in text
    assert ('validator answer R1V2 (gene_validator, field gene): status resolved; resolved values '
            '{"curie": "FB:FBgn0259685"}; explanation "Exact symbol match."') in text
    names = [question["name"] for question in quick_check.record_questions(record)]
    assert names == ["in_scope__R1", "supported__R1", "own_study__R1", "validator__R1V2"]


@pytest.mark.parametrize("curator,context,expected", [
    (QUESTION, {"source_kind": "chat", "executed_query": "Extract reagents."}, (QUESTION, "curator_request")),
    (QUESTION, None, (QUESTION, "curator_request")),
    ("Task Instructions: run the step", {"source_kind": "flow", "executed_query": "Extract reagents."},
     ("Extract reagents.", "delegation")),
    ("", {"source_kind": "chat", "executed_query": "Extract reagents."}, ("Extract reagents.", "delegation")),
    ("Task Instructions: run the step", {"source_kind": "flow"}, ("Task Instructions: run the step", "curator_request")),
    (None, None, (None, "none")),
])
def test_request_text_follows_the_calibration_rule(curator, context, expected):
    assert quick_check.review_request(curator, context) == expected


def test_long_request_text_is_cut_like_the_calibration_input():
    text, _ = quick_check.review_request("x" * 3001, None)
    assert text == "x" * 3000 + " [truncated]"
    assert '"(no request text recorded)"' in quick_check.build_input(None, [])


@pytest.mark.parametrize("probabilities,expected", [
    ({"in_scope": 0.02, "supported": 1.0, "own_study": 1.0}, ("remove", None)),
    ({"in_scope": 0.021, "supported": 1.0, "own_study": 1.0}, ("sol", None)),
    ({"in_scope": 0.8, "supported": 0.8, "own_study": 0.5}, ("skip", None)),
    ({"in_scope": 0.79, "supported": 1.0, "own_study": 1.0}, ("sol", None)),
    ({"in_scope": 1.0, "supported": 0.79, "own_study": 1.0}, ("sol", None)),
    ({"in_scope": 1.0, "supported": 1.0, "own_study": 0.49}, ("sol", None)),
    ({"in_scope": 1.0, "supported": 1.0}, ("sol", "unanswered")),
    ({"in_scope": 0.0}, ("remove", None)),
])
def test_routes_apply_the_thresholds(probabilities, expected):
    record, answers = _record("R1", **probabilities)
    assert quick_check.route(record, answers) == expected


def test_every_resolved_validator_answer_must_clear_its_threshold_to_skip():
    record, answers = _record("R1", validators=("resolved", "failed", "ambiguous"),
                              in_scope=1.0, supported=1.0, own_study=1.0, V1=0.8, V3=0.79)
    assert quick_check.route(record, answers) == ("sol", None)
    answers["validator__R1V3"] = 0.8
    assert quick_check.route(record, answers) == ("skip", None)
    del answers["validator__R1V3"]
    assert quick_check.route(record, answers) == ("sol", "unanswered")


@pytest.mark.parametrize("role", ["validated_reference", "metadata_only", "evidence_quote", "generic_proxy_object"])
def test_sub_records_always_stay_with_the_supervisor(role):
    for probabilities in ({"in_scope": 1.0, "supported": 1.0, "own_study": 1.0}, {"in_scope": 0.0}):
        record, answers = _record("R1", role=role, **probabilities)
        assert record["top_level"] is False
        assert quick_check.route(record, answers) == ("sol", "sub_record")


def test_curatable_units_and_records_without_a_declared_role_are_top_level():
    packet = _packet()
    packet["records"][0]["role"] = "curatable_unit"
    packet["records"][1]["role"] = "validated_reference"
    packet["records"].append({"record": None, "review": [{"review": "waiting", "codes": ["global"]}]})
    records = quick_check.packet_records(packet)
    assert [(record["record"], record["top_level"]) for record in records][:3] == [
        ("R1", True), ("R2", False), ("R3", True)]
    assert len(records) == 6


def test_record_without_its_saved_source_is_not_asked_and_stays_with_the_supervisor():
    packet = _packet()
    packet["records"][2] = {"record": "R3", "target_id": "t3", "source_unavailable": "History conflict",
                            "review": [{"review": "record", "handle": "D3"}]}
    records = quick_check.packet_records(packet)
    assert [record["record"] for record in quick_check.asked_records(records)] == ["R1", "R2", "R4", "R5", "R6"]
    assert quick_check.route(records[2], {}) == ("sol", "source_unavailable")


@pytest.mark.asyncio
async def test_check_sends_w5_with_the_packet_cache_key_and_logs_routes(decisions):
    overrides = {"in_scope__R4": 0.0, "own_study__R5": 0.3}
    decisions.respond = lambda body: {
        "answers": _answer_all(body["questions"], overrides=overrides),
        "usage": {"input_tokens": 3458, "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 1267},
                  "output_tokens": 0}}
    packet = _packet()
    result = await quick_check.check_packet(packet, request=QUESTION, cache_key=packet["packet_id"])

    [sent] = decisions.requests
    assert sent["url"] == "https://decisions.test/v1/decisions"
    assert sent["body"]["model"] == "gpt-6-luna"
    assert sent["body"]["prompt_cache_key"] == packet["packet_id"]
    assert sent["body"]["input"] == (FIXTURES / "quick_check_t58_w5_input.txt").read_text()
    assert len(sent["body"]["questions"]) == 18
    assert result["status"] == "completed"
    assert result["routes"] == {"skip": 4, "remove": 1, "sol": 1}
    assert [(row["record"], row["route"]) for row in result["records"]] == [
        ("R1", "skip"), ("R2", "skip"), ("R3", "skip"), ("R4", "remove"), ("R5", "sol"), ("R6", "skip")]
    assert result["records"][4] == {
        "record": "R5", "target_id": "21910a7e-6dd4-435d-90cf-f5029516d901", "type": "generic_reagent_candidate",
        "role": None, "top_level": True, "route": "sol", "in_scope": 1.0, "supported": 1.0, "own_study": 0.3}
    assert result["usage"] == {"input": 3458, "cached_input": 0, "cache_write_input": 1267, "output": 0}
    assert result["thresholds"]["remove_in_scope_at_most"] == 0.02


@pytest.mark.asyncio
async def test_more_than_400_questions_are_split_into_requests_that_share_one_input(decisions):
    packet = _packet()
    template = packet["records"][0]
    packet["records"] = [{**copy.deepcopy(template), "record": f"R{index}", "target_id": f"t{index}"}
                         for index in range(1, 135)]
    result = await quick_check.check_packet(packet, request=QUESTION, cache_key="packet-big")

    sizes = [len(sent["body"]["questions"]) for sent in decisions.requests]
    assert sizes == [400, 2]
    assert len({sent["body"]["input"] for sent in decisions.requests}) == 1
    assert {sent["body"]["prompt_cache_key"] for sent in decisions.requests} == {"packet-big"}
    names = [question["name"] for sent in decisions.requests for question in sent["body"]["questions"]]
    assert len(names) == len(set(names)) == 402
    assert result["request_count"] == 2 and result["routes"] == {"skip": 134, "remove": 0, "sol": 0}
    assert result["usage"]["input"] == 2 * 3458


@pytest.mark.asyncio
async def test_refused_questions_leave_the_record_with_the_supervisor(decisions):
    def respond(body):
        answers = _answer_all(body["questions"])
        answers[1] = {"type": "refusal", "name": answers[1]["name"]}
        return {"answers": answers, "usage": {"input_tokens": 10}}

    decisions.respond = respond
    result = await quick_check.check_packet(_packet(), request=QUESTION, cache_key="p")
    assert result["status"] == "partial"
    assert result["refused"] == ["supported__R1"] and result["missing"] == []
    assert result["records"][0]["route"] == "sol" and result["records"][0]["route_reason"] == "unanswered"
    assert result["usage"] == {"input": 10, "cached_input": 0, "cache_write_input": 0, "output": 0}


@pytest.mark.asyncio
async def test_packet_without_records_makes_no_call(decisions):
    packet = {"packet_id": "p", "evidence": {}, "records": [{"record": None, "review": []}]}
    result = await quick_check.check_packet(packet, request=QUESTION, cache_key="p")
    assert result["status"] == "no_questions" and decisions.requests == []


def _context():
    return ReviewContext(SCOPE, "episode-1", "reviewer")


def _source(metadata=None):
    """The saved extraction record under review; only its metadata is read."""
    return SimpleNamespace(metadata=metadata or {})


@pytest.mark.asyncio
async def test_shadow_check_logs_one_trace_event_per_fresh_packet(decisions, captured, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=quick_check.__name__)
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    loads = []
    monkeypatch.setattr(quick_check, "_load_packet",
                        lambda scope, episode, packet: loads.append((scope, episode, packet)) or _packet())
    metadata = {"execution_context": {"source_kind": "chat", "executed_query": "Extract transgenic fly stocks."}}
    with quick_check.review_scope(QUESTION, _source(metadata)):
        quick_check.packet_pinned(_context(), "packet-1")
        quick_check.packet_pinned(_context(), "packet-1")
        await _drain()
    assert loads == [(SCOPE, "episode-1", "packet-1")]
    [event] = captured.events
    assert event["event_type"] == "review_quick_check.shadow"
    payload = event["output_summary"]
    assert {key: payload[key] for key in ("packet_id", "episode_id", "wording_version", "model", "mode", "status",
                                          "request_source")} == {
        "packet_id": "packet-1", "episode_id": "episode-1", "wording_version": "W5", "model": "gpt-6-luna",
        "mode": "shadow", "status": "completed", "request_source": "curator_request"}
    assert decisions.requests[0]["body"]["input"].startswith(
        'A curator asked an AI curation assistant this request about one publication:\n"' + QUESTION + '"')
    assert captured.reports == []
    [line] = [record for record in caplog.records if getattr(record, "event", None) == "review_quick_check"]
    assert line.levelname == "INFO" and line.quick_check["routes"] == payload["routes"]
    assert line.quick_check["records"][0] == {"record": "R1", "route": "skip", "in_scope": 1.0,
                                              "supported": 1.0, "own_study": 1.0}
    assert payload["records"][0]["target_id"] == "cafa5538-3de0-48ba-a2c2-bdf1d8912198"


@pytest.mark.asyncio
async def test_mode_off_makes_no_call(decisions, captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "off")
    load = Mock()
    monkeypatch.setattr(quick_check, "_load_packet", load)
    with quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), "packet-1")
        await _drain()
    assert not quick_check._RUNNING and decisions.requests == [] and captured.events == []
    load.assert_not_called()


@pytest.mark.asyncio
async def test_default_mode_is_off(decisions, captured, monkeypatch):
    monkeypatch.delenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", raising=False)
    with quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), "packet-1")
        await _drain()
    assert decisions.requests == [] and captured.events == []


@pytest.mark.asyncio
async def test_review_without_a_bound_scope_is_not_checked(decisions, captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    quick_check.packet_pinned(_context(), "packet-1")
    await _drain()
    assert decisions.requests == [] and captured.events == []


@pytest.mark.asyncio
async def test_provider_failure_is_logged_without_effect(decisions, captured, monkeypatch, caplog):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    decisions.respond = lambda body: httpx.Response(
        503, json={"error": {"message": "overloaded", "type": "server_error", "code": "server_overloaded"}})
    with quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), "packet-1")
        await _drain()
    [event] = captured.events
    payload = event["output_summary"]
    assert payload["status"] == "failed"
    assert payload["error"] == {"type": "InternalServerError", "status_code": 503, "code": "server_overloaded"}
    assert "records" not in payload and "routes" not in payload
    assert captured.reports == []
    assert any(record.message == "Validation review quick check failed" for record in caplog.records)


@pytest.mark.asyncio
async def test_unexpected_failure_reports_without_content_and_without_effect(captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")

    def broken(*args):
        raise KeyError("curator note: fake sensitive transgenic stock text")

    monkeypatch.setattr(quick_check, "_load_packet", broken)
    with quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), "packet-1")
        await _drain()
    [event] = captured.events
    assert event["output_summary"]["status"] == "failed"
    assert event["output_summary"]["error"] == {"type": "KeyError"}
    [(exc, kwargs)] = captured.reports
    assert str(exc) == "Validation review quick check failed (KeyError)"
    assert exc.__cause__ is None and exc.__context__ is None
    assert kwargs == {"component": "validation_review", "operation": "quick_check_failed", "level": "warning"}
    assert "fake sensitive" not in repr(captured)


@pytest.mark.asyncio
async def test_invalid_mode_is_reported_and_never_raised(captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "on")
    with quick_check.review_scope(QUESTION, _source()):
        assert quick_check.packet_pinned(_context(), "packet-1") is None
    [(exc, _)] = captured.reports
    assert str(exc) == "Validation review quick check failed (ValueError)"


def _read_tool(context):
    from src.lib.validation_review.tools import create_tools

    return next(tool for tool in create_tools(context, checkpoint_provider=Mock()) if tool.name == "read_review_packet")


async def _invoke(tool, arguments):
    from agents.tool_context import ToolContext

    text = json.dumps(arguments)
    return await tool.on_invoke_tool(
        ToolContext(context=None, tool_name=tool.name, tool_call_id="read", tool_arguments=text), text)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "provider_failure", "observer_defect"])
async def test_reviewer_packet_read_is_unchanged_by_the_shadow_check(outcome, decisions, captured, monkeypatch):
    from src.lib.validation_review import reads

    page = {"packet_id": "packet-1", "page": 1, "pages": 1, "next_page": None, "ready": False,
            "record_count": 6, "answer_count": 0, "decision_count": 6,
            "records": _packet()["records"], "evidence": _packet()["evidence"]}
    calls = []
    monkeypatch.setattr(reads, "review_packet", lambda *args, **kwargs: calls.append((args, kwargs)) or
                        copy.deepcopy(page))
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    if outcome == "provider_failure":
        decisions.respond = lambda body: httpx.Response(500, json={"error": {"message": "boom"}})
    elif outcome == "observer_defect":
        monkeypatch.setattr(quick_check, "check_packet", Mock(side_effect=TypeError("defect")))
    context = _context()
    tool = _read_tool(context)
    with quick_check.review_scope(QUESTION, _source()):
        first = await _invoke(tool, {})
        second = await _invoke(tool, {"packet_id": "packet-1", "page": 1})
        await _drain()
    assert first == page and second == page
    assert calls == [((SCOPE, "episode-1"), {"packet_id": None, "page": None}),
                     ((SCOPE, "episode-1"), {"packet_id": "packet-1", "page": 1})]
    [event] = captured.events
    assert event["output_summary"]["status"] == ("completed" if outcome == "completed" else "failed")
    assert len(decisions.requests) == (0 if outcome == "observer_defect" else 1)


@pytest.mark.asyncio
async def test_reviewer_packet_read_does_not_wait_for_the_check(captured, monkeypatch):
    from src.lib.validation_review import reads

    release = asyncio.Event()

    async def slow_check(body, *, request, cache_key):
        await release.wait()
        return {"status": "completed"}

    monkeypatch.setattr(reads, "review_packet", lambda *args, **kwargs: {"packet_id": "packet-1"})
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    monkeypatch.setattr(quick_check, "check_packet", slow_check)
    with quick_check.review_scope(QUESTION, _source()):
        result = await asyncio.wait_for(_invoke(_read_tool(_context()), {}), timeout=5)
        assert result == {"packet_id": "packet-1"} and captured.events == []
        release.set()
        await _drain()
    assert captured.events[0]["output_summary"]["status"] == "completed"


@pytest.mark.asyncio
async def test_binding_a_scope_reads_nothing_and_a_bad_record_only_fails_the_check(decisions, captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    with quick_check.review_scope(QUESTION, object()):
        quick_check.packet_pinned(_context(), "packet-1")
        await _drain()
    [event] = captured.events
    assert event["output_summary"]["status"] == "failed"
    assert event["output_summary"]["error"] == {"type": "AttributeError"}
    assert decisions.requests == [] and len(captured.reports) == 1


@pytest.mark.asyncio
async def test_the_check_is_never_charged_to_the_review_budget(captured, monkeypatch):
    from src.lib.validation_review.provider import ACTIVE_BUDGET, ExecutionBudget, budget_scope

    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    seen = []

    async def check(body, *, request, cache_key):
        seen.append(ACTIVE_BUDGET.get())
        return {"status": "completed"}

    monkeypatch.setattr(quick_check, "check_packet", check)
    budget = ExecutionBudget(SCOPE, "episode-1", "reviewer_tokens", True, reviewer_agent_id="reviewer")
    with budget_scope(budget), quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), "packet-1")
        await _drain()
        assert ACTIVE_BUDGET.get() is budget
    assert seen == [None]
