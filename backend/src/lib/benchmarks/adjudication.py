"""Strictly gated supplemental adjudication for ambiguous benchmark mismatches."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, ValidationError

from src.lib.observability.runtime import report_runtime_exception

from .models import (
    BenchmarkAdjudicationAttempt,
    BenchmarkAdjudicationFailure,
    BenchmarkAdjudicationResult,
    BenchmarkDeterministicScore,
    BilledCost,
    StrictModel,
)

ADJUDICATION_RUBRIC_VERSION = 1
ADJUDICATION_MODEL = "gpt-5.6-sol"
_RUBRIC = """You are adjudicating only an explicitly ambiguous benchmark mismatch.
The deterministic score remains authoritative. Decide whether the expected value or
actual value is better supported by the supplied case, or return uncertain. Do not
repair malformed output, excuse missing fields, or reinterpret provider failures."""
ADJUDICATION_PROMPT_ID = (
    "benchmark-ambiguity-v1:" + hashlib.sha256(_RUBRIC.encode("utf-8")).hexdigest()[:16]
)


class AdjudicationDecision(StrictModel):
    outcome: Literal["supports_expected", "supports_actual", "uncertain"]
    reason: str = Field(min_length=1, max_length=2000)
    confidence: Decimal = Field(ge=0, le=1, strict=False)
    uncertainty: str = Field(max_length=1000)


class RawAdjudicationResponse(StrictModel):
    decision: AdjudicationDecision
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    billed_cost: BilledCost | None = None


AdjudicationExecutor = Callable[[str, str, int], Awaitable[RawAdjudicationResponse]]


class _AdjudicationProviderError(RuntimeError):
    """Sanitized provider failure safe for operational reporting."""


def _sanitized_provider_error(error_type: str) -> _AdjudicationProviderError:
    try:
        raise _AdjudicationProviderError(
            f"Benchmark adjudicator provider failed ({error_type})"
        ) from None
    except _AdjudicationProviderError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized


def _usage_totals(
    attempts: list[BenchmarkAdjudicationAttempt],
) -> tuple[int | None, int | None, BilledCost | None]:
    input_values = [item.input_tokens for item in attempts if item.input_tokens is not None]
    output_values = [
        item.output_tokens for item in attempts if item.output_tokens is not None
    ]
    costs = [item.billed_cost for item in attempts if item.billed_cost is not None]
    total_cost = None
    if costs and len({cost.unit for cost in costs}) == 1:
        sources = {cost.source for cost in costs}
        total_cost = BilledCost(
            amount=sum((cost.amount for cost in costs), Decimal("0")),
            unit=costs[0].unit,
            source=(sources.pop() if len(sources) == 1 else "summed_adjudication_attempts"),
        )
    return (
        sum(input_values) if input_values else None,
        sum(output_values) if output_values else None,
        total_cost,
    )


def is_adjudication_eligible(score: BenchmarkDeterministicScore) -> bool:
    """Require ambiguity for every non-passing field; hard failures close the gate."""

    failing = [field for field in score.fields if field.outcome != "pass"]
    return bool(failing) and all(
        field.mismatch_class == "ambiguous" and field.adjudication_eligible
        for field in failing
    )


def build_adjudication_prompt(
    *, expected: Any, actual: Any, score: BenchmarkDeterministicScore
) -> str:
    payload = {
        "actual": actual,
        "deterministic": score.model_dump(mode="json"),
        "expected": expected,
        "rubric_version": ADJUDICATION_RUBRIC_VERSION,
    }
    return f"{_RUBRIC}\n\nINPUT:\n{json.dumps(payload, sort_keys=True, default=str)}"


class SupplementalAdjudicator:
    """Bound retries/turns and preserve failures instead of altering gold scores."""

    def __init__(
        self,
        *,
        executor: AdjudicationExecutor,
        enabled: bool,
        timeout_seconds: float,
        retries: int,
        turn_limit: int,
        tool_call_limit: int,
        result_max_bytes: int,
    ) -> None:
        if tool_call_limit != 0:
            raise ValueError("direct benchmark adjudication does not permit tools")
        self.executor = executor
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.turn_limit = turn_limit
        self.result_max_bytes = result_max_bytes

    def unavailable(
        self, category: Literal["disabled", "ineligible", "case_limit"], message: str
    ) -> BenchmarkAdjudicationResult:
        return BenchmarkAdjudicationResult(
            status="not_requested",
            prompt_id=ADJUDICATION_PROMPT_ID,
            model=ADJUDICATION_MODEL,
            failure=BenchmarkAdjudicationFailure(
                category=category, message=message, attempts=0
            ),
        )

    async def adjudicate(
        self,
        *,
        expected: Any,
        actual: Any,
        score: BenchmarkDeterministicScore,
    ) -> BenchmarkAdjudicationResult:
        if not is_adjudication_eligible(score):
            return self.unavailable(
                "ineligible", "deterministic mismatch is not exclusively ambiguous"
            )
        if not self.enabled:
            return self.unavailable("disabled", "supplemental adjudication is disabled")

        prompt = build_adjudication_prompt(
            expected=expected, actual=actual, score=score
        )
        started = time.monotonic()
        attempts = 0
        decisions: list[RawAdjudicationResponse] = []
        attempt_records: list[BenchmarkAdjudicationAttempt] = []
        last_category: Literal[
            "timeout", "refusal", "invalid_result", "provider_error"
        ] = "provider_error"
        for turn in range(self.turn_limit):
            response: RawAdjudicationResponse | None = None
            for retry in range(self.retries + 1):
                attempts += 1
                attempt_started = time.monotonic()
                try:
                    response = await asyncio.wait_for(
                        self.executor(
                            ADJUDICATION_MODEL,
                            prompt,
                            self.result_max_bytes,
                        ),
                        timeout=self.timeout_seconds,
                    )
                    serialized = json.dumps(
                        response.model_dump(mode="json"), sort_keys=True
                    ).encode("utf-8")
                    if len(serialized) > self.result_max_bytes:
                        raise ValueError("adjudication result exceeds configured size")
                    attempt_records.append(
                        BenchmarkAdjudicationAttempt(
                            turn=turn + 1,
                            attempt=attempts,
                            retry=retry,
                            status="completed",
                            latency_ms=max(
                                0, int((time.monotonic() - attempt_started) * 1000)
                            ),
                            outcome=response.decision.outcome,
                            reason=response.decision.reason,
                            confidence=response.decision.confidence,
                            uncertainty=response.decision.uncertainty,
                            input_tokens=response.input_tokens,
                            output_tokens=response.output_tokens,
                            billed_cost=response.billed_cost,
                        )
                    )
                    break
                except TimeoutError:
                    response = None
                    last_category = "timeout"
                except (ValidationError, ValueError, json.JSONDecodeError):
                    last_category = "invalid_result"
                except PermissionError:
                    response = None
                    last_category = "refusal"
                except Exception as exc:
                    response = None
                    last_category = "provider_error"
                    report_runtime_exception(
                        _sanitized_provider_error(type(exc).__name__),
                        component="benchmark_adjudication",
                        operation="provider_call_failed",
                        context={
                            "attempt": attempts,
                            "model": ADJUDICATION_MODEL,
                            "retry": retry,
                            "turn": turn + 1,
                        },
                    )
                attempt_records.append(
                    BenchmarkAdjudicationAttempt(
                        turn=turn + 1,
                        attempt=attempts,
                        retry=retry,
                        status=last_category,
                        latency_ms=max(
                            0, int((time.monotonic() - attempt_started) * 1000)
                        ),
                        input_tokens=(response.input_tokens if response else None),
                        output_tokens=(response.output_tokens if response else None),
                        billed_cost=(response.billed_cost if response else None),
                    )
                )
                response = None
                if last_category == "refusal":
                    break
                if retry == self.retries:
                    break
            if response is None:
                input_tokens, output_tokens, billed_cost = _usage_totals(
                    attempt_records
                )
                return BenchmarkAdjudicationResult(
                    status="failed",
                    prompt_id=ADJUDICATION_PROMPT_ID,
                    model=ADJUDICATION_MODEL,
                    latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    billed_cost=billed_cost,
                    attempts=attempt_records,
                    failure=BenchmarkAdjudicationFailure(
                        category=last_category,
                        message="supplemental adjudication did not return a valid result",
                        attempts=attempts,
                    ),
                )
            decisions.append(response)

        outcomes = {response.decision.outcome for response in decisions}
        input_tokens, output_tokens, billed_cost = _usage_totals(attempt_records)
        if len(outcomes) != 1:
            return BenchmarkAdjudicationResult(
                status="failed",
                prompt_id=ADJUDICATION_PROMPT_ID,
                model=ADJUDICATION_MODEL,
                latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                billed_cost=billed_cost,
                attempts=attempt_records,
                failure=BenchmarkAdjudicationFailure(
                    category="non_reproducible",
                    message="adjudication turns returned conflicting outcomes",
                    attempts=attempts,
                ),
            )
        final = decisions[-1]
        return BenchmarkAdjudicationResult(
            status="completed",
            outcome=final.decision.outcome,
            reason=final.decision.reason,
            confidence=final.decision.confidence,
            uncertainty=final.decision.uncertainty,
            prompt_id=ADJUDICATION_PROMPT_ID,
            model=ADJUDICATION_MODEL,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            billed_cost=billed_cost,
            attempts=attempt_records,
        )


async def execute_direct_openai_adjudication(
    model: str,
    prompt: str,
    result_max_bytes: int,
) -> RawAdjudicationResponse:
    """Call native OpenAI directly with a strict structured-output schema."""

    from openai import AsyncOpenAI

    response = await AsyncOpenAI(max_retries=0).responses.parse(
        model=model,
        input=prompt,
        text_format=AdjudicationDecision,
    )
    parsed = response.output_parsed
    if parsed is None:
        if getattr(response, "output_text", ""):
            encoded = response.output_text.encode("utf-8")
            if len(encoded) > result_max_bytes:
                raise ValueError("adjudication result exceeds configured size")
            parsed = AdjudicationDecision.model_validate_json(response.output_text)
        else:
            raise PermissionError(
                "adjudicator refused or returned no structured output"
            )
    usage = getattr(response, "usage", None)
    return RawAdjudicationResponse(
        decision=AdjudicationDecision.model_validate(parsed),
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )
