import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.lib.benchmarks.adjudication import (
    ADJUDICATION_PROMPT_ID,
    AdjudicationDecision,
    RawAdjudicationResponse,
    SupplementalAdjudicator,
    execute_direct_openai_adjudication,
)
from src.lib.benchmarks.models import BenchmarkScorerReference, BilledCost
from src.lib.benchmarks.scoring import score_case


def _score(*, ambiguous=True, malformed=False, provider_failure=False):
    expected = {"value": [] if malformed else "expected"}
    actual = {"value": "wrong" if malformed else "actual"}
    return score_case(
        scorer=BenchmarkScorerReference(
            id="deterministic-v1",
            configuration={
                "scoring_version": 1,
                "fields": [
                    {
                        "path": "/value",
                        "comparison": "ordered_collection" if malformed else "exact",
                        "ambiguous": ambiguous,
                    }
                ],
            },
        ),
        expected=expected,
        actual=actual,
        provider_failure=provider_failure,
    )


def _adjudicator(executor, **overrides):
    values = {
        "enabled": True,
        "model": "gpt-5.6-sol",
        "timeout_seconds": 0.1,
        "retries": 0,
        "turn_limit": 1,
        "tool_call_limit": 0,
        "result_max_bytes": 10000,
    }
    values.update(overrides)
    return SupplementalAdjudicator(executor=executor, **values)


def _response(outcome="supports_expected", *, billed_amount=None):
    return RawAdjudicationResponse(
        decision=AdjudicationDecision(
            outcome=outcome,
            reason="semantic interpretation is supported",
            confidence=Decimal("0.8"),
            uncertainty="minor wording ambiguity",
        ),
        input_tokens=100,
        output_tokens=20,
        billed_cost=(
            BilledCost(
                amount=Decimal(billed_amount),
                unit="USD",
                source="provider_usage",
            )
            if billed_amount is not None
            else None
        ),
    )


def _direct_openai_client(monkeypatch, *, response=None, request_error=None):
    parse = AsyncMock(return_value=response, side_effect=request_error)
    close = AsyncMock()
    client = SimpleNamespace(
        responses=SimpleNamespace(parse=parse),
        close=close,
    )
    constructor_calls = []

    def build_client(**kwargs):
        constructor_calls.append(kwargs)
        return client

    monkeypatch.setattr("openai.AsyncOpenAI", build_client)
    return client, constructor_calls


async def test_direct_openai_adjudication_closes_client_after_success(monkeypatch):
    decision = AdjudicationDecision(
        outcome="supports_expected",
        reason="expected value is supported",
        confidence=Decimal("0.9"),
        uncertainty="",
    )
    response = SimpleNamespace(
        output_parsed=decision,
        usage=SimpleNamespace(input_tokens=12, output_tokens=4),
    )
    client, constructor_calls = _direct_openai_client(
        monkeypatch,
        response=response,
    )

    result = await execute_direct_openai_adjudication(
        "gpt-5.6-sol", "adjudicate this", 10_000
    )

    assert constructor_calls == [{"max_retries": 0}]
    client.responses.parse.assert_awaited_once_with(
        model="gpt-5.6-sol",
        input="adjudicate this",
        text_format=AdjudicationDecision,
    )
    client.close.assert_awaited_once_with()
    assert result.decision == decision
    assert result.input_tokens == 12
    assert result.output_tokens == 4


async def test_direct_openai_adjudication_closes_client_after_request_failure(
    monkeypatch,
):
    request_error = RuntimeError("request failed")
    client, _constructor_calls = _direct_openai_client(
        monkeypatch,
        request_error=request_error,
    )

    with pytest.raises(RuntimeError) as exc_info:
        await execute_direct_openai_adjudication(
            "gpt-5.6-sol", "adjudicate this", 10_000
        )

    assert exc_info.value is request_error
    client.close.assert_awaited_once_with()


async def test_direct_openai_adjudication_preserves_request_failure_when_close_fails(
    monkeypatch,
):
    request_error = RuntimeError("request failed")
    client, _constructor_calls = _direct_openai_client(
        monkeypatch,
        request_error=request_error,
    )
    client.close.side_effect = RuntimeError("close failed")

    with pytest.raises(RuntimeError) as exc_info:
        await execute_direct_openai_adjudication(
            "gpt-5.6-sol", "adjudicate this", 10_000
        )

    assert exc_info.value is request_error
    client.close.assert_awaited_once_with()


