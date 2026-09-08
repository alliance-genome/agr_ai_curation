"""Content-free exception wrappers for benchmark failure boundaries."""


class BenchmarkOperationError(RuntimeError):
    """Safe operational failure with no provider text or SQL parameters."""


def sanitized_benchmark_error(operation: str, error_type: str) -> BenchmarkOperationError:
    """Accept only source-owned operation names and exception type names."""
    try:
        raise BenchmarkOperationError(f"Benchmark {operation} failed ({error_type})") from None
    except BenchmarkOperationError as sanitized:
        sanitized.__context__ = None
        sanitized.__cause__ = None
        return sanitized
