"""Shared errors for the execution-only benchmark API."""


class BenchmarkCatalogError(ValueError):
    """A suite, catalog, or resolved plan is invalid."""
