import asyncio
from decimal import Decimal

import pytest

from src.lib.benchmarks.adjudication import (
    ADJUDICATION_MODEL,
    ADJUDICATION_PROMPT_ID,
    AdjudicationDecision,
    RawAdjudicationResponse,
    SupplementalAdjudicator,
)
from src.lib.benchmarks.models import BenchmarkScorerReference
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
        "timeout_seconds": 0.1,
        "retries": 0,
        "turn_limit": 1,
        "tool_call_limit": 0,
        "result_max_bytes": 10000,
    }
    values.update(overrides)
    return SupplementalAdjudicator(executor=executor, **values)


def _response(outcome="supports_expected"):
    return RawAdjudicationResponse(
        decision=AdjudicationDecision(
            outcome=outcome,
            reason="semantic interpretation is supported",
            confidence=Decimal("0.8"),
            uncertainty="minor wording ambiguity",
        ),
        input_tokens=100,
        output_tokens=20,
    )


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
    assert result.model == ADJUDICATION_MODEL
    assert result.input_tokens == 100
    assert result.output_tokens == 20
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
        return _response("uncertain")

    result = await _adjudicator(executor, retries=1).adjudicate(
        expected={}, actual={}, score=_score()
    )
    assert result.status == "completed"
    assert result.outcome == "uncertain"
    assert calls == 2


async def test_oversized_structured_result_is_rejected():
    async def executor(*_args):
        return _response()

    result = await _adjudicator(executor, result_max_bytes=10).adjudicate(
        expected={}, actual={}, score=_score()
    )
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "invalid_result"


async def test_timeout_and_refusal_are_inspectable_failures():
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


async def test_conflicting_turns_are_non_reproducible():
    outcomes = iter(["supports_expected", "supports_actual"])

    async def executor(*_args):
        return _response(next(outcomes))

    result = await _adjudicator(executor, turn_limit=2).adjudicate(
        expected={}, actual={}, score=_score()
    )
    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "non_reproducible"


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
