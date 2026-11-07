"""Shadow Decisions quick check: trial input, two-order averaging, three rules, and no effect on review."""
import asyncio
import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import AsyncOpenAI

from src.lib.validation_history.store import HistoryScope
from src.lib.validation_review import quick_check
from src.lib.validation_review.decisions import ReviewContext

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "validation_review"
QUESTION = "What transgenic flies are used in the publication?"
SCOPE = HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session")
RULES = {rule.name: rule for rule in quick_check.DEFINITION.rules}
PAGE = {"packet_id": "packet-1"}


def _packet(validators=True):
    """Tonight's T58 packet; R1 also carries a failed and a resolved validator answer."""
    packet = json.loads((FIXTURES / "quick_check_packet_t58.json").read_text())
    if validators:
        packet["records"][0]["review"] += [
            {"review": "validator_answer", "handle": "D7", "validator": "gene_validator", "field": "gene",
             "outcome": "failed"},
            {"review": "validator_answer", "handle": "D8", "validator": "gene_validator", "field": "gene",
             "status": "resolved", "values": {"curie": "FB:FBgn0259685"}, "explanation": "Exact symbol match."},
        ]
    return packet


def _golden():
    return json.loads((FIXTURES / "quick_check_t58_v6_golden.json").read_text())


def _answers(questions, overrides=None, default=None):
    """Answer every question: scope and validator yes, negated questions no, choice accept."""
    overrides = overrides or {}
    answers = []
    for question in questions:
        name = question["name"]
        if question["type"] == "choice":
            accept = overrides.get(name, 0.9)
            answers.append({"type": "choice", "name": name, "choice": "accept", "confidence": accept,
                            "probabilities": [{"value": "accept", "probability": accept},
                                              {"value": "remove_out_of_scope", "probability": 0.0},
                                              {"value": "needs_review", "probability": round(1 - accept, 6)}]})
            continue
        negated = name.startswith(("sup_neg", "own_neg"))
        probability = overrides.get(name, default if default is not None else (0.0 if negated else 1.0))
        answers.append({"type": "predicate", "name": name, "probability": probability})
    return answers


class FakeDecisions:
    """The Decisions endpoint behind a mocked HTTP transport; records every request body."""

    def __init__(self):
        self.requests = []
        self.clients = []
        self.respond = lambda body: {"answers": _answers(body["questions"]),
                                     "usage": {"input_tokens": 3000, "output_tokens": 0,
                                               "input_tokens_details": {"cached_tokens": 1000,
                                                                        "cache_write_tokens": 500}}}

    async def handler(self, request):
        body = json.loads(request.content)
        self.requests.append({"url": str(request.url), "body": body})
        response = self.respond(body)
        if asyncio.iscoroutine(response):
            response = await response
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, json=response)

    def client(self):
        client = AsyncOpenAI(api_key="test-placeholder", base_url="https://decisions.test/v1", max_retries=0,
                             http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))
        self.clients.append(client)
        return client

    def order(self, order):
        return [sent for sent in self.requests if sent["body"]["prompt_cache_key"].endswith(":" + order)]


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


async def _check(fake, packet, *, cache_key="p"):
    prepared = quick_check.prepare_check(packet, request=QUESTION)
    async with fake.client() as client:
        outcomes = await quick_check.run_check(prepared, client, cache_key=cache_key)
    return quick_check.summarize(prepared, outcomes)


async def _drain():
    while quick_check._RUNNING:
        await asyncio.gather(*list(quick_check._RUNNING))


def _context():
    return ReviewContext(SCOPE, "episode-1", "reviewer")


def _source(metadata=None):
    """The saved extraction record under review; only its metadata is read."""
    return SimpleNamespace(metadata=metadata or {})


def test_both_orders_render_exactly_as_the_trial_did():
    records = quick_check.asked_records(quick_check.packet_records(_packet()))
    golden = _golden()
    for order in (quick_check.FORWARD, quick_check.REVERSED):
        labelled = quick_check.ordered(records, order)
        assert {rid: record["record"] for rid, record in labelled} == golden[order]["labels"]
        assert quick_check.build_input(QUESTION, labelled) == golden[order]["input"]
        questions = [question for rid, record in labelled
                     for question, *_ in quick_check.record_questions(rid, record)]
        assert questions == golden[order]["questions"]


