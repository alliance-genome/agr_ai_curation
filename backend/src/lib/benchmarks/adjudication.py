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

from .models import (
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
        last_category: Literal[
            "timeout", "refusal", "invalid_result", "provider_error"
        ] = "provider_error"
        for turn in range(self.turn_limit):
            response: RawAdjudicationResponse | None = None
            for retry in range(self.retries + 1):
                attempts += 1
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
                    break
                except TimeoutError:
                    response = None
                    last_category = "timeout"
                except (ValidationError, ValueError, json.JSONDecodeError):
                    response = None
                    last_category = "invalid_result"
                except PermissionError:
                    response = None
                    last_category = "refusal"
                    break
                except Exception:
                    response = None
                    last_category = "provider_error"
                if retry == self.retries:
                    break
            if response is None:
                return BenchmarkAdjudicationResult(
                    status="failed",
                    prompt_id=ADJUDICATION_PROMPT_ID,
                    model=ADJUDICATION_MODEL,
                    latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                    failure=BenchmarkAdjudicationFailure(
                        category=last_category,
                        message="supplemental adjudication did not return a valid result",
                        attempts=attempts,
                    ),
                )
            decisions.append(response)

        outcomes = {response.decision.outcome for response in decisions}
        if len(outcomes) != 1:
            return BenchmarkAdjudicationResult(
                status="failed",
                prompt_id=ADJUDICATION_PROMPT_ID,
                model=ADJUDICATION_MODEL,
                latency_ms=max(0, int((time.monotonic() - started) * 1000)),
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
            input_tokens=sum(item.input_tokens or 0 for item in decisions),
            output_tokens=sum(item.output_tokens or 0 for item in decisions),
            billed_cost=final.billed_cost,
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
