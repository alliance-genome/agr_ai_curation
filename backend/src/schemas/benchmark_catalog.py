"""Versioned discovery and preview envelopes around the existing suite models."""

from typing import Literal

from pydantic import Field, model_validator

from src.lib.benchmarks.models import (
    BenchmarkModelCatalogEntry, BenchmarkRouteSlot, BenchmarkSuite,
    BenchmarkTargetCatalogEntry, FrozenStrictModel, ResolvedBenchmarkPlan,
)

CatalogSection = Literal["targets", "route_slots", "models"]


class BenchmarkCatalogPage(FrozenStrictModel):
    schema_version: Literal[1] = 1
    catalog_schema_version: Literal[1] = 1
    catalog_digest: str
    environment_id: str
    api_enabled: bool
    execution_enabled: bool
    worker_enabled: bool
    resolver_ids: tuple[str, ...]
    section: CatalogSection
    items: tuple[BenchmarkTargetCatalogEntry | BenchmarkRouteSlot | BenchmarkModelCatalogEntry, ...]
    total_items: int
    next_cursor: str | None = None


class BenchmarkSuiteReference(FrozenStrictModel):
    suite_id: str = Field(min_length=1, max_length=128)
    suite_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class BenchmarkSuiteSummary(BenchmarkSuiteReference):
    schema_version: Literal[2] = 2
    case_count: int
    configuration_count: int
    repetitions: int


class BenchmarkSuitePage(FrozenStrictModel):
    schema_version: Literal[1] = 1
    suite_catalog_digest: str
    items: tuple[BenchmarkSuiteSummary, ...]
    total_items: int
    next_cursor: str | None = None


class BenchmarkSuiteResponse(FrozenStrictModel):
    schema_version: Literal[1] = 1
    suite_digest: str
    suite: BenchmarkSuite


class BenchmarkPlanPreviewRequest(FrozenStrictModel):
    catalog_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    suite: BenchmarkSuite | None = None
    checked_in_suite: BenchmarkSuiteReference | None = None

    @model_validator(mode="after")
    def require_one_suite(self) -> "BenchmarkPlanPreviewRequest":
        if (self.suite is None) == (self.checked_in_suite is None):
            raise ValueError("Supply exactly one suite or checked_in_suite reference")
        return self


class BenchmarkPreviewWarning(FrozenStrictModel):
    code: Literal["inputs_not_materialized"] = "inputs_not_materialized"
    message: str = "Input content, source authorization, availability, versions and digests are checked at admission, not preview."


class BenchmarkPlanPreviewResponse(FrozenStrictModel):
    schema_version: Literal[1] = 1
    catalog_schema_version: Literal[1] = 1
    suite_schema_version: Literal[2] = 2
    plan: ResolvedBenchmarkPlan
    cell_count: int
    warnings: tuple[BenchmarkPreviewWarning, ...] = (BenchmarkPreviewWarning(),)