def test_definition_is_one_versioned_set():
    definition = quick_check.DEFINITION
    assert definition.wording_id == "v6-shadow"
    assert [question.key for question in definition.questions] == [
        "scope_lenient", "sup_neg", "own_neg", "val_entity", "choice3"]
    assert [question.key for question in definition.questions if question.negated] == ["sup_neg", "own_neg"]
    assert definition.orders == ("forward", "reversed")
    assert {rule.name: (rule.remove_scope_below, rule.skip_at_least) for rule in definition.rules} == {
        "removal_only": (0.5, None),
        "balanced": (0.3, (("scope_lenient", 0.8), ("sup_neg", 0.8), ("own_neg", 0.9), ("val_entity", 0.8))),
        "strict": (0.3, (("scope_lenient", 0.8), ("sup_neg", 0.8), ("own_neg", 0.9), ("val_entity", 0.8),
                         ("choice3:accept", 0.5))),
    }


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


def _record(*, kind="object", validators=()):
    role = {"object": None, "claim": None, "sub_record": "validated_reference"}[kind]
    record_type = "generic_claim" if kind == "claim" else "generic_object"
    record = {"record": "R1", "target_id": "t1", "type": record_type, "role": role, "source_available": True,
              "values": {}, "evidence": [],
              "validators": [{"validator": "v", "field": "f", "status": status, "values": None, "explanation": None}
                             for status in validators]}
    record["kind"] = quick_check.record_kind(record)
    return record


def _orders(forward, reversed_=None):
    """Per-order answers in the shape summarize collects them."""
    return {"forward": forward, "reversed": forward if reversed_ is None else reversed_}


GOOD = {"scope_lenient": 1.0, "sup_neg": 0.0, "own_neg": 0.0, "choice3": {"accept": 0.9, "remove_out_of_scope": 0.0,
                                                                          "needs_review": 0.1}}


def _routes(record, answers):
    scores = quick_check.oriented_scores(answers, record)
    return {name: quick_check.route(record, scores, rule) for name, rule in RULES.items()}


def test_orders_are_averaged_and_negated_questions_turned_around():
    record = _record(validators=("resolved", "failed", "ambiguous"))
    forward = {**GOOD, "scope_lenient": 0.95, "sup_neg": 0.1, "own_neg": 0.04, "val_entity": {1: 0.9, 3: 0.7}}
    reverse = {**GOOD, "scope_lenient": 0.12, "sup_neg": 0.3, "own_neg": 0.0, "val_entity": {1: 0.8, 3: 0.95},
               "choice3": {"accept": 0.4, "remove_out_of_scope": 0.2, "needs_review": 0.4}}
    scores = quick_check.oriented_scores(_orders(forward, reverse), record)
    assert scores == pytest.approx({"scope_lenient": 0.535, "sup_neg": 0.8, "own_neg": 0.98, "val_entity": 0.825,
                                    "choice3:accept": 0.65, "choice3:remove_out_of_scope": 0.1,
                                    "choice3:needs_review": 0.25})
    assert _routes(record, _orders(forward, reverse)) == {
        "removal_only": ("sol", None), "balanced": ("sol", None), "strict": ("sol", None)}


