"""Argument handling for the thin developer benchmark command."""

from __future__ import annotations

import argparse
import asyncio
import sys

from src.lib.openai_agents.config import get_benchmark_enabled

from .loader import BenchmarkCatalogError
from .models import BenchmarkRoute, BenchmarkSelection
from .runtime import build_default_service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or execute checked-in benchmarks"
    )
    parser.add_argument(
        "--profile", action="append", default=[], help="Profile ID (repeatable)"
    )
    parser.add_argument(
        "--case", action="append", default=[], dest="cases", help="Case ID (repeatable)"
    )
    parser.add_argument("--provider", help="Explicit route provider override")
    parser.add_argument("--model", help="Explicit route model override")
    parser.add_argument(
        "--validate", action="store_true", help="Validate and print the bounded matrix"
    )
    parser.add_argument("--dry-run", action="store_true", help="Alias for --validate")
    return parser


def _selection(args: argparse.Namespace) -> BenchmarkSelection:
    if bool(args.provider) != bool(args.model):
        raise BenchmarkCatalogError("--provider and --model must be supplied together")
    route = (
        BenchmarkRoute(provider=args.provider, model=args.model)
        if args.provider and args.model
        else None
    )
    return BenchmarkSelection(
        profile_ids=args.profile, case_ids=args.cases, route=route
    )


async def _run(args: argparse.Namespace) -> int:
    selection = _selection(args)
    service = build_default_service()
    if args.validate or args.dry_run:
        print(service.plan(selection).model_dump_json(indent=2))
        return 0
    if not get_benchmark_enabled():
        raise BenchmarkCatalogError(
            "Benchmark execution is disabled; set BENCHMARK_ENABLED=true"
        )
    response = await service.execute(selection)
    print(response.model_dump_json(indent=2))
    return 1 if any(run.status == "failed" for run in response.runs) else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (BenchmarkCatalogError, ValueError) as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 2
