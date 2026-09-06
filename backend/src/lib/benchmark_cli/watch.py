"""Authorized observation recovery without submission or cancellation."""

import re
import time
import math
from collections.abc import Callable
from typing import Any

from src.lib.openai_agents.config import (
    get_benchmark_cli_event_reconnect_attempts,
    get_benchmark_cli_poll_interval_seconds,
    get_benchmark_cli_poll_timeout_seconds,
)

from .client import BenchmarkClient, ClientError, ExitCode

TERMINAL = {
    "completed": ExitCode.OK,
    "completed_with_failures": ExitCode.PARTIAL_FAILURE,
    "failed": ExitCode.PARTIAL_FAILURE,
    "cancelled": ExitCode.CANCELLED,
}


def _cursor(value: Any, job_id: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(re.escape(job_id) + r":[0-9]+", value):
        raise ClientError(ExitCode.SERVER, "Server returned an invalid event cursor")
    return value


def watch(client: BenchmarkClient, job_id: str, emit: Callable[[dict], None], *, cursor: str | None = None, polling: bool = False) -> ExitCode:
    interval = get_benchmark_cli_poll_interval_seconds()
    poll_timeout = get_benchmark_cli_poll_timeout_seconds()
    if not all(math.isfinite(value) and value > 0 for value in (interval, poll_timeout)):
        raise ClientError(ExitCode.VALIDATION, "Observation timings must be finite and positive")
    if cursor is not None:
        try:
            cursor = _cursor(cursor, job_id)
        except ClientError:
            raise ClientError(ExitCode.VALIDATION, "Invalid last-event-id for this job") from None
    reconciled = False

    def show(summary):
        if not isinstance(summary, dict) or summary.get("id") != job_id or not isinstance(summary.get("status"), str):
            raise ClientError(ExitCode.SERVER, "Server returned invalid job status")
        emit({"event": "job.status", "job_id": job_id, "last_event_id": cursor, "summary": summary})
        return TERMINAL.get(summary["status"])

    def reconcile():
        nonlocal reconciled
        reconciled = False
        try:
            result = client.request("GET", f"/api/v1/benchmarks/jobs/{job_id}")
        except ClientError as error:
            if error.code == ExitCode.TRANSPORT:
                return None
            raise
        terminal = show(result.get("summary") if isinstance(result, dict) else None)
        reconciled = True
        return terminal

    for attempt in range(get_benchmark_cli_event_reconnect_attempts() + 1):
        resume_after = None
        try:
            for event, identifier, payload in client.events(job_id, cursor):
                if event == "benchmark.event":
                    cursor = _cursor(identifier, job_id)
                    emit({"event": "benchmark.event", "job_id": job_id, "last_event_id": cursor})
                elif event == "job.status":
                    terminal = show(payload)
                    if terminal is not None:
                        return terminal
                elif event == "stream.error":
                    if isinstance(payload, dict) and payload.get("code") == "event_history_expired":
                        resume_after = _cursor(payload.get("resume_after"), job_id)
                        break
                    if isinstance(payload, dict) and payload.get("code") in {"authorization_required", "capability_required"}:
                        raise ClientError(ExitCode.AUTHORIZATION, "Event observation is not authorized")
                    raise ClientError(ExitCode.TRANSPORT, "Event observation interrupted")
        except ClientError as error:
            if error.resume_after is not None:
                resume_after = _cursor(error.resume_after, job_id)
            elif error.code != ExitCode.TRANSPORT:
                raise
        # EOF is not success. Always read fresh authorized status before a reconnect.
        terminal = reconcile()
        if terminal is not None:
            return terminal
        if resume_after is not None and reconciled:
            cursor = resume_after
        emit({"event": "connection.interrupted", "job_id": job_id, "last_event_id": cursor})
        if attempt < get_benchmark_cli_event_reconnect_attempts():
            time.sleep(interval)
    if not polling:
        raise ClientError(ExitCode.TRANSPORT, "Event reconnect limit reached; resume observation explicitly")
    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        terminal = reconcile()
        if terminal is not None:
            return terminal
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    raise ClientError(ExitCode.TRANSPORT, "Polling observation timed out; server work was not cancelled")