@pytest.mark.parametrize("change,expected", [
    ({}, {"removal_only": "sol", "balanced": "skip", "strict": "skip"}),
    ({"scope_lenient": 0.49}, {"removal_only": "remove", "balanced": "sol", "strict": "sol"}),
    ({"scope_lenient": 0.5}, {"removal_only": "sol", "balanced": "sol", "strict": "sol"}),
    ({"scope_lenient": 0.29}, {"removal_only": "remove", "balanced": "remove", "strict": "remove"}),
    ({"scope_lenient": 0.3}, {"removal_only": "remove", "balanced": "sol", "strict": "sol"}),
    ({"scope_lenient": 0.8, "sup_neg": 0.2, "own_neg": 0.1, "val_entity": {1: 0.8}},
     {"removal_only": "sol", "balanced": "skip", "strict": "skip"}),
    ({"scope_lenient": 0.79}, {"removal_only": "sol", "balanced": "sol", "strict": "sol"}),
    ({"sup_neg": 0.21}, {"removal_only": "sol", "balanced": "sol", "strict": "sol"}),
    ({"own_neg": 0.11}, {"removal_only": "sol", "balanced": "sol", "strict": "sol"}),
    ({"val_entity": {1: 0.79}}, {"removal_only": "sol", "balanced": "sol", "strict": "sol"}),
    ({"choice3": {"accept": 0.49, "remove_out_of_scope": 0.0, "needs_review": 0.51}},
     {"removal_only": "sol", "balanced": "skip", "strict": "sol"}),
    ({"choice3": {"accept": 0.5, "remove_out_of_scope": 0.0, "needs_review": 0.5}},
     {"removal_only": "sol", "balanced": "skip", "strict": "skip"}),
])
def test_three_rules_at_their_thresholds(change, expected):
    record = _record(validators=("resolved",))
    answers = {**GOOD, "val_entity": {1: 1.0}, **change}
    assert {name: route for name, (route, _) in _routes(record, _orders(answers)).items()} == expected


def test_a_record_without_validator_answers_needs_none_to_skip():
    record = _record(validators=("failed",))
    assert {name: route for name, (route, _) in _routes(record, _orders(GOOD)).items()} == {
        "removal_only": "sol", "balanced": "skip", "strict": "skip"}


def test_claims_are_never_removed_and_sub_records_never_leave_the_supervisor():
    claim = _record(kind="claim")
    assert _routes(claim, _orders({**GOOD, "scope_lenient": 0.0}))["removal_only"] == ("sol", None)
    assert _routes(claim, _orders(GOOD))["balanced"] == ("skip", None)
    sub_record = _record(kind="sub_record")
    for answers in ({**GOOD, "scope_lenient": 0.0}, GOOD):
        assert set(_routes(sub_record, _orders(answers)).values()) == {("sol", "sub_record")}


def test_an_answer_missing_in_either_order_never_removes_or_skips():
    record = _record(validators=("resolved",))
    forward = {**GOOD, "scope_lenient": 0.0, "val_entity": {1: 1.0}}
    reverse = {key: value for key, value in forward.items() if key != "scope_lenient"}
    assert _routes(record, _orders(forward, reverse)) == {
        "removal_only": ("sol", "unanswered"), "balanced": ("sol", "unanswered"), "strict": ("sol", "unanswered")}
    reverse = {**GOOD, "val_entity": {}}
    assert _routes(record, _orders({**GOOD, "val_entity": {1: 1.0}}, reverse))["balanced"] == ("sol", "unanswered")


def test_only_the_platform_reference_roles_are_sub_records():
    packet = _packet(validators=False)
    packet["records"][0]["role"] = "curatable_unit"
    packet["records"][1]["role"] = "validated_reference"
    packet["records"][2]["type"] = "generic_claim"
    packet["records"][3]["role"] = "generic_proxy_object"
    packet["records"][4]["role"] = "metadata_only"
    packet["records"][5]["role"] = "supporting_reference"
    packet["records"].append({**copy.deepcopy(packet["records"][0]), "record": "R7", "role": "evidence_quote"})
    packet["records"].append({"record": None, "review": [{"review": "waiting", "codes": ["global"]}]})
    records = quick_check.packet_records(packet)
    assert [(record["record"], record["kind"]) for record in records] == [
        ("R1", "object"), ("R2", "sub_record"), ("R3", "claim"), ("R4", "object"), ("R5", "sub_record"),
        ("R6", "sub_record"), ("R7", "object")]


