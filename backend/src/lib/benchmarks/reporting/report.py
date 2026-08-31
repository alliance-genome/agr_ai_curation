"""Pure aggregation of canonical run and score records."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Literal

from ..models import BenchmarkCaseRun, BenchmarkScoringRecord
from .models import (
    AccuracySummary,
    ActualRoute,
    AdjudicationFailureOutcome,
    AdjudicationOutcome,
    AdjudicationSummary,
    BenchmarkReport,
    CaseReport,
    CostSummary,
    CostTotal,
    DimensionSummary,
    DeterministicScoreOutcome,
    FailureCount,
    LatencySummary,
    ReportFailure,
    ReportProvenance,
    ScoreOutcome,
    UsageSummary,
)


def _accuracy(scores: Iterable[BenchmarkScoringRecord]) -> AccuracySummary:
    records = list(scores)
    outcomes = Counter(score.deterministic.outcome for score in records)
    total_weight = sum(
        (score.deterministic.total_weight for score in records), Decimal("0")
    )
    earned_weight = sum(
        (score.deterministic.earned_weight for score in records), Decimal("0")
    )
    return AccuracySummary(
        passed=outcomes["pass"],
        partial=outcomes["partial"],
        failed=outcomes["fail"],
        pass_rate=(
            Decimal(outcomes["pass"]) / Decimal(len(records)) if records else None
        ),
        weighted_score=(earned_weight / total_weight if total_weight else None),
    )


def _adjudication(scores: Iterable[BenchmarkScoringRecord]) -> AdjudicationSummary:
    records = [score.adjudication for score in scores if score.adjudication is not None]
    statuses = Counter(record.status for record in records)
    outcomes = Counter(record.outcome for record in records if record.outcome is not None)
    return AdjudicationSummary(
        not_requested=statuses["not_requested"],
        completed=statuses["completed"],
        failed=statuses["failed"],
        supports_expected=outcomes["supports_expected"],
        supports_actual=outcomes["supports_actual"],
        uncertain=outcomes["uncertain"],
    )


def _usage(runs: Iterable[BenchmarkCaseRun]) -> UsageSummary:
    records = list(runs)
    present = [run.provider_usage for run in records if run.provider_usage is not None]
    return UsageSummary(
        input_tokens=sum(item.input_tokens for item in present if item.input_tokens is not None),
        output_tokens=sum(
            item.output_tokens for item in present if item.output_tokens is not None
        ),
        total_tokens=sum(item.total_tokens for item in present if item.total_tokens is not None),
        records_missing_usage=len(records) - len(present),
        records_missing_input_tokens=sum(
            run.provider_usage is None or run.provider_usage.input_tokens is None
            for run in records
        ),
        records_missing_output_tokens=sum(
            run.provider_usage is None or run.provider_usage.output_tokens is None
            for run in records
        ),
        records_missing_total_tokens=sum(
            run.provider_usage is None or run.provider_usage.total_tokens is None
            for run in records
        ),
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


def _adjudication_cost(scores: Iterable[BenchmarkScoringRecord]) -> CostSummary:
    records = [score.adjudication for score in scores if score.adjudication is not None]
    totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    present = 0
    for adjudication in records:
        cost = adjudication.billed_cost
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
) -> DimensionSummary:
    scores = [score for run in runs for score in run.scoring]
    return DimensionSummary(
        dimension=dimension,
        key=key,
        run_count=len(runs),
        succeeded=sum(run.status == "succeeded" for run in runs),
        failed=sum(run.status == "failed" for run in runs),
        deterministic_accuracy=_accuracy(scores),
        adjudication=_adjudication(scores),
        latency=_latency(runs),
        usage=_usage(runs),
        cost=_cost(runs),
        adjudication_cost=_adjudication_cost(scores),
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
    provenance: ReportProvenance,
) -> BenchmarkReport:
    """Build a stable, sensitive-content-free report from canonical runs."""

    ordered_runs = sorted(runs, key=lambda run: run.run_id)
    known_run_ids = {run.run_id for run in ordered_runs}
    if len(known_run_ids) != len(ordered_runs):
        raise ValueError("canonical run records must have unique run IDs")

    cases = []
    for run in ordered_runs:
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
                adjudication_cost=_adjudication_cost(run.scoring),
                scores=[
                    ScoreOutcome(
                        deterministic=DeterministicScoreOutcome(
                            scorer_id=score.deterministic.scorer_id,
                            scoring_version=score.deterministic.scoring_version,
                            outcome=score.deterministic.outcome,
                            weighted_score=score.deterministic.weighted_score,
                            earned_weight=score.deterministic.earned_weight,
                            total_weight=score.deterministic.total_weight,
                        ),
                        adjudication=(
                            AdjudicationOutcome(
                                rubric_version=score.adjudication.rubric_version,
                                status=score.adjudication.status,
                                outcome=score.adjudication.outcome,
                                confidence=score.adjudication.confidence,
                                prompt_id=score.adjudication.prompt_id,
                                model=score.adjudication.model,
                                latency_ms=score.adjudication.latency_ms,
                                input_tokens=score.adjudication.input_tokens,
                                output_tokens=score.adjudication.output_tokens,
                                billed_cost=(
                                    CostTotal(
                                        unit=score.adjudication.billed_cost.unit,
                                        source=score.adjudication.billed_cost.source,
                                        amount=score.adjudication.billed_cost.amount,
                                    )
                                    if score.adjudication.billed_cost is not None
                                    else None
                                ),
                                failure=(
                                    AdjudicationFailureOutcome(
                                        category=score.adjudication.failure.category,
                                        attempts=score.adjudication.failure.attempts,
                                    )
                                    if score.adjudication.failure is not None
                                    else None
                                ),
                            )
                            if score.adjudication is not None
                            else None
                        ),
                    )
                    for score in run.scoring
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
            )
            for key in sorted(agent_groups)
        ],
        by_route=[
            _summary(
                dimension="route",
                key=key,
                runs=route_groups[key],
            )
            for key in sorted(route_groups)
        ],
        cross_route=[
            _summary(
                dimension="case",
                key=key,
                runs=case_groups[key],
            )
            for key in sorted(case_groups)
        ],
        aggregate=_summary(
            dimension="case",
            key="all",
            runs=ordered_runs,
        ),
        failures=[
            FailureCount(category=key, count=failures[key]) for key in sorted(failures)
        ],
    )
