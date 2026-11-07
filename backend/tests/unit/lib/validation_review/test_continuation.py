from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from src.lib.validation_review import continuation, reviewer
from src.lib.validation_review.decisions import ReviewContext
from src.lib.validation_history.store import HistoryScope
from src.lib.validation_review.provider import ACTIVE_BUDGET


def unit(**changes):
    return {"mode": "validation", "record": "record-a", "has_subject": True, "has_current_absence": False,
            "settled_by": None, "curator_decided": False, "can_repair": False, "repair_blocker": "required_inputs_unavailable",
            "work_states": [], **changes}


@pytest.mark.parametrize("code", ["unreviewed", "episode_review_pending", "answer_review_pending"])
def test_existing_subjects_are_assessable_without_new_attempt_allowance(code):
    eligible, causes = continuation.classify([{"target_id": "a", "code": code}], {"a": unit()})
    assert eligible == {"a"} and not causes


@pytest.mark.parametrize("code", ["retired", "historical_review_unknown", "configured_validator_unavailable",
                                  "required_validator_unavailable", "execution_pending"])
def test_structural_blockers_preserve_independent_work(code):
    eligible, causes = continuation.classify(
        [{"target_id": "a", "code": code}, {"target_id": "b", "code": "unreviewed"}],
        {"a": unit(), "b": unit()})
    assert eligible == {"b"}
    assert causes[0]["code"] == code


@pytest.mark.parametrize("code", ["stale", "incomplete", "unreviewed"])
def test_missing_subject_requires_an_authorized_repair_path(code):
    blockers = [{"target_id": "a", "code": code}]
    assert continuation.classify(blockers, {"a": unit(has_subject=False)})[0] == set()
    assert continuation.classify(blockers, {"a": unit(has_subject=False, can_repair=True)})[0] == {"a"}


def test_curator_unknown_and_document_lifecycle_do_not_trigger_nudges():
    eligible, causes = continuation.classify([{"target_id": "a", "code": "unreviewed"}],
                                           {"a": unit(mode="curator")})
    assert not eligible and causes[0]["code"] == "curator_authority_required"
    for code in ("document_retired", "future_unrecognized_state"):
        eligible, causes = continuation.classify(
            [{"target_id": None, "code": code}, {"target_id": "a", "code": "unreviewed"}], {"a": unit()})
        assert not eligible
        assert causes[0]["code"] in {"document_retired", "unsupported_review_state"}


@pytest.mark.parametrize("decision", ["reject", "defer", "acknowledge_failure"])
@pytest.mark.parametrize("code", ["unreviewed", "episode_review_pending"])
def test_a_decision_left_pending_with_nothing_new_and_no_recheck_does_not_rerun(code, decision):
    # Production trace 58b3683e: crb p13A was rejected after its one follow-up;
    # two more reviewer runs could not change it.
    eligible, causes = continuation.classify(
        [{"target_id": "a", "code": code}, {"target_id": "b", "code": "unreviewed"}],
        {"a": unit(mode="extraction_only", settled_by=decision), "b": unit(mode="extraction_only")})
    assert eligible == {"b"}
    assert causes == [{"target_id": "a", "code": "reviewer_left_pending", "decision": decision}]


def test_a_rejected_record_with_a_recheck_left_stays_eligible():
    blockers = [{"target_id": "a", "code": "unreviewed"}]
    units = {"a": unit(mode="extraction_only", settled_by="reject", can_repair=True)}
    assert continuation.classify(blockers, units) == ({"a"}, [])


def test_new_input_after_a_decision_or_an_unread_answer_keeps_the_record_eligible():
    # settled_by is None once a newer answer, request or contribution exists.
    blockers = [{"target_id": "a", "code": "unreviewed"}]
    assert continuation.classify(blockers, {"a": unit(mode="extraction_only")}) == ({"a"}, [])
    # An answer this episode has not reviewed is always new input.
    answered = [{"target_id": "a", "code": "answer_review_pending"}, {"target_id": "a", "code": "unreviewed"}]
    assert continuation.classify(answered, {"a": unit(settled_by="reject")}) == ({"a"}, [])