@pytest.mark.asyncio
async def test_check_asks_both_orders_at_once_and_logs_every_rule(decisions):
    both_sent = asyncio.Event()
    golden = _golden()

    async def respond(body):
        if len(decisions.requests) == 2:
            both_sent.set()
        await asyncio.wait_for(both_sent.wait(), timeout=5)
        overrides = {}
        if body["prompt_cache_key"].endswith(":forward"):
            overrides = {"scope_lenient__R4": 0.0, "scope_lenient__R6": 0.03, "own_neg__R5": 0.5}
        else:  # R6 is R1 here, and R4 is R3
            overrides = {"scope_lenient__R3": 0.01, "scope_lenient__R1": 0.0, "own_neg__R2": 0.5}
        return {"answers": _answers(body["questions"], overrides),
                "usage": {"input_tokens": 3000, "output_tokens": 0}}

    decisions.respond = respond
    packet = _packet()
    result = await _check(decisions, packet, cache_key=packet["packet_id"])

    for order in ("forward", "reversed"):
        [sent] = decisions.order(order)
        assert sent["url"] == "https://decisions.test/v1/decisions"
        assert sent["body"]["model"] == "gpt-6-luna"
        assert sent["body"]["prompt_cache_key"] == packet["packet_id"] + ":" + order
        assert sent["body"]["input"] == golden[order]["input"]
        assert sent["body"]["questions"] == golden[order]["questions"]
    assert result["status"] == "completed"
    assert [call["order"] for call in result["calls"]] == ["forward", "reversed"]
    assert all(call["usage"] == {"input": 3000, "cached_input": 0, "cache_write_input": 0, "output": 0}
               for call in result["calls"])
    assert result["usage"]["input"] == 6000
    assert result["routes"] == {"removal_only": {"skip": 0, "remove": 2, "sol": 4},
                                "balanced": {"skip": 3, "remove": 2, "sol": 1},
                                "strict": {"skip": 3, "remove": 2, "sol": 1}}
    rows = {row["record"]: row for row in result["records"]}
    assert rows["R4"]["routes"] == {"removal_only": "remove", "balanced": "remove", "strict": "remove"}
    assert rows["R5"]["routes"]["balanced"] == "sol" and rows["R5"]["average"]["own_neg"] == 0.5
    r1 = rows["R1"]
    assert r1["forward"]["val_entity"] == {"R1V2": 1.0} and r1["reversed"]["val_entity"] == {"R1V2": 1.0}
    assert r1["forward"]["choice3"] == {"accept": 0.9, "remove_out_of_scope": 0.0, "needs_review": 0.1}
    assert r1["average"] == {"scope_lenient": 1.0, "sup_neg": 1.0, "own_neg": 1.0, "val_entity": 1.0,
                             "choice3:accept": 0.9, "choice3:remove_out_of_scope": 0.0, "choice3:needs_review": 0.1}
    assert r1["routes"] == {"removal_only": "sol", "balanced": "skip", "strict": "skip"}
    assert rows["R6"]["forward"]["scope_lenient"] == 0.03 and rows["R6"]["reversed"]["scope_lenient"] == 0.0
    assert result["rules"]["strict"]["skip_at_least"]["choice3:accept"] == 0.5


@pytest.mark.asyncio
async def test_more_than_400_questions_are_split_within_each_order(decisions):
    packet = _packet(validators=False)
    template = packet["records"][0]
    packet["records"] = [{**copy.deepcopy(template), "record": f"R{index}", "target_id": f"t{index}"}
                         for index in range(1, 102)]
    result = await _check(decisions, packet, cache_key="packet-big")

    for order in ("forward", "reversed"):
        sent = decisions.order(order)
        assert [len(item["body"]["questions"]) for item in sent] == [400, 4]
        assert len({item["body"]["input"] for item in sent}) == 1
        names = [question["name"] for item in sent for question in item["body"]["questions"]]
        assert len(names) == len(set(names)) == 404
    assert len(result["calls"][0]["requests"]) == 2
    assert result["routes"]["balanced"] == {"skip": 101, "remove": 0, "sol": 0}


@pytest.mark.asyncio
async def test_refused_questions_leave_the_record_with_the_supervisor(decisions):
    def respond(body):
        answers = _answers(body["questions"])
        if body["prompt_cache_key"].endswith(":reversed"):
            answers = [{"type": "refusal", "name": answer["name"]} if answer["name"] == "sup_neg__R6" else answer
                       for answer in answers]
        return {"answers": answers, "usage": {"input_tokens": 10}}

    decisions.respond = respond
    result = await _check(decisions, _packet())
    assert result["status"] == "partial"
    assert result["refused"] == [{"order": "reversed", "record": "R1", "question": "sup_neg", "validator": None}]
    assert result["missing"] == []
    r1 = next(row for row in result["records"] if row["record"] == "R1")
    assert r1["routes"] == {"removal_only": "sol", "balanced": "sol", "strict": "sol"}
    assert r1["route_reasons"] == {"balanced": "unanswered", "strict": "unanswered"}
    assert "sup_neg" not in r1["reversed"] and "sup_neg" not in r1["average"]


