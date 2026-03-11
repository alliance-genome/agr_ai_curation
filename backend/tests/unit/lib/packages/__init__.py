"""Unit tests for runtime package contract helpers."""

from pathlib import Path


def find_repo_root(start: Path) -> Path:
    """Resolve the repository root by walking upward to a known sentinel."""
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (candidate / "docker-compose.test.yml").exists():
            return candidate
        if (candidate / "backend").is_dir() and (candidate / "packages").is_dir():
            return candidate

    raise RuntimeError(f"Could not resolve repository root from {start}")
