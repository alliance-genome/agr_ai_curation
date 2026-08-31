"""Developer-only, project-agnostic benchmark harness."""

from .loader import BenchmarkCatalog, BenchmarkCatalogError
from .models import (
    BenchmarkAggregateScore,
    BenchmarkCaseRun,
    BenchmarkDeterministicScore,
    BenchmarkScoringRecord,
    BenchmarkSelection,
    DryRunPlan,
)
from .scoring import aggregate_scores, score_case
from .service import BenchmarkService

__all__ = [
    "BenchmarkAggregateScore",
    "BenchmarkCaseRun",
    "BenchmarkCatalog",
    "BenchmarkCatalogError",
    "BenchmarkDeterministicScore",
    "BenchmarkScoringRecord",
    "BenchmarkSelection",
    "BenchmarkService",
    "DryRunPlan",
    "aggregate_scores",
    "score_case",
]