@pytest.mark.asyncio
async def test_one_failed_order_fails_the_check_and_keeps_the_other_order(decisions):
    def respond(body):
        if body["prompt_cache_key"].endswith(":reversed"):
            return httpx.Response(429, json={"error": {"message": "slow down", "code": "rate_limit_exceeded"}})
        return {"answers": _answers(body["questions"]), "usage": {"input_tokens": 10}}

    decisions.respond = respond
    result = await _check(decisions, _packet())
    assert len(decisions.requests) == 2
    assert result["status"] == "failed"
    error = {"type": "RateLimitError", "status_code": 429, "code": "rate_limit_exceeded"}
    assert result["error"] == error
    forward, reverse = result["calls"]
    assert forward["order"] == "forward" and forward["usage"]["input"] == 10 and "error" not in forward
    assert reverse == {"order": "reversed", "questions": 25, "error": error}
    assert result["usage"]["input"] == 10
    r1 = result["records"][0]
    assert r1["forward"]["scope_lenient"] == 1.0 and r1["forward"]["val_entity"] == {"R1V2": 1.0}
    assert "reversed" not in r1 and "average" not in r1
    assert r1["routes"] == {"removal_only": "sol", "balanced": "sol", "strict": "sol"}
    assert r1["route_reasons"] == {"removal_only": "unanswered", "balanced": "unanswered", "strict": "unanswered"}
    assert result["routes"]["balanced"] == {"skip": 0, "remove": 0, "sol": 6}


@pytest.mark.asyncio
async def test_missing_answers_name_the_packet_record_and_question(decisions):
    def respond(body):
        answers = _answers(body["questions"])
        if body["prompt_cache_key"].endswith(":reversed"):  # packet R1 is R6 in this order
            answers = [answer for answer in answers if answer["name"] != "val_entity__R6V2"]
        return {"answers": answers, "usage": {"input_tokens": 10}}

    decisions.respond = respond
    result = await _check(decisions, _packet())
    assert result["status"] == "partial"
    assert result["missing"] == [{"order": "reversed", "record": "R1", "question": "val_entity", "validator": 2}]
    r1 = result["records"][0]
    assert r1["routes"] == {"removal_only": "sol", "balanced": "sol", "strict": "sol"}
    assert r1["route_reasons"] == {"balanced": "unanswered", "strict": "unanswered"}


@pytest.mark.asyncio
async def test_a_defect_in_one_order_is_reported_and_the_other_kept(decisions, captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    ask_order = quick_check._ask_order

    async def broken_reverse(client, call, cache_key):
        if call.order == "reversed":
            raise TypeError("defect")
        return await ask_order(client, call, cache_key)

    monkeypatch.setattr(quick_check, "_ask_order", broken_reverse)
    with quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), PAGE)
        await _drain()
    [event] = captured.events
    payload = event["output_summary"]
    assert payload["status"] == "failed" and payload["error"] == {"type": "TypeError"}
    assert [call.get("error") for call in payload["calls"]] == [None, {"type": "TypeError"}]
    [(exc, _)] = captured.reports
    assert str(exc) == "Validation review quick check failed (TypeError)"


@pytest.mark.asyncio
async def test_packet_without_records_makes_no_call(decisions, captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    packet = {"packet_id": "p", "evidence": {}, "records": [{"record": None, "review": []}]}
    assert quick_check.prepare_check(packet, request=QUESTION).calls == ()
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: packet)
    with quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), PAGE)
        await _drain()
    [event] = captured.events
    assert event["output_summary"]["status"] == "no_questions"
    assert decisions.requests == [] and decisions.clients == []