@pytest.mark.parametrize("changes", [{"mode": "curator"}, {"curator_decided": True, "can_repair": True},
                                     {"curator_decided": True}])
def test_curator_authority_stops_reviewer_runs(changes):
    blockers = [{"target_id": "a", "code": "unreviewed"}, {"target_id": "a", "code": "answer_review_pending"}]
    eligible, causes = continuation.classify(blockers, {"a": unit(**changes)})
    assert not eligible and causes == [{"target_id": "a", "code": "curator_authority_required"}]


def test_pending_records_name_each_record_once_with_its_plain_reason():
    blockers = [{"target_id": "a", "code": "unreviewed"}, {"target_id": "a2", "code": "unreviewed"},
                {"target_id": "b", "code": "unreviewed"}, {"target_id": "c", "code": "unreviewed"},
                {"target_id": "d", "code": "configured_validator_unavailable"},
                {"target_id": "e", "code": "unreviewed"}, {"target_id": "f", "code": "unreviewed"},
                {"target_id": None, "code": "execution_uncertain"}]
    units = {"a": unit(record="ra", settled_by="reject"), "a2": unit(record="ra"),
             "b": unit(record="rb", settled_by="reject", can_repair=True),
             "c": unit(record="rc", mode="curator"), "d": unit(record="rd"),
             "e": unit(record="re", settled_by="acknowledge_failure"),
             # A curator's pending decision holds the record; the reviewer cannot change it.
             "f": unit(record="rf", curator_decided=True, can_repair=True)}
    eligible, causes = continuation.classify(blockers[:-1], units)
    assert not {"e", "f"} & eligible
    assert {"target_id": "f", "code": "curator_authority_required"} in causes
    assert continuation.pending_records(blockers, units, eligible, causes) == [
        {"record_target_id": "ra", "why": "not_accepted", "recheck_left": False},
        {"record_target_id": "rb", "why": "not_accepted", "recheck_left": True},
        {"record_target_id": "rc", "why": "curator_decision", "recheck_left": False},
        {"record_target_id": "rd", "why": "cannot_check", "recheck_left": False},
        {"record_target_id": "re", "why": "cannot_check", "recheck_left": False},
        {"record_target_id": "rf", "why": "curator_decision", "recheck_left": False},
    ]


def test_curator_message_says_how_many_records_which_and_why():
    pending = [{"record_target_id": "ra", "why": "not_accepted", "recheck_left": False, "label": "crb p13A"}]
    assert continuation.pending_message(pending) == (
        "Review finished with 1 record still pending: crb p13A (the reviewer did not accept it, "
        "and no re-check is left). Saved results and history are retained.")
    pending += [{"record_target_id": "rc", "why": "curator_decision", "recheck_left": False, "label": "white"},
                {"record_target_id": "rb", "why": "not_decided", "recheck_left": True, "label": "Oregon R"}]
    message = continuation.pending_message(pending)
    assert message.startswith("Review finished with 3 records still pending: crb p13A (")
    assert "white (it needs a curator's decision)" in message
    assert "Oregon R (the reviewer did not decide it)" in message
    assert "_" not in message and "exhausted" not in message


def test_terminal_reason_is_the_current_state_not_an_earlier_refusal():
    settled = view(eligible=False, causes=[{"target_id": "a", "code": "reviewer_left_pending", "decision": "reject"}],
                   pending=[{"record_target_id": "ra", "why": "not_accepted", "recheck_left": False}])
    refusal = {"tool": "request_targeted_extraction", "target_id": "ra",
               "code": "extraction_attempts_exhausted", "resource": "ra"}
    outcome = continuation.terminal(settled, [refusal], "notes")
    assert outcome["reason"] == "reviewer_left_pending"
    assert outcome["causes"] == [*settled["causes"], refusal]
    assert outcome["pending"] == settled["pending"] and outcome["output"] == "notes"
    from src.lib.validation_review.flow_outcome import status_for_reason
    assert status_for_reason(outcome["reason"]) == "review_pending"


