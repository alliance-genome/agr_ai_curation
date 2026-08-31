"""Bounded orchestration shared by the admin API and developer CLI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from src.lib.observability.runtime import report_runtime_exception
from src.lib.openai_agents.provider_usage import (
    capture_provider_usage,
    provider_usage_metadata,
)

from .adjudication import SupplementalAdjudicator, is_adjudication_eligible
from .loader import BenchmarkCatalog, BenchmarkCatalogError
from .models import (
    BenchmarkCaseRun,
    BenchmarkExecutionResponse,
    BenchmarkFailure,
    BenchmarkOutput,
    BenchmarkRoute,
    BenchmarkScoringRecord,
    BenchmarkSelection,
    DryRunPlan,
    ExecutionResult,
    PlannedCaseRun,
    ProviderUsage,
)
from .scoring import aggregate_scores, score_case

BenchmarkExecutor = Callable[
    [str, dict[str, Any], BenchmarkRoute, str], Awaitable[ExecutionResult]
]

_RESTRICTED_KEYS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "api_key",
    "api-key",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "id_token",
    "credential",
}
_AUTH_VALUE_PATTERN = re.compile(r"(?i)\b(?:bearer|basic)\s+\S+")


def _redact_restricted(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if (
                normalized_key in _RESTRICTED_KEYS
                or normalized_key.endswith("_api_key")
                or normalized_key.endswith("_password")
                or normalized_key.endswith("_secret")
            ):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_restricted(item)
        return redacted
    if isinstance(value, list):
        return [_redact_restricted(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_restricted(item) for item in value]
    if isinstance(value, str):
        return _AUTH_VALUE_PATTERN.sub("[REDACTED]", value)
    return value


class BenchmarkService:
    def __init__(
        self,
        catalog: BenchmarkCatalog,
        *,
        agent_executor: BenchmarkExecutor,
        flow_executor: BenchmarkExecutor,
        max_concurrency: int,
        matrix_limit: int,
        case_limit: int,
        result_limit: int,
        timeout_seconds: float,
        retries: int,
        preview_max_chars: int,
        inline_max_bytes: int,
        adjudicator: SupplementalAdjudicator | None = None,
        adjudication_case_limit: int = 1,
    ) -> None:
        self.catalog = catalog
        self._executors = {"agent": agent_executor, "flow": flow_executor}
        self.max_concurrency = max_concurrency
        self.matrix_limit = matrix_limit
        self.case_limit = case_limit
        self.result_limit = result_limit
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.preview_max_chars = preview_max_chars
        self.inline_max_bytes = inline_max_bytes
        self.adjudicator = adjudicator
        self.adjudication_case_limit = adjudication_case_limit

    def plan(self, selection: BenchmarkSelection) -> DryRunPlan:
        if selection.route is not None:
            self.catalog.validate_route(selection.route.model, selection.route.provider)
        requested_profiles = set(selection.profile_ids)
        unknown_profiles = requested_profiles - {
            loaded.profile.profile_id for loaded in self.catalog.profiles
        }
        if unknown_profiles:
            raise BenchmarkCatalogError(
                f"Unknown benchmark profiles: {', '.join(sorted(unknown_profiles))}"
            )
        runs: list[PlannedCaseRun] = []
        selected_case_count = 0
        matched_case_ids: set[str] = set()
        for loaded in self.catalog.profiles:
            profile = loaded.profile
            if requested_profiles and profile.profile_id not in requested_profiles:
                continue
            cases = [
                case
                for case in loaded.cases
                if not selection.case_ids or case.case_id in selection.case_ids
            ]
            selected_case_count += len(cases)
            matched_case_ids.update(case.case_id for case in cases)
            routes = (
                [selection.route] if selection.route is not None else profile.routes
            )
            for case in cases:
                for route in routes:
                    identity = "\0".join(
                        (
                            profile.profile_id,
                            case.case_id,
                            route.provider,
                            route.model,
                            case.fixture_digest,
                        )
                    )
                    run_id = f"benchmark-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
                    runs.append(
                        PlannedCaseRun(
                            run_id=run_id,
                            profile_id=profile.profile_id,
                            case_id=case.case_id,
                            target=profile.target,
                            requested_route=route,
                            fixture_digest=case.fixture_digest,
                        )
                    )
        unknown_cases = set(selection.case_ids) - matched_case_ids
        if unknown_cases:
            raise BenchmarkCatalogError(
                f"Unknown selected cases: {', '.join(sorted(unknown_cases))}"
            )
        if selected_case_count > self.case_limit:
            raise BenchmarkCatalogError(
                f"Selection contains {selected_case_count} cases; limit is {self.case_limit}"
            )
        if len(runs) > self.matrix_limit:
            raise BenchmarkCatalogError(
                f"Matrix contains {len(runs)} runs; limit is {self.matrix_limit}"
            )
        if len(runs) > self.result_limit:
            raise BenchmarkCatalogError(
                f"Result count {len(runs)} exceeds limit {self.result_limit}"
            )
        return DryRunPlan(runs=runs)

    async def execute(
        self, selection: BenchmarkSelection
    ) -> BenchmarkExecutionResponse:
        plan = self.plan(selection)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def run(planned: PlannedCaseRun) -> tuple[BenchmarkCaseRun, Any]:
            async with semaphore:
                return await self._execute_case(planned)

        completed = list(await asyncio.gather(*(run(item) for item in plan.runs)))
        runs = [item[0] for item in completed]
        if self.adjudicator is not None:
            adjudicated_cases = 0
            for run_record, raw_output in completed:
                loaded = self.catalog.get_profile(run_record.profile_id)
                case = next(
                    item for item in loaded.cases if item.case_id == run_record.case_id
                )
                for scoring_record in run_record.scoring:
                    score = scoring_record.deterministic
                    if not is_adjudication_eligible(score):
                        continue
                    if (
                        self.adjudicator.enabled
                        and adjudicated_cases >= self.adjudication_case_limit
                    ):
                        scoring_record.adjudication = self.adjudicator.unavailable(
                            "case_limit",
                            "supplemental adjudication case limit was reached",
                        )
                        continue
                    if self.adjudicator.enabled:
                        adjudicated_cases += 1
                    scoring_record.adjudication = await self.adjudicator.adjudicate(
                        expected=case.expected,
                        actual=raw_output,
                        score=score,
                    )
        return BenchmarkExecutionResponse(runs=runs, aggregates=aggregate_scores(runs))

    async def _execute_case(
        self, planned: PlannedCaseRun
    ) -> tuple[BenchmarkCaseRun, Any]:
        loaded = self.catalog.get_profile(planned.profile_id)
        case = next(case for case in loaded.cases if case.case_id == planned.case_id)
        started_at = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        result: ExecutionResult | None = None
        provider_usage: ProviderUsage | None = None
        failure: BenchmarkFailure | None = None
        executor = self._executors[planned.target.kind]
        for attempt in range(self.retries + 1):
            try:
                with capture_provider_usage() as usage_records:
                    result = await asyncio.wait_for(
                        executor(
                            planned.target.id,
                            case.input,
                            planned.requested_route,
                            planned.run_id,
                        ),
                        timeout=self.timeout_seconds,
                    )
                if usage_records:
                    provider_usage = ProviderUsage.model_validate(
                        provider_usage_metadata(usage_records[-1])
                    )
                break
            except TimeoutError:
                failure = BenchmarkFailure(
                    category="timeout", message="Benchmark execution timed out"
                )
            except ValueError:
                failure = BenchmarkFailure(
                    category="configuration_error",
                    message="Benchmark runtime configuration is invalid",
                )
                break
            except RuntimeError:
                failure = BenchmarkFailure(
                    category="runtime_error",
                    message="Benchmark target execution failed",
                )
            except Exception:
                sanitized_exception = RuntimeError(
                    "Unexpected benchmark orchestration failure"
                )
                report_runtime_exception(
                    sanitized_exception,
                    component="benchmarks",
                    operation="execute_case",
                    tags={"run_kind": planned.target.kind},
                    context={
                        "run_id_hash": planned.run_id.removeprefix("benchmark-"),
                        "target_kind": planned.target.kind,
                    },
                )
                failure = BenchmarkFailure(
                    category="internal_error", message="Benchmark execution failed"
                )
                break
            if attempt == self.retries:
                break
        completed_at = datetime.now(timezone.utc)
        output = self._bounded_output(result.output) if result is not None else None
        run_record = BenchmarkCaseRun(
            run_id=planned.run_id,
            profile_id=planned.profile_id,
            case_id=planned.case_id,
            target=planned.target,
            requested_route=planned.requested_route,
            provider_usage=provider_usage if result is not None else None,
            started_at=started_at,
            completed_at=completed_at,
            latency_ms=max(0, int((time.monotonic() - started_monotonic) * 1000)),
            status="succeeded" if result is not None else "failed",
            failure=None if result is not None else failure,
            fixture_digest=planned.fixture_digest,
            output=output,
        )
        raw_output = result.output if result is not None else None
        run_record.scoring = [
            BenchmarkScoringRecord(
                deterministic=score_case(
                    scorer=scorer,
                    expected=case.expected,
                    actual=raw_output,
                    provider_failure=result is None,
                )
            )
            for scorer in loaded.profile.scorers
        ]
        return run_record, raw_output

    def _bounded_output(self, value: Any) -> BenchmarkOutput:
        value = _redact_restricted(value)
        serialized = json.dumps(
            value, sort_keys=True, ensure_ascii=False, default=str
        ).encode("utf-8")
        if len(serialized) <= self.inline_max_bytes:
            kind = "text" if isinstance(value, str) else "json"
            return BenchmarkOutput(kind=kind, value=value, size_bytes=len(serialized))
        preview = serialized.decode("utf-8", errors="replace")[: self.preview_max_chars]
        return BenchmarkOutput(
            kind="preview", value=preview, truncated=True, size_bytes=len(serialized)
        )