def test_record_without_its_saved_source_is_not_asked_and_stays_with_the_supervisor():
    packet = _packet(validators=False)
    packet["records"][2] = {"record": "R3", "target_id": "t3", "source_unavailable": "History conflict",
                            "review": [{"review": "record", "handle": "D3"}]}
    records = quick_check.packet_records(packet)
    asked = quick_check.asked_records(records)
    assert [record["record"] for record in asked] == ["R1", "R2", "R4", "R5", "R6"]
    assert [rid for rid, _ in quick_check.ordered(asked, "forward")] == ["R1", "R2", "R3", "R4", "R5"]
    assert quick_check.route(records[2], {}, RULES["balanced"]) == ("sol", "source_unavailable")


@pytest.mark.asyncio
async def test_shadow_check_logs_one_trace_event_per_fresh_packet(decisions, captured, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=quick_check.__name__)
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    loads = []
    monkeypatch.setattr(quick_check, "_load_packet",
                        lambda scope, episode, packet: loads.append((scope, episode, packet)) or _packet())
    metadata = {"execution_context": {"source_kind": "chat", "executed_query": "Extract transgenic fly stocks."}}
    with quick_check.review_scope(QUESTION, _source(metadata)):
        quick_check.packet_pinned(_context(), PAGE)
        quick_check.packet_pinned(_context(), PAGE)
        await _drain()
    assert loads == [(SCOPE, "episode-1", "packet-1")]
    assert len(decisions.requests) == 2
    [event] = captured.events
    assert event["event_type"] == "review_quick_check.shadow"
    payload = event["output_summary"]
    assert {key: payload[key] for key in ("packet_id", "episode_id", "wording_id", "model", "mode", "status",
                                          "request_source")} == {
        "packet_id": "packet-1", "episode_id": "episode-1", "wording_id": "v6-shadow", "model": "gpt-6-luna",
        "mode": "shadow", "status": "completed", "request_source": "curator_request"}
    assert all(call["latency_ms"] >= 0 and call["usage"]["input"] == 3000 for call in payload["calls"])
    assert decisions.requests[0]["body"]["input"].startswith(
        'A curator asked an AI curation assistant this request about one publication:\n"' + QUESTION + '"')
    assert captured.reports == []
    [line] = [record for record in caplog.records if getattr(record, "event", None) == "review_quick_check"]
    assert line.levelname == "INFO" and line.quick_check["routes"] == payload["routes"]
    assert set(line.quick_check["records"][0]) == {"record", "kind", "routes", "average"}
    assert payload["records"][0]["target_id"] == "cafa5538-3de0-48ba-a2c2-bdf1d8912198"


@pytest.mark.asyncio
async def test_mode_off_makes_no_call(decisions, captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "off")
    load = Mock()
    monkeypatch.setattr(quick_check, "_load_packet", load)
    with quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), PAGE)
        await _drain()
    assert not quick_check._RUNNING and decisions.requests == [] and captured.events == []
    load.assert_not_called()


@pytest.mark.asyncio
async def test_default_mode_is_off(decisions, captured, monkeypatch):
    monkeypatch.delenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", raising=False)
    with quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), PAGE)
        await _drain()
    assert decisions.requests == [] and captured.events == []


@pytest.mark.asyncio
async def test_review_without_a_bound_scope_is_not_checked(decisions, captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    quick_check.packet_pinned(_context(), PAGE)
    await _drain()
    assert decisions.requests == [] and captured.events == []


@pytest.mark.asyncio
async def test_provider_failure_is_logged_without_effect(decisions, captured, monkeypatch, caplog):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    decisions.respond = lambda body: httpx.Response(
        503, json={"error": {"message": "overloaded", "type": "server_error", "code": "server_overloaded"}})
    with quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), PAGE)
        await _drain()
    [event] = captured.events
    payload = event["output_summary"]
    assert payload["status"] == "failed"
    error = {"type": "InternalServerError", "status_code": 503, "code": "server_overloaded"}
    assert payload["error"] == error
    assert [call["error"] for call in payload["calls"]] == [error, error]
    assert payload["usage"] is None
    assert all(set(row) & {"forward", "reversed", "average"} == set() for row in payload["records"])
    assert payload["routes"]["balanced"] == {"skip": 0, "remove": 0, "sol": 6}
    assert captured.reports == []
    assert any(record.message == "Validation review quick check failed" for record in caplog.records)