def test_a_stall_is_reported_even_when_another_unit_names_the_reason(monkeypatch):
    reports = []
    monkeypatch.setattr("src.lib.validation_review.audit.report_operational_failure", reports.append)
    blocked = view(causes=[{"target_id": "b", "code": "configured_validator_unavailable"}])
    outcome = continuation.terminal(blocked, [], None, stalled=True)
    assert outcome["reason"] == "configured_validator_unavailable"
    assert len(reports) == 1 and isinstance(reports[0], continuation.ReviewerStalled)
    continuation.terminal(blocked, [], None)
    assert len(reports) == 1


def view(signature="same", ready=False, eligible=True, causes=(), pending=()):
    return {"signature": signature, "review": {"ready": ready, "state": "complete" if ready else "pending"},
            "eligible": {"private-target"} if eligible else set(), "causes": list(causes),
            "pending": list(pending)}


@pytest.fixture
def runtime(monkeypatch):
    context = ReviewContext(HistoryScope("owner", "00000000-0000-0000-0000-000000000001", "session"),
                            "episode", "supervisor")
    history = object()
    surface = SimpleNamespace(source_tools=[SimpleNamespace(name="record_validation_review")], run_history=history)
    agent = SimpleNamespace(tools=surface.source_tools, tool_surface=surface)
    setup = Mock()
    commit = Mock()
    flush = Mock()
    monkeypatch.setattr(reviewer, "_require_review_tools", Mock())
    monkeypatch.setattr("src.lib.openai_agents.tool_surface.apply_tool_surface", setup)
    monkeypatch.setattr("src.lib.prompts.context.commit_pending_prompts", commit)
    monkeypatch.setattr(reviewer, "flush_phase_config", flush)
    monkeypatch.setattr("src.lib.observability.cost_context.get_agent_cost_identity", lambda a: {"agent_id": "supervisor"})
    monkeypatch.setattr("src.lib.validation_history.store.SessionLocal", MagicMock())
    turns = Mock(return_value=None)
    monkeypatch.setattr(reviewer.admission, "remaining_reviewer_turns", turns)
    deadline = Mock(return_value=None)
    monkeypatch.setattr("src.lib.validation_review.provider.remaining_seconds", deadline)
    reports = Mock()
    monkeypatch.setattr("src.lib.validation_review.audit.report_operational_failure", reports)
    calls = []

    async def run(agent, incoming, **kwargs):
        calls.append((agent, incoming, kwargs, ACTIVE_BUDGET.get()))
        saved = [*incoming] if isinstance(incoming, list) else [{"role": "user", "content": incoming}]
        saved.append({"role": "assistant", "content": f"ordinary final {len(calls)}"})
        return SimpleNamespace(final_output=saved[-1]["content"], to_input_list=lambda: saved)

    monkeypatch.setattr("src.lib.openai_agents.runner.run_agent_with_owned_openai_resources", run)
    return SimpleNamespace(context=context, agent=agent, calls=calls, setup=setup, commit=commit,
                           flush=flush, turns=turns, deadline=deadline, reports=reports)


@pytest.mark.asyncio
async def test_productive_continuation_keeps_budget_history_and_setup_once(runtime, monkeypatch):
    r = runtime
    monkeypatch.setenv("VALIDATION_REVIEW_INITIAL_CORRECTIVE_CONTINUATIONS", "0")
    monkeypatch.setattr(continuation, "inspect", Mock(side_effect=[view("initial"), view("new-answer"), view("done", ready=True)]))
    r.turns.side_effect = [10, 8]
    result = await reviewer.run(r.context, r.agent, input="original request")
    assert result["review"]["ready"] and len(r.calls) == 2
    assert r.calls[0][3] is r.calls[1][3]
    assert r.calls[0][2]["run_config"] is r.calls[1][2]["run_config"]
    assert [c[2]["max_turns"] for c in r.calls] == [10, 8]
    assert r.calls[1][1] == [{"role": "user", "content": "original request"},
                            {"role": "assistant", "content": "ordinary final 1"},
                            {"role": "user", "content": continuation.REMINDER}]
    assert "private-target" not in continuation.REMINDER and "episode" not in continuation.REMINDER
    r.setup.assert_called_once()
    r.commit.assert_called_once()
    r.flush.assert_called_once()
    r.deadline.assert_called_once()
    r.reports.assert_not_called()
    assert ACTIVE_BUDGET.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("allowance,phases", [(0, 1), (1, 2), (2, 3)])
