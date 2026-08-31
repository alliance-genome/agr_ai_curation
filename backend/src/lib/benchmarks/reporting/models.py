"""Forward-only schemas for reviewable benchmark reports and manifests."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from ..models import BenchmarkRoute, BenchmarkTarget, StrictModel

_LOGICAL_RUN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class AccuracySummary(StrictModel):
    passed: int = Field(ge=0)
    partial: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: Decimal | None = Field(default=None, ge=0, le=1, strict=False)
    weighted_score: Decimal | None = Field(default=None, ge=0, le=1, strict=False)


class AdjudicationSummary(StrictModel):
    not_requested: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    supports_expected: int = Field(ge=0)
    supports_actual: int = Field(ge=0)
    uncertain: int = Field(ge=0)


class UsageSummary(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    records_missing_usage: int = Field(ge=0)
    records_missing_input_tokens: int = Field(ge=0)
    records_missing_output_tokens: int = Field(ge=0)
    records_missing_total_tokens: int = Field(ge=0)


class CostTotal(StrictModel):
    unit: str = Field(min_length=1, max_length=32)
    source: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(ge=0, strict=False)


class CostSummary(StrictModel):
    exact_totals: list[CostTotal]
    records_with_exact_cost: int = Field(ge=0)
    records_missing_exact_cost: int = Field(ge=0)


class LatencySummary(StrictModel):
    count: int = Field(ge=0)
    total_ms: int = Field(ge=0)
    minimum_ms: int | None = Field(default=None, ge=0)
    maximum_ms: int | None = Field(default=None, ge=0)
    mean_ms: Decimal | None = Field(default=None, ge=0, strict=False)


class DeterministicScoreOutcome(StrictModel):
    scorer_id: str
    scoring_version: int = Field(ge=1)
    outcome: Literal["pass", "partial", "fail"]
    weighted_score: Decimal = Field(ge=0, le=1, strict=False)
    earned_weight: Decimal = Field(ge=0, strict=False)
    total_weight: Decimal = Field(gt=0, strict=False)


class AdjudicationFailureOutcome(StrictModel):
    category: str
    attempts: int = Field(ge=0)


class AdjudicationOutcome(StrictModel):
    rubric_version: int = Field(ge=1)
    status: Literal["not_requested", "completed", "failed"]
    outcome: Literal["supports_expected", "supports_actual", "uncertain"] | None
    confidence: Decimal | None = Field(default=None, ge=0, le=1, strict=False)
    prompt_id: str
    model: str
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    billed_cost: CostTotal | None
    failure: AdjudicationFailureOutcome | None


class ScoreOutcome(StrictModel):
    deterministic: DeterministicScoreOutcome
    adjudication: AdjudicationOutcome | None


class ReportFailure(StrictModel):
    category: str


class ActualRoute(StrictModel):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=255)


class CaseReport(StrictModel):
    run_id: str
    profile_id: str
    case_id: str
    target: BenchmarkTarget
    fixture_digest: str
    requested_route: BenchmarkRoute
    actual_route: ActualRoute | None
    started_at: datetime
    completed_at: datetime
    latency_ms: int = Field(ge=0)
    status: Literal["succeeded", "failed"]
    failure: ReportFailure | None
    usage: UsageSummary
    cost: CostSummary
    adjudication_cost: CostSummary
    scores: list[ScoreOutcome]


class DimensionSummary(StrictModel):
    dimension: Literal["agent", "route", "case"]
    key: str
    run_count: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    deterministic_accuracy: AccuracySummary
    adjudication: AdjudicationSummary
    latency: LatencySummary
    usage: UsageSummary
    cost: CostSummary
    adjudication_cost: CostSummary


class FailureCount(StrictModel):
    category: str
    count: int = Field(ge=0)


class ReportProvenance(StrictModel):
    logical_run_id: str = Field(
        min_length=1, max_length=255, pattern=_LOGICAL_RUN_PATTERN
    )
    generated_at: datetime
    profile_revision: str = Field(min_length=1, max_length=255)
    config_revision: str = Field(min_length=1, max_length=255)
    code_revision: str = Field(min_length=1, max_length=255)


class BenchmarkReport(StrictModel):
    schema_version: Literal[1] = 1
    provenance: ReportProvenance
    cases: list[CaseReport]
    by_agent: list[DimensionSummary]
    by_route: list[DimensionSummary]
    cross_route: list[DimensionSummary]
    aggregate: DimensionSummary
    failures: list[FailureCount]


class ArtifactDescriptor(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class StoredArtifactReceipt(StrictModel):
    name: str
    bucket: str
    key: str
    version_id: str = Field(min_length=1)
    etag: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class ArtifactManifest(StrictModel):
    schema_version: Literal[1] = 1
    provenance: ReportProvenance
    fixture_digests: list[str]
    scorer_versions: list[str]
    adjudicator_versions: list[str]
    requested_routes: list[BenchmarkRoute]
    actual_routes: list[ActualRoute]
    artifacts: list[ArtifactDescriptor]
    storage_receipts: list[StoredArtifactReceipt] = Field(default_factory=list)


class ArtifactBundle(StrictModel):
    report: BenchmarkReport
    report_bytes: bytes
    manifest: ArtifactManifest
    manifest_bytes: bytes