@pytest.mark.asyncio
async def test_unexpected_failure_reports_without_content_and_without_effect(captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")

    def broken(*args):
        raise KeyError("curator note: fake sensitive transgenic stock text")

    monkeypatch.setattr(quick_check, "_load_packet", broken)
    with quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), PAGE)
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
        assert quick_check.packet_pinned(_context(), PAGE) is None
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
        monkeypatch.setattr(quick_check, "prepare_check", Mock(side_effect=TypeError("defect")))
    tool = _read_tool(_context())
    with quick_check.review_scope(QUESTION, _source()):
        first = await _invoke(tool, {})
        second = await _invoke(tool, {"packet_id": "packet-1", "page": 1})
        await _drain()
    assert first == page and second == page
    assert calls == [((SCOPE, "episode-1"), {"packet_id": None, "page": None}),
                     ((SCOPE, "episode-1"), {"packet_id": "packet-1", "page": 1})]
    [event] = captured.events
    assert event["output_summary"]["status"] == ("completed" if outcome == "completed" else "failed")
    assert len(decisions.requests) == (0 if outcome == "observer_defect" else 2)


@pytest.mark.asyncio
async def test_reviewer_packet_read_does_not_wait_for_the_check(decisions, captured, monkeypatch):
    from src.lib.validation_review import reads

    release = asyncio.Event()
    run_check = quick_check.run_check

    async def slow_check(prepared, client, *, cache_key):
        await release.wait()
        return await run_check(prepared, client, cache_key=cache_key)

    monkeypatch.setattr(reads, "review_packet", lambda *args, **kwargs: {"packet_id": "packet-1"})
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    monkeypatch.setattr(quick_check, "run_check", slow_check)
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
        quick_check.packet_pinned(_context(), PAGE)
        await _drain()
    [event] = captured.events
    assert event["output_summary"]["status"] == "failed"
    assert event["output_summary"]["error"] == {"type": "AttributeError"}
    assert decisions.requests == [] and len(captured.reports) == 1


@pytest.mark.asyncio
async def test_the_check_is_never_charged_to_the_review_budget(decisions, captured, monkeypatch):
    from src.lib.validation_review.provider import ACTIVE_BUDGET, ExecutionBudget, budget_scope

    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    seen = []
    run_check = quick_check.run_check

    async def check(prepared, client, *, cache_key):
        seen.append(ACTIVE_BUDGET.get())
        return await run_check(prepared, client, cache_key=cache_key)

    monkeypatch.setattr(quick_check, "run_check", check)
    budget = ExecutionBudget(SCOPE, "episode-1", "reviewer_tokens", True, reviewer_agent_id="reviewer")
    with budget_scope(budget), quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), PAGE)
        await _drain()
        assert ACTIVE_BUDGET.get() is budget
    assert seen == [None]


@pytest.mark.asyncio
async def test_a_page_without_a_packet_id_is_reported_and_never_raised(captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    with quick_check.review_scope(QUESTION, _source()):
        assert quick_check.packet_pinned(_context(), {}) is None
        await _drain()
    [(exc, _)] = captured.reports
    assert str(exc) == "Validation review quick check failed (KeyError)"
    assert captured.events == []


@pytest.mark.asyncio
async def test_cancelling_a_check_mid_call_closes_the_client_and_lets_nothing_escape(
        decisions, captured, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=quick_check.__name__)
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    never = asyncio.Event()

    async def hang(body):
        await never.wait()

    decisions.respond = hang
    loop_errors = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, context: loop_errors.append(context))
    with quick_check.review_scope(QUESTION, _source()):
        quick_check.packet_pinned(_context(), PAGE)
        await asyncio.wait_for(_until(lambda: len(decisions.requests) == 2), timeout=5)
        [task] = list(quick_check._RUNNING)
        task.cancel()
        results = await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert isinstance(results[0], asyncio.CancelledError)
    assert not quick_check._RUNNING
    assert [client.is_closed() for client in decisions.clients] == [True]
    assert captured.events == [] and captured.reports == [] and loop_errors == []
    [line] = [record for record in caplog.records if getattr(record, "event", None) == "review_quick_check"]
    assert line.quick_check == {"packet_id": "packet-1", "wording_id": "v6-shadow", "status": "cancelled"}


