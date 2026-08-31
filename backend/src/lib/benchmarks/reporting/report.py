"""Pure aggregation of canonical run and score records."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Literal

from ..models import BenchmarkCaseRun
from .models import (
    AccuracySummary,
    ActualRoute,
    BenchmarkReport,
    BenchmarkScoreRecord,
    CaseReport,
    CostSummary,
    CostTotal,
    DimensionSummary,
    FailureCount,
    LatencySummary,
    ReportFailure,
    ReportProvenance,
    ScoreOutcome,
    UsageSummary,
)


def _accuracy(scores: Iterable[BenchmarkScoreRecord], method: str) -> AccuracySummary:
    outcomes = Counter(score.outcome for score in scores if score.method == method)
    decided = outcomes["passed"] + outcomes["failed"]
    return AccuracySummary(
        passed=outcomes["passed"],
        failed=outcomes["failed"],
        errors=outcomes["error"],
        pass_rate=(Decimal(outcomes["passed"]) / Decimal(decided) if decided else None),
    )


def _usage(runs: Iterable[BenchmarkCaseRun]) -> UsageSummary:
    records = list(runs)
    present = [run.provider_usage for run in records if run.provider_usage is not None]
    return UsageSummary(
        input_tokens=sum(item.input_tokens or 0 for item in present),
        output_tokens=sum(item.output_tokens or 0 for item in present),
        total_tokens=sum(item.total_tokens or 0 for item in present),
        records_missing_usage=len(records) - len(present),
    )


def _cost(runs: Iterable[BenchmarkCaseRun]) -> CostSummary:
    records = list(runs)
    totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    present = 0
    for run in records:
        cost = run.provider_usage.billed_cost if run.provider_usage else None
        if cost is not None:
            totals[(cost.unit, cost.source)] += cost.amount
            present += 1
    return CostSummary(
        exact_totals=[
            CostTotal(unit=unit, source=source, amount=totals[(unit, source)])
            for unit, source in sorted(totals)
        ],
        records_with_exact_cost=present,
        records_missing_exact_cost=len(records) - present,
    )


def _latency(runs: Iterable[BenchmarkCaseRun]) -> LatencySummary:
    values = [run.latency_ms for run in runs]
    total = sum(values)
    return LatencySummary(
        count=len(values),
        total_ms=total,
        minimum_ms=min(values) if values else None,
        maximum_ms=max(values) if values else None,
        mean_ms=Decimal(total) / Decimal(len(values)) if values else None,
    )


def _summary(
    *,
    dimension: Literal["agent", "route", "case"],
    key: str,
    runs: list[BenchmarkCaseRun],
    scores_by_run: dict[str, list[BenchmarkScoreRecord]],
) -> DimensionSummary:
    scores = [score for run in runs for score in scores_by_run.get(run.run_id, [])]
    return DimensionSummary(
        dimension=dimension,
        key=key,
        run_count=len(runs),
        succeeded=sum(run.status == "succeeded" for run in runs),
        failed=sum(run.status == "failed" for run in runs),
        deterministic_accuracy=_accuracy(scores, "deterministic"),
        adjudicated_accuracy=_accuracy(scores, "adjudicated"),
        latency=_latency(runs),
        usage=_usage(runs),
        cost=_cost(runs),
    )


def _actual_route(run: BenchmarkCaseRun) -> ActualRoute | None:
    usage = run.provider_usage
    if usage is None or usage.actual_provider is None or usage.actual_model is None:
        return None
    return ActualRoute(provider=usage.actual_provider, model=usage.actual_model)


def _route_key(run: BenchmarkCaseRun) -> str:
    actual = _actual_route(run)
    if actual is None:
        return "unavailable"
    return f"{actual.provider}/{actual.model}"


def _grouped(
    runs: list[BenchmarkCaseRun], key: Callable[[BenchmarkCaseRun], str]
) -> dict[str, list[BenchmarkCaseRun]]:
    groups: dict[str, list[BenchmarkCaseRun]] = defaultdict(list)
    for run in runs:
        groups[key(run)].append(run)
    return groups


def build_benchmark_report(
    runs: Iterable[BenchmarkCaseRun],
    scores: Iterable[BenchmarkScoreRecord],
    provenance: ReportProvenance,
) -> BenchmarkReport:
    """Build a stable, sensitive-content-free report from canonical records."""

    ordered_runs = sorted(runs, key=lambda run: run.run_id)
    score_records = sorted(
        scores, key=lambda score: (score.run_id, score.method, score.scorer_id)
    )
    known_run_ids = {run.run_id for run in ordered_runs}
    if len(known_run_ids) != len(ordered_runs):
        raise ValueError("canonical run records must have unique run IDs")
    unknown = sorted({score.run_id for score in score_records} - known_run_ids)
    if unknown:
        raise ValueError(f"score records reference unknown runs: {', '.join(unknown)}")
    score_keys = {
        (score.run_id, score.scorer_id, score.method) for score in score_records
    }
    if len(score_keys) != len(score_records):
        raise ValueError("score records must be unique per run, scorer, and method")
    scores_by_run: dict[str, list[BenchmarkScoreRecord]] = defaultdict(list)
    for score in score_records:
        scores_by_run[score.run_id].append(score)

    cases = []
    for run in ordered_runs:
        run_scores = scores_by_run.get(run.run_id, [])
        cases.append(
            CaseReport(
                run_id=run.run_id,
                profile_id=run.profile_id,
                case_id=run.case_id,
                target=run.target,
                fixture_digest=run.fixture_digest,
                requested_route=run.requested_route,
                actual_route=_actual_route(run),
                started_at=run.started_at,
                completed_at=run.completed_at,
                latency_ms=run.latency_ms,
                status=run.status,
                failure=(ReportFailure(category=run.failure.category) if run.failure else None),
                usage=_usage([run]),
                cost=_cost([run]),
                scores=[
                    ScoreOutcome(
                        scorer_id=score.scorer_id,
                        scorer_version=score.scorer_version,
                        method=score.method,
                        outcome=score.outcome,
                        score=score.score,
                        adjudicator_version=score.adjudicator_version,
                    )
                    for score in run_scores
                ],
            )
        )

    agent_groups = _grouped(ordered_runs, lambda run: f"{run.target.kind}:{run.target.id}")
    route_groups = _grouped(ordered_runs, _route_key)
    case_groups = _grouped(ordered_runs, lambda run: f"{run.profile_id}/{run.case_id}")
    failures = Counter(
        run.failure.category for run in ordered_runs if run.failure is not None
    )
    return BenchmarkReport(
        provenance=provenance,
        cases=cases,
        by_agent=[
            _summary(
                dimension="agent",
                key=key,
                runs=agent_groups[key],
                scores_by_run=scores_by_run,
            )
            for key in sorted(agent_groups)
        ],
        by_route=[
            _summary(
                dimension="route",
                key=key,
                runs=route_groups[key],
                scores_by_run=scores_by_run,
            )
            for key in sorted(route_groups)
        ],
        cross_route=[
            _summary(
                dimension="case",
                key=key,
                runs=case_groups[key],
                scores_by_run=scores_by_run,
            )
            for key in sorted(case_groups)
        ],
        aggregate=_summary(
            dimension="case",
            key="all",
            runs=ordered_runs,
            scores_by_run=scores_by_run,
        ),
        failures=[
            FailureCount(category=key, count=failures[key]) for key in sorted(failures)
        ],
    )