async def test_no_progress_stops_once_after_configured_corrective_nudges(runtime, monkeypatch, allowance, phases):
    r = runtime
    monkeypatch.setenv("VALIDATION_REVIEW_INITIAL_CORRECTIVE_CONTINUATIONS", str(allowance))
    monkeypatch.setattr(continuation, "inspect", Mock(side_effect=lambda *a: view()))
    result = await reviewer.run(r.context, r.agent, input="review")
    assert result["reason"] == "reviewer_stalled" and not result["review"]["ready"]
    assert result["output"] == f"ordinary final {phases}"
    assert len(r.calls) == phases
    r.reports.assert_called_once()
    assert isinstance(r.reports.call_args.args[0], continuation.ReviewerStalled)


@pytest.mark.asyncio
async def test_structural_cause_is_not_replaced_by_stall(runtime, monkeypatch):
    r = runtime
    blocked = view(eligible=False, causes=[{"code": "configured_validator_unavailable", "target_id": "private-target"}])
    monkeypatch.setattr(continuation, "inspect", Mock(side_effect=lambda *a: blocked))
    result = await reviewer.run(r.context, r.agent, input="review")
    assert result["reason"] == "configured_validator_unavailable"
    assert result["causes"] == blocked["causes"] and len(r.calls) == 1
    r.reports.assert_not_called()


@pytest.mark.asyncio
async def test_new_exact_read_page_earns_continuation_but_repeat_does_not(runtime, monkeypatch):
    from src.lib.validation_review.observations import record_read
    from src.lib.openai_agents import runner
    r = runtime
    monkeypatch.setenv("VALIDATION_REVIEW_INITIAL_CORRECTIVE_CONTINUATIONS", "0")
    monkeypatch.setattr(continuation, "inspect", Mock(side_effect=lambda *a: view()))
    original = runner.run_agent_with_owned_openai_resources

    async def reading(*args, **kwargs):
        record_read("completion", {"sha256": "same-answer", "offset": 0, "content": "exact answer"})
        return await original(*args, **kwargs)

    monkeypatch.setattr(runner, "run_agent_with_owned_openai_resources", reading)
    result = await reviewer.run(r.context, r.agent, input="review")
    assert len(r.calls) == 2 and result["reason"] == "reviewer_stalled"


@pytest.mark.asyncio
async def test_persistent_typed_tool_refusal_survives_ordinary_final(runtime, monkeypatch):
    from src.lib.validation_review.observations import record_tool
    from src.lib.openai_agents import runner
    r = runtime
    monkeypatch.setenv("VALIDATION_REVIEW_INITIAL_CORRECTIVE_CONTINUATIONS", "0")
    monkeypatch.setattr(continuation, "inspect", Mock(side_effect=lambda *a: view()))
    original = runner.run_agent_with_owned_openai_resources

    async def refused(*args, **kwargs):
        record_tool("request_targeted_extraction", {"entry_ids": ["source-entry"]},
                    {"state": "not_applied", "code": "saved_extractor_contract_unavailable"})
        return await original(*args, **kwargs)

    monkeypatch.setattr(runner, "run_agent_with_owned_openai_resources", refused)
    result = await reviewer.run(r.context, r.agent, input="review")
    assert result["reason"] == "saved_extractor_contract_unavailable"
    assert not result["review"]["ready"] and result["causes"][0]["target_id"] == "source-entry"
    # The refusal names the reason, but the run still stalled and is reported once.
    r.reports.assert_called_once()
    assert isinstance(r.reports.call_args.args[0], continuation.ReviewerStalled)


def test_verified_current_absence_can_replace_stale_selection_without_a_new_attempt():
    blockers = [{"target_id": "a", "code": "stale"}]
    assert continuation.classify(blockers, {"a": unit(has_current_absence=True)}) == ({"a"}, [])
    assert continuation.classify(blockers, {"a": unit()})[0] == set()