async def test_completed_adjudication_preserves_structured_provenance():
    calls = []

    async def executor(model, prompt, max_bytes):
        calls.append((model, prompt, max_bytes))
        return _response()

    result = await _adjudicator(executor).adjudicate(
        expected={"value": "expected"}, actual={"value": "actual"}, score=_score()
    )

    assert result.status == "completed"
    assert result.outcome == "supports_expected"
    assert result.confidence == Decimal("0.8")
    assert result.prompt_id == ADJUDICATION_PROMPT_ID
    assert result.model == "gpt-5.6-sol"
    assert result.input_tokens == 100
    assert result.output_tokens == 20
    assert len(result.attempts) == 1
    assert result.attempts[0].status == "completed"
    assert result.attempts[0].turn == 1
    assert result.attempts[0].attempt == 1
    assert calls[0][0] == "gpt-5.6-sol"


@pytest.mark.parametrize(
    ("score", "expected_category"),
    [
        (_score(ambiguous=False), "ineligible"),
        (_score(malformed=True), "ineligible"),
        (_score(provider_failure=True), "ineligible"),
    ],
)
async def test_hard_deterministic_failures_cannot_be_upgraded(score, expected_category):
    async def forbidden(*_args):
        raise AssertionError("hard failure reached paid adjudicator")

    result = await _adjudicator(forbidden).adjudicate(
        expected={"value": "expected"}, actual={"value": "actual"}, score=score
    )

    assert result.status == "not_requested"
    assert result.outcome is None
    assert result.failure is not None
    assert result.failure.category == expected_category


async def test_missing_required_evidence_cannot_reach_adjudicator():
    score = score_case(
        scorer=BenchmarkScorerReference(
            id="deterministic-v1",
            configuration={
                "scoring_version": 1,
                "fields": [
                    {
                        "comparison": "evidence",
                        "evidence_paths": ["/evidence/quote"],
                        "ambiguous": True,
                    }
                ],
            },
        ),
        expected={"evidence": {"quote": "expected"}},
        actual={"evidence": {}},
    )

    async def forbidden(*_args):
        raise AssertionError("missing evidence reached paid adjudicator")

    result = await _adjudicator(forbidden).adjudicate(
        expected={}, actual={}, score=score
    )

    assert result.status == "not_requested"
    assert result.failure is not None
    assert result.failure.category == "ineligible"


async def test_disabled_adjudicator_is_unavailable_by_default():
    async def forbidden(*_args):
        raise AssertionError("disabled adjudicator was called")

    result = await _adjudicator(forbidden, enabled=False).adjudicate(
        expected={}, actual={}, score=_score()
    )
    assert result.status == "not_requested"
    assert result.failure is not None
    assert result.failure.category == "disabled"


