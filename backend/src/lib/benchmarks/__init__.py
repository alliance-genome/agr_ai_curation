"""Developer-only, project-agnostic benchmark harness."""

from .input_resolvers import (
    BenchmarkInputResolver,
    BenchmarkInputResolverCatalog,
    BenchmarkResolverRegistrationError,
    BenchmarkSourceRequestContext,
    DelegatedAuthorizationCapability,
    DelegatedSourceAuthorization,
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
from .snapshots import (
    BenchmarkSnapshotRepository,
    BenchmarkSnapshotStore,
    FrozenBenchmarkInputSnapshot,
    materialize_and_freeze_plan_inputs,
)

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
    "BenchmarkSnapshotRepository",
    "BenchmarkSnapshotStore",
    "BenchmarkSourceRequestContext",
    "BenchmarkScoringRecord",
    "BenchmarkSelection",
    "BenchmarkService",
    "BenchmarkSuite",
    "DryRunPlan",
    "DelegatedAuthorizationCapability",
    "DelegatedSourceAuthorization",
    "FrozenBenchmarkInputSnapshot",
    "MaterializedBenchmarkInput",
    "MaterializedBenchmarkPlanInputs",
    "ResolvedBenchmarkPlan",
    "aggregate_scores",
    "score_case",
    "load_checked_in_suites",
    "materialize_plan_inputs",
    "materialize_and_freeze_plan_inputs",
    "load_suite",
    "resolve_suite",
    "validate_suite",
]
