"""Developer-only, project-agnostic benchmark harness."""

from .input_resolvers import (
    BenchmarkInputResolver,
    BenchmarkInputResolverCatalog,
    BenchmarkResolverRegistrationError,
    MaterializedBenchmarkPlanInputs,
    MaterializedBenchmarkInput,
    materialize_plan_inputs,
)
from .loader import BenchmarkCatalog, BenchmarkCatalogError
from .models import (
    BenchmarkAdjudicationAttempt,
    BenchmarkAggregateScore,
    BenchmarkCaseRun,
    BenchmarkDeterministicScore,
    BenchmarkScoringRecord,
    BenchmarkSelection,
    BenchmarkSuite,
    DryRunPlan,
    ResolvedBenchmarkPlan,
)
from .suites import load_checked_in_suites, load_suite, resolve_suite, validate_suite
from .scoring import aggregate_scores, score_case
from .service import BenchmarkService

__all__ = [
    "BenchmarkAdjudicationAttempt",
    "BenchmarkAggregateScore",
    "BenchmarkCaseRun",
    "BenchmarkCatalog",
    "BenchmarkCatalogError",
    "BenchmarkDeterministicScore",
    "BenchmarkInputResolver",
    "BenchmarkInputResolverCatalog",
    "BenchmarkResolverRegistrationError",
    "BenchmarkScoringRecord",
    "BenchmarkSelection",
    "BenchmarkService",
    "BenchmarkSuite",
    "DryRunPlan",
    "MaterializedBenchmarkInput",
    "MaterializedBenchmarkPlanInputs",
    "ResolvedBenchmarkPlan",
    "aggregate_scores",
    "score_case",
    "load_checked_in_suites",
    "materialize_plan_inputs",
    "load_suite",
    "resolve_suite",
    "validate_suite",
]