async def test_invalid_result_retries_and_records_attempts():
    calls = 0

    async def executor(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("invalid structured result")
        return _response("uncertain", billed_amount="0.003")

    result = await _adjudicator(executor, retries=1).adjudicate(
        expected={}, actual={}, score=_score()
    )
    assert result.status == "completed"
    assert result.outcome == "uncertain"
    assert calls == 2
    assert [attempt.status for attempt in result.attempts] == [
        "invalid_result",
        "completed",
    ]
    assert [attempt.retry for attempt in result.attempts] == [0, 1]
    assert result.input_tokens == 100
    assert result.billed_cost == BilledCost(
        amount=Decimal("0.003"), unit="USD", source="provider_usage"
    )


async def test_oversized_structured_result_is_rejected():
    async def executor(*_args):
        return _response()

    result = await _adjudicator(executor, result_max_bytes=10).adjudicate(
        expected={}, actual={}, score=_score()
    )
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "invalid_result"


async def test_timeout_and_refusal_are_inspectable_failures(monkeypatch):
    reported = []
    monkeypatch.setattr(
        "src.lib.benchmarks.adjudication.report_runtime_exception",
        lambda *args, **kwargs: reported.append((args, kwargs)),
    )

    async def timeout_executor(*_args):
        await asyncio.sleep(0.02)
        return _response()

    timeout = await _adjudicator(timeout_executor, timeout_seconds=0.001).adjudicate(
        expected={}, actual={}, score=_score()
    )
    assert timeout.failure is not None
    assert timeout.failure.category == "timeout"

    async def refusal_executor(*_args):
        raise PermissionError("refused")

    refusal = await _adjudicator(refusal_executor).adjudicate(
        expected={}, actual={}, score=_score()
    )
    assert refusal.failure is not None
    assert refusal.failure.category == "refusal"
    assert reported == []


async def test_conflicting_turns_are_non_reproducible():
    outcomes = iter(["supports_expected", "supports_actual"])

    async def executor(*_args):
        return _response(next(outcomes), billed_amount="0.002")

    result = await _adjudicator(executor, turn_limit=2).adjudicate(
        expected={}, actual={}, score=_score()
    )
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "non_reproducible"
    assert [attempt.outcome for attempt in result.attempts] == [
        "supports_expected",
        "supports_actual",
    ]
    assert result.input_tokens == 200
    assert result.output_tokens == 40
    assert result.billed_cost == BilledCost(
        amount=Decimal("0.004"), unit="USD", source="provider_usage"
    )


async def test_agreeing_turns_preserve_each_decision_and_total_cost():
    async def executor(*_args):
        return _response("supports_expected", billed_amount="0.0015")

    result = await _adjudicator(executor, turn_limit=2).adjudicate(
        expected={}, actual={}, score=_score()
    )

    assert result.status == "completed"
    assert [attempt.turn for attempt in result.attempts] == [1, 2]
    assert [attempt.outcome for attempt in result.attempts] == [
        "supports_expected",
        "supports_expected",
    ]
    assert result.input_tokens == 200
    assert result.billed_cost == BilledCost(
        amount=Decimal("0.0030"), unit="USD", source="provider_usage"
    )


async def test_failure_after_paid_turn_preserves_usage_and_attempts(monkeypatch):
    calls = 0
    reported = []

    async def executor(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _response("supports_expected", billed_amount="0.004")
        raise OSError("Authorization: Bearer secret-provider-value")

    monkeypatch.setattr(
        "src.lib.benchmarks.adjudication.report_runtime_exception",
        lambda exc, **kwargs: reported.append((exc, kwargs)),
    )
    result = await _adjudicator(executor, turn_limit=2).adjudicate(
        expected={}, actual={}, score=_score()
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "provider_error"
    assert [attempt.status for attempt in result.attempts] == [
        "completed",
        "provider_error",
    ]
    assert result.input_tokens == 100
    assert result.output_tokens == 20
    assert result.billed_cost == BilledCost(
        amount=Decimal("0.004"), unit="USD", source="provider_usage"
    )
    assert len(reported) == 1
    reported_exception, metadata = reported[0]
    serialized = json.dumps(
        {"exception": str(reported_exception), "metadata": metadata}, sort_keys=True
    )
    assert "secret-provider-value" not in serialized
    assert "Authorization" not in serialized
    assert reported_exception.__traceback__ is not None
    assert reported_exception.__cause__ is None
    assert reported_exception.__context__ is None
    assert metadata["component"] == "benchmark_adjudication"
    assert metadata["operation"] == "provider_call_failed"
    assert metadata["context"] == {
        "attempt": 2,
        "model": "gpt-5.6-sol",
        "retry": 0,
        "turn": 2,
    }


async def test_mixed_ambiguous_and_hard_mismatches_close_gate():
    score = score_case(
        scorer=BenchmarkScorerReference(
            id="deterministic-v1",
            configuration={
                "scoring_version": 1,
                "fields": [
                    {"path": "/semantic", "comparison": "exact", "ambiguous": True},
                    {"path": "/required", "comparison": "exact"},
                ],
            },
        ),
        expected={"semantic": "expected", "required": True},
        actual={"semantic": "actual"},
    )

    async def forbidden(*_args):
        raise AssertionError("mixed hard failure reached paid adjudicator")

    result = await _adjudicator(forbidden).adjudicate(
        expected={}, actual={}, score=score
    )
    assert result.status == "not_requested"
    assert result.failure is not None
    assert result.failure.category == "ineligible"


def test_tool_calls_are_forbidden():
    async def executor(*_args):
        return _response()

    with pytest.raises(ValueError, match="does not permit tools"):
        _adjudicator(executor, tool_call_limit=1)


async def test_configured_model_reaches_executor_and_provenance():
    calls = []

    async def executor(model, *_args):
        calls.append(model)
        return _response()

    result = await _adjudicator(
        executor, model="deployment-adjudicator-v2"
    ).adjudicate(expected={}, actual={}, score=_score())

    assert calls == ["deployment-adjudicator-v2"]
    assert result.model == "deployment-adjudicator-v2"
