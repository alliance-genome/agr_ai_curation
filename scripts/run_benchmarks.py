#!/usr/bin/env python3
"""Thin developer client for the deployment-local Benchmark API."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from src.lib.benchmark_cli.commands import main  # noqa: E402  # pyright: ignore[reportMissingImports]


if __name__ == "__main__":
    raise SystemExit(main())
