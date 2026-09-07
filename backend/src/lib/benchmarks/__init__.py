"""Project-agnostic execution-only benchmark API."""

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
from .errors import BenchmarkCatalogError
from .models import (
    BenchmarkSuite,
    ResolvedBenchmarkPlan,
)
from .suites import load_checked_in_suites, load_suite, resolve_suite, validate_suite
from .snapshots import (
    BenchmarkSnapshotRepository,
    BenchmarkSnapshotStore,
    FrozenBenchmarkInputSnapshot,
    materialize_and_freeze_plan_inputs,
)

__all__ = [
    "BenchmarkCatalogError",
    "BenchmarkInputResolver",
    "BenchmarkInputResolverCatalog",
    "BenchmarkResolverRegistrationError",
    "BenchmarkSnapshotRepository",
    "BenchmarkSnapshotStore",
    "BenchmarkSourceRequestContext",
    "BenchmarkSuite",
    "DelegatedAuthorizationCapability",
    "DelegatedSourceAuthorization",
    "FrozenBenchmarkInputSnapshot",
    "MaterializedBenchmarkInput",
    "MaterializedBenchmarkPlanInputs",
    "ResolvedBenchmarkPlan",
    "load_checked_in_suites",
    "materialize_plan_inputs",
    "materialize_and_freeze_plan_inputs",
    "load_suite",
    "resolve_suite",
    "validate_suite",
]
