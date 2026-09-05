"""Pure execution-plan checks shared by preview and authoritative admission."""

from src.lib.openai_agents.config import (
    get_benchmark_max_cases,
    get_benchmark_max_cells,
    get_benchmark_max_configurations,
    get_benchmark_max_repetitions,
)

from .loader import BenchmarkCatalogError
from .models import BenchmarkRouteCatalog, BenchmarkSuite, ResolvedBenchmarkPlan
from .suites import resolve_suite


def resolve_execution_plan(suite: BenchmarkSuite, catalog: BenchmarkRouteCatalog) -> ResolvedBenchmarkPlan:
    """Resolve exactly named arms; no input I/O, provider calls or persistence."""
    plan = resolve_suite(
        suite, catalog,
        max_cases=get_benchmark_max_cases(),
        max_configurations=get_benchmark_max_configurations(),
        max_repetitions=get_benchmark_max_repetitions(),
        max_cells=get_benchmark_max_cells(),
    )
    if any(case.target.kind == "agent" and not (case.user_query or "").strip() for case in plan.cases):
        raise BenchmarkCatalogError("Agent benchmark cases require an explicit curator query")
    return plan