async def _until(condition):
    while not condition():
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_concurrent_reviews_each_check_their_own_packets(decisions, captured, monkeypatch):
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    both_bound = asyncio.Barrier(2)

    async def review(episode):
        with quick_check.review_scope(QUESTION, _source()):
            await both_bound.wait()
            context = ReviewContext(SCOPE, episode, "reviewer")
            quick_check.packet_pinned(context, PAGE)
            quick_check.packet_pinned(context, PAGE)
            await _drain()

    await asyncio.gather(review("episode-1"), review("episode-2"))
    assert sorted(event["output_summary"]["episode_id"] for event in captured.events) == ["episode-1", "episode-2"]
    assert len(decisions.requests) == 4


@pytest.mark.asyncio
async def test_chat_review_binds_the_scope_only_while_the_reviewer_runs(monkeypatch):
    from src.lib.openai_agents import extraction_trace_events
    from src.lib.validation_review import chat
    from src.lib.validation_review.chat import ChatReview

    review = ChatReview("owner", "document", "session", "turn", None, (), QUESTION)
    record = SimpleNamespace(extraction_result_id="source", metadata={"execution_context": {"source_kind": "chat"}})
    context = SimpleNamespace(episode_id="episode", history_scope=None, actor_id="supervisor")
    monkeypatch.setattr(review, "context", lambda _: context)
    monkeypatch.setattr(review, "saved_review_outcome", lambda _: None)
    monkeypatch.setattr(review, "reference", lambda _: dict(artifact_id="artifact", episode_id="episode",
                                                             plan_ref="plan"))
    adapter = SimpleNamespace(initial=AsyncMock(), advance=AsyncMock(), execute=None, prepare_inputs=None,
                              refresh_inputs=None)
    monkeypatch.setattr(chat, "ValidatorAdapter", lambda *a, **kw: adapter)
    monkeypatch.setattr(review, "extractor_adapter",
                        lambda *a: SimpleNamespace(request=None, receipt={"agent_key": "saved", "contract": None}))
    monkeypatch.setattr(chat.reviewer, "create_supervisor_phase", lambda *a, **kw: SimpleNamespace(tools=[]))
    monkeypatch.setattr(extraction_trace_events, "write_extraction_trace_event", lambda **kw: None)
    during = []

    async def run(*args, **kwargs):
        during.append(quick_check._SCOPE.get())
        return {"review": {"ready": True}}

    monkeypatch.setattr(chat.reviewer, "run", run)
    assert quick_check._SCOPE.get() is None
    assert await review.review_result(record) == {"review": {"ready": True}}
    [scope] = during
    assert scope.curator_request == QUESTION and scope.record is record and scope.checked == set()
    assert quick_check._SCOPE.get() is None


@pytest.mark.asyncio
async def test_logged_event_and_line_carry_no_quotes_values_or_request_text(decisions, captured, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=quick_check.__name__)
    monkeypatch.setenv("VALIDATION_REVIEW_QUICK_CHECK_MODE", "shadow")
    monkeypatch.setattr(quick_check, "_load_packet", lambda *args: _packet())
    delegation = "List the transgenic fly stocks used in the paper."
    metadata = {"execution_context": {"source_kind": "flow", "executed_query": delegation}}
    with quick_check.review_scope(QUESTION, _source(metadata)):
        quick_check.packet_pinned(_context(), PAGE)
        await _drain()
    [event] = captured.events
    [line] = [record for record in caplog.records if getattr(record, "event", None) == "review_quick_check"]
    assert event["output_summary"]["request_source"] == "delegation"
    assert delegation in decisions.requests[0]["body"]["input"]
    logged = json.dumps(event, default=str) + json.dumps(line.quick_check, default=str)
    packet = _packet()
    quotes = [item["verified_quote"] for item in packet["evidence"].values() if "verified_quote" in item]
    values = [record["values"]["label"] for record in packet["records"]] + [
        record["values"]["source_label"] for record in packet["records"]]
    for text in [QUESTION, delegation, "Exact symbol match.", "FB:FBgn0259685", *quotes, *values]:
        assert text not in logged, text
