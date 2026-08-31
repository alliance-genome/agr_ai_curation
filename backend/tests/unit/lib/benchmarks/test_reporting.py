import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.lib.benchmarks.models import (
    BenchmarkAdjudicationAttempt,
    BenchmarkAdjudicationResult,
    BenchmarkCaseRun,
    BenchmarkDeterministicScore,
    BenchmarkFailure,
    BenchmarkOutput,
    BenchmarkRoute,
    BenchmarkScoringRecord,
    BenchmarkTarget,
    BilledCost,
    ProviderUsage,
)
from src.lib.benchmarks.reporting import (
    BenchmarkReport,
    ReportProvenance,
    build_artifact_bundle,
    build_benchmark_report,
    canonical_json_bytes,
)

NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)


def _canonical_score(*, adjudicated: bool = False) -> BenchmarkScoringRecord:
    fixture = Path(__file__).parent / "fixtures" / "deterministic_score_v1.json"
    deterministic = BenchmarkDeterministicScore.model_validate_json(
        fixture.read_text(encoding="utf-8")
    )
    adjudication = None
    if adjudicated:
        billed_cost = BilledCost(
            amount=Decimal("0.01"),
            unit="USD",
            source="provider-telemetry",
        )
        adjudication = BenchmarkAdjudicationResult(
            status="completed",
            outcome="supports_actual",
            reason="private adjudication rationale with extracted evidence",
            confidence=Decimal("0.75"),
            uncertainty="private evidence uncertainty",
            prompt_id="benchmark-adjudication-v1",
            model="judge-model-v3",
            latency_ms=25,
            input_tokens=8,
            output_tokens=3,
            billed_cost=billed_cost,
            attempts=[
                BenchmarkAdjudicationAttempt(
                    turn=1,
                    attempt=1,
                    retry=0,
                    status="completed",
                    latency_ms=25,
                    outcome="supports_actual",
                    reason="private attempt rationale with extracted evidence",
                    confidence=Decimal("0.75"),
                    uncertainty="private attempt evidence uncertainty",
                    input_tokens=8,
                    output_tokens=3,
                    billed_cost=billed_cost,
                )
            ],
        )
    return BenchmarkScoringRecord(
        deterministic=deterministic,
        adjudication=adjudication,
    )


def _run(
    run_id: str,
    *,
    case_id: str,
    actual_provider: str | None = "provider-a",
    cost: Decimal | None = Decimal("0.125"),
    failed: bool = False,
    scoring: list[BenchmarkScoringRecord] | None = None,
) -> BenchmarkCaseRun:
    usage = ProviderUsage(
        requested_provider="router",
        requested_model="model-a",
        actual_provider=actual_provider,
        actual_model="model-a" if actual_provider else None,
        latency_ms=10,
        input_tokens=5,
        output_tokens=7,
        total_tokens=12,
        billed_cost=(
            BilledCost(amount=cost, unit="USD", source="provider-telemetry")
            if cost is not None
            else None
        ),
    )
    return BenchmarkCaseRun(
        run_id=run_id,
        profile_id="profile-1",
        case_id=case_id,
        target=BenchmarkTarget(kind="agent", id="agent-1"),
        requested_route=BenchmarkRoute(provider="router", model="model-a"),
        provider_usage=usage,
        started_at=NOW,
        completed_at=NOW,
        latency_ms=10,
        status="failed" if failed else "succeeded",
        failure=(
            BenchmarkFailure(
                category="runtime_error",
                message="Authorization: Bearer raw-secret exception and document text",
            )
            if failed
            else None
        ),
        fixture_digest=f"sha256:{'a' * 64}",
        output=BenchmarkOutput(
            kind="json",
            value={
                "prompt": "private prompt",
                "pdf_text": "restricted document",
                "evidence": "private evidence",
                "authorization": "Bearer raw-secret",
            },
            size_bytes=100,
        ),
        scoring=scoring or [],
    )


def _provenance(logical_run_id: str = "logical-1") -> ReportProvenance:
    return ReportProvenance(
        logical_run_id=logical_run_id,
        generated_at=NOW,
        profile_revision="profiles-sha256:abc",
        config_revision="config-git:123",
        code_revision="git:456",
    )


