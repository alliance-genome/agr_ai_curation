import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.lib.benchmarks.models import (
    BenchmarkCaseRun,
    BenchmarkFailure,
    BenchmarkOutput,
    BenchmarkRoute,
    BenchmarkTarget,
    BilledCost,
    ProviderUsage,
)
from src.lib.benchmarks.reporting import (
    BenchmarkReport,
    BenchmarkScoreRecord,
    ReportProvenance,
    build_artifact_bundle,
    build_benchmark_report,
    canonical_json_bytes,
)

NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)


def _run(
    run_id: str,
    *,
    case_id: str,
    actual_provider: str | None = "provider-a",
    cost: Decimal | None = Decimal("0.125"),
    failed: bool = False,
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
    )


def _provenance(logical_run_id: str = "logical-1") -> ReportProvenance:
    return ReportProvenance(
        logical_run_id=logical_run_id,
        generated_at=NOW,
        profile_revision="profiles-sha256:abc",
        config_revision="config-git:123",
        code_revision="git:456",
    )


def test_report_reconciles_accuracy_usage_latency_cost_routes_and_failures():
    runs = [
        _run("run-1", case_id="case-1"),
        _run(
            "run-2",
            case_id="case-1",
            actual_provider=None,
            cost=None,
            failed=True,
        ),
    ]
    scores = [
        BenchmarkScoreRecord(
            run_id="run-1",
            scorer_id="exact",
            scorer_version="1",
            method="deterministic",
            outcome="passed",
            score=Decimal("1"),
        ),
        BenchmarkScoreRecord(
            run_id="run-1",
            scorer_id="judge",
            scorer_version="2",
            method="adjudicated",
            adjudicator_version="judge-model:3",
            outcome="failed",
            score=Decimal("0.25"),
        ),
    ]

    report = build_benchmark_report(runs, scores, _provenance())

    assert report.aggregate.run_count == 2
    assert report.aggregate.failed == 1
    assert report.aggregate.deterministic_accuracy.pass_rate == Decimal("1")
    assert report.aggregate.adjudicated_accuracy.pass_rate == Decimal("0")
    assert report.aggregate.latency.total_ms == 20
    assert report.aggregate.usage.total_tokens == 24
    assert report.aggregate.cost.exact_totals[0].amount == Decimal("0.125")
    assert report.aggregate.cost.exact_totals[0].source == "provider-telemetry"
    assert report.aggregate.cost.records_missing_exact_cost == 1
    assert [summary.key for summary in report.by_route] == [
        "provider-a/model-a",
        "unavailable",
    ]
    assert report.failures[0].category == "runtime_error"
    assert report.cases[1].actual_route is None


def test_artifacts_are_allowlisted_redacted_and_stable():
    report = build_benchmark_report(
        [_run("run-1", case_id="case-1", failed=True)], [], _provenance()
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
    ):
        assert forbidden not in serialized
        assert forbidden not in first.manifest_bytes.decode()
    assert first.manifest.artifacts[0].sha256.startswith("sha256:")
    assert first.manifest.fixture_digests == [f"sha256:{'a' * 64}"]


def test_allowlist_rejects_sensitive_or_unknown_artifact_content():
    provenance = ReportProvenance(
        logical_run_id="logical-1",
        generated_at=NOW,
        profile_revision="deployment-secret",
        config_revision="config-git:123",
        code_revision="git:456",
    )
    report = build_benchmark_report(
        [_run("run-1", case_id="case-1")], [], provenance
    )
    with pytest.raises(ValueError, match="secret"):
        canonical_json_bytes(report, secret_patterns=["deployment-secret"])

    payload = json.loads(canonical_json_bytes(build_benchmark_report(
        [_run("run-1", case_id="case-1")], [], _provenance()
    )))
    payload["raw_prompt"] = "must not enter"
    with pytest.raises(ValidationError, match="raw_prompt"):
        BenchmarkReport.model_validate(payload)


def test_score_records_must_reference_a_canonical_run():
    score = BenchmarkScoreRecord(
        run_id="unknown",
        scorer_id="exact",
        scorer_version="1",
        method="deterministic",
        outcome="passed",
    )
    with pytest.raises(ValueError, match="unknown runs"):
        build_benchmark_report([], [score], _provenance())


def test_human_readable_actual_provider_is_preserved():
    report = build_benchmark_report(
        [_run("run-1", case_id="case-1", actual_provider="Test Provider")],
        [],
        _provenance(),
    )
    assert report.cases[0].actual_route is not None
    assert report.cases[0].actual_route.provider == "Test Provider"
