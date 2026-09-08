"""CLI argument and API command mapping, independent of execution internals."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID
from typing import NoReturn

from src.lib.openai_agents.config import get_benchmark_admission_max_bytes

from .client import BenchmarkClient, ClientError, Credentials, ExitCode
from .watch import watch

PREFIX = "/api/v1/benchmarks"


class SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's default error includes supplied arguments, potentially secrets.
        raise ClientError(ExitCode.VALIDATION, "Invalid arguments; use --help for command syntax")


def build_parser() -> argparse.ArgumentParser:
    parser = SafeParser(description="Use the asynchronous Benchmark API without running agents locally")
    parser.add_argument("--base-url", default=os.getenv("BENCHMARK_API_URL"))
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--access-token-env", default="BENCHMARK_ACCESS_TOKEN", help="Environment variable containing orchestration access token")
    parser.add_argument("--curator-token-env", default="BENCHMARK_CURATOR_TOKEN", help="Environment variable containing target-human credential")
    parser.add_argument("--source-token-env", default="BENCHMARK_SOURCE_TOKEN", help="Environment variable containing source delegation credential")
    commands = parser.add_subparsers(dest="command", required=True)
    observer = commands.add_parser("watch", help="Watch durable progress without submitting work")
    observer.add_argument("job_id", type=UUID)
    observer.add_argument("--last-event-id")
    observer.add_argument("--poll-fallback", action="store_true")
    catalog = commands.add_parser("catalog", help="Get one catalog section page")
    catalog.add_argument("section", choices=["targets", "route_slots", "models"])
    suites = commands.add_parser("suites", help="List one checked-in suite page")
    suite = commands.add_parser("suite", help="Retrieve a checked-in suite")
    suite.add_argument("suite_id")
    for command, digest in [(catalog, "catalog_digest"), (suites, "suite_catalog_digest")]:
        command.add_argument("--limit", type=int)
        command.add_argument("--cursor")
        command.add_argument("--" + digest.replace("_", "-"))
    jobs = commands.add_parser("jobs", help="List one job-summary page")
    jobs.add_argument("--status")
    jobs.add_argument("--limit", type=int)
    jobs.add_argument("--cursor-created-at")
    jobs.add_argument("--cursor-job-id", type=UUID)
    cells = commands.add_parser("cells", help="List one cell-summary page")
    cells.add_argument("job_id", type=UUID)
    cells.add_argument("--limit", type=int)
    cells.add_argument("--cursor-position", type=int)
    cells.add_argument("--cursor-cell-id", type=UUID)
    for name in ("get", "cancel", "delete", "rerun"):
        command = commands.add_parser(name)
        command.add_argument("job_id", type=UUID)
        if name == "get":
            command.add_argument("--cell-id", type=UUID)
        if name == "delete":
            command.add_argument("--confirm", required=True, type=UUID, help="Repeat the exact terminal job ID to delete")
        if name == "rerun":
            command.add_argument("--cell-id", action="append", type=UUID, default=[])
            command.add_argument("--idempotency-key", required=True)
    for name in ("validate", "submit"):
        command = commands.add_parser(name)
        command.add_argument("--request", type=Path, required=True, help="JSON file with the exact API request body")
        if name == "submit":
            command.add_argument("--idempotency-key", required=True)
            command.add_argument("--delegate-source", action="store_true")
    return parser


def _read_body(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            data = handle.read(get_benchmark_admission_max_bytes() + 1)
        if len(data) > get_benchmark_admission_max_bytes():
            raise ValueError
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError):
        raise ClientError(ExitCode.VALIDATION, "Cannot read a bounded JSON request object") from None


def execute(args: argparse.Namespace, client: BenchmarkClient):
    name = args.command
    if name in {"catalog", "suites", "jobs", "cells"}:
        fields = {
            "catalog": ("section", "limit", "cursor", "catalog_digest"),
            "suites": ("limit", "cursor", "suite_catalog_digest"),
            "jobs": ("status", "limit", "cursor_created_at", "cursor_job_id"),
            "cells": ("limit", "cursor_position", "cursor_cell_id"),
        }[name]
        params = {field: str(getattr(args, field)) for field in fields if getattr(args, field) is not None}
        path = f"{PREFIX}/jobs/{args.job_id}/cells" if name == "cells" else f"{PREFIX}/{name}"
        return client.request("GET", path, params=params, human=name in {"catalog", "suites"})
    if name == "suite":
        return client.request("GET", f"{PREFIX}/suites/{args.suite_id}", human=True)
    if name == "validate":
        return client.request("POST", f"{PREFIX}/plans/validate", body=_read_body(args.request), human=True)
    if name in {"submit", "rerun"}:
        body = _read_body(args.request) if name == "submit" else {"cell_ids": [str(value) for value in args.cell_id]}
        path = f"{PREFIX}/jobs" if name == "submit" else f"{PREFIX}/jobs/{args.job_id}/rerun"
        # Explicit key is reusable; never generate a replacement on uncertainty.
        return client.request("POST", path, body=body, human=True,
                              source=name == "submit" and args.delegate_source,
                              idempotency_key=args.idempotency_key)
    path = f"{PREFIX}/jobs/{args.job_id}"
    if name == "get":
        if args.cell_id:
            path += f"/cells/{args.cell_id}"
        return client.request("GET", path)
    if name == "cancel":
        return client.request("POST", path + "/cancel")
    if name == "delete":
        if args.confirm != args.job_id:
            raise ClientError(ExitCode.VALIDATION, "Deletion confirmation must match the job ID")
        return client.request("DELETE", path)
    raise ClientError(ExitCode.VALIDATION, "Unknown command")


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if not args.base_url:
            raise ClientError(ExitCode.VALIDATION, "Set BENCHMARK_API_URL or --base-url")
        credentials = Credentials(os.getenv(args.access_token_env, ""), os.getenv(args.curator_token_env), os.getenv(args.source_token_env))
        with BenchmarkClient(args.base_url, credentials) as client:
            if args.command == "watch":
                def emit(event):
                    print(json.dumps({"api_origin": client.base_url, **event}, indent=None if args.json else 2), flush=True)
                return watch(client, str(args.job_id), emit, cursor=args.last_event_id, polling=args.poll_fallback)
            result = execute(args, client)
            output = {"api_origin": client.base_url, "command": args.command, "result": result}
            print(json.dumps(output, indent=None if args.json else 2, allow_nan=False))
        return ExitCode.OK
    except ClientError as error:
        print(str(error), file=sys.stderr)
        return error.code
    except KeyboardInterrupt:
        print("Observation interrupted; server work was not cancelled", file=sys.stderr)
        return ExitCode.TRANSPORT