def test_report_consumes_canonical_scoring_and_reconciles_run_metrics():
    runs = [
        _run(
            "run-1",
            case_id="case-1",
            scoring=[_canonical_score(adjudicated=True)],
        ),
        _run(
            "run-2",
            case_id="case-1",
            actual_provider=None,
            cost=None,
            failed=True,
        ),
    ]

    report = build_benchmark_report(runs, _provenance())

    assert report.aggregate.run_count == 2
    assert report.aggregate.failed == 1
    assert report.aggregate.deterministic_accuracy.partial == 1
    assert report.aggregate.deterministic_accuracy.pass_rate == Decimal("0")
    assert report.aggregate.deterministic_accuracy.weighted_score == Decimal("2.5") / 3
    assert report.aggregate.adjudication.completed == 1
    assert report.aggregate.adjudication.supports_actual == 1
    assert report.aggregate.latency.total_ms == 20
    assert report.aggregate.usage.total_tokens == 24
    assert report.aggregate.cost.exact_totals[0].amount == Decimal("0.125")
    assert report.aggregate.cost.records_missing_exact_cost == 1
    assert report.aggregate.adjudication_cost.exact_totals[0].amount == Decimal("0.01")
    assert [summary.key for summary in report.by_route] == [
        "provider-a/model-a",
        "unavailable",
    ]
    assert report.failures[0].category == "runtime_error"
    case_score = report.cases[0].scores[0]
    assert case_score.deterministic.outcome == "partial"
    assert case_score.adjudication is not None
    assert case_score.adjudication.billed_cost is not None
    assert case_score.adjudication.billed_cost.amount == Decimal("0.01")
    assert report.cases[1].actual_route is None


def test_usage_preserves_partial_and_absent_unknown_token_fields():
    partial_usage = ProviderUsage(
        requested_provider="router",
        requested_model="model-a",
        latency_ms=10,
        input_tokens=5,
        output_tokens=None,
        total_tokens=None,
    )
    partial = _run("run-1", case_id="case-1").model_copy(
        update={"provider_usage": partial_usage}
    )
    absent = _run("run-2", case_id="case-2").model_copy(
        update={"provider_usage": None}
    )

    usage = build_benchmark_report([partial, absent], _provenance()).aggregate.usage

    assert usage.input_tokens == 5
    assert usage.output_tokens == 0
    assert usage.total_tokens == 0
    assert usage.records_missing_usage == 1
    assert usage.records_missing_input_tokens == 1
    assert usage.records_missing_output_tokens == 2
    assert usage.records_missing_total_tokens == 2


def test_artifacts_are_allowlisted_redacted_and_stable():
    report = build_benchmark_report(
        [
            _run(
                "run-1",
                case_id="case-1",
                failed=True,
                scoring=[_canonical_score(adjudicated=True)],
            )
        ],
        _provenance(),
    )
    first = build_artifact_bundle(report)
    second = build_artifact_bundle(report)

    assert first.report_bytes == second.report_bytes
    assert first.manifest_bytes == second.manifest_bytes
    serialized = first.report_bytes.decode()
    for forbidden in (
        "raw-secret",
        "private prompt",
        "restricted document",
        "private evidence",
        "Authorization",
        "private adjudication rationale",
        "private evidence uncertainty",
        "private attempt rationale",
        "private attempt evidence uncertainty",
        "values differ under configured rule",
    ):
        assert forbidden not in serialized
        assert forbidden not in first.manifest_bytes.decode()
    assert first.manifest.artifacts[0].sha256.startswith("sha256:")
    assert first.manifest.fixture_digests == [f"sha256:{'a' * 64}"]
    assert first.manifest.scorer_versions == ["deterministic-v1:1"]
    assert first.manifest.adjudicator_versions == [
        "rubric:1:prompt:benchmark-adjudication-v1:model:judge-model-v3"
    ]
    report_payload = json.loads(first.report_bytes)
    manifest_payload = json.loads(first.manifest_bytes)
    assert report_payload["cases"][0]["requested_route"] == {
        "provider": "router",
        "model": "model-a",
    }
    assert manifest_payload["requested_routes"] == [
        {"provider": "router", "model": "model-a"}
    ]


def test_allowlist_rejects_sensitive_or_unknown_artifact_content():
    provenance = ReportProvenance(
        logical_run_id="logical-1",
        generated_at=NOW,
        profile_revision="deployment-secret",
        config_revision="config-git:123",
        code_revision="git:456",
    )
    report = build_benchmark_report([_run("run-1", case_id="case-1")], provenance)
    with pytest.raises(ValueError, match="secret"):
        canonical_json_bytes(report, secret_patterns=["deployment-secret"])

    payload = json.loads(
        canonical_json_bytes(
            build_benchmark_report(
                [_run("run-1", case_id="case-1")], _provenance()
            )
        )
    )
    payload["raw_prompt"] = "must not enter"
    with pytest.raises(ValidationError, match="raw_prompt"):
        BenchmarkReport.model_validate(payload)


def test_canonical_run_ids_must_be_unique():
    run = _run("duplicate", case_id="case-1")
    with pytest.raises(ValueError, match="unique run IDs"):
        build_benchmark_report([run, run], _provenance())


def test_human_readable_actual_provider_is_preserved():
    report = build_benchmark_report(
        [_run("run-1", case_id="case-1", actual_provider="Test Provider")],
        _provenance(),
    )
    assert report.cases[0].actual_route is not None
    assert report.cases[0].actual_route.provider == "Test Provider"
