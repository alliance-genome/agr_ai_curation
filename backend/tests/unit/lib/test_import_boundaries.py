"""Regression tests for backend library import boundaries."""

from pathlib import Path
import subprocess
import sys


def test_batch_processor_imports_in_fresh_interpreter() -> None:
    """Batch imports must not depend on another module having loaded flows first."""
    backend_root = Path(__file__).resolve().parents[3]

    subprocess.run(
        [sys.executable, "-c", "from src.lib.batch import processor"],
        cwd=backend_root,
        check=True,
    )
