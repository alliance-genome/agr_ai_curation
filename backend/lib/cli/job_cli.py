"""Command line utilities for monitoring the embedding job queue."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

from sqlalchemy import create_engine, func
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.models import EmbeddingJob, JobStatus, JobType


DEFAULT_DB_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("TEST_DATABASE_URL")
    or "sqlite:///./test_database.db"
)


@contextmanager
def _session_scope(engine: Engine) -> Iterable[Session]:
    """Provide a transactional scope for CLI commands."""

    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor embedding job queue status and activity"
    )
    parser.add_argument(
        "--database-url",
        default=DEFAULT_DB_URL,
        help="Database connection string (defaults to DATABASE_URL/TEST_DATABASE_URL)",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("summary", help="Show queue summary and active workers")

    list_parser = subparsers.add_parser("list", help="List recent jobs")
    list_parser.add_argument(
        "--status",
        choices=[status.value for status in JobStatus],
        nargs="*",
        help="Filter by one or more job statuses",
    )
    list_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of jobs to display (default: 20)",
    )
    list_parser.add_argument(
        "--include-error",
        action="store_true",
        help="Include truncated error logs in the output",
    )

    return parser.parse_args(list(argv))


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _fetch_summary(session: Session) -> Dict[str, Any]:
    counts = (
        session.query(EmbeddingJob.status, func.count())
        .group_by(EmbeddingJob.status)
        .all()
    )
    counts_sorted = sorted(
        counts,
        key=lambda item: (
            item[0].value if isinstance(item[0], JobStatus) else str(item[0])
        ),
    )
    total = sum(count for _, count in counts_sorted)

    pending_job = (
        session.query(EmbeddingJob)
        .filter(EmbeddingJob.status == JobStatus.PENDING)
        .order_by(EmbeddingJob.created_at.asc())
        .first()
    )

    running_workers = (
        session.query(EmbeddingJob.worker_id)
        .filter(
            EmbeddingJob.status == JobStatus.RUNNING, EmbeddingJob.worker_id != None
        )  # noqa: E711
        .distinct()
        .all()
    )

    return {
        "total_jobs": total,
        "by_status": [
            {
                "status": (
                    status.value if isinstance(status, JobStatus) else str(status)
                ),
                "count": count,
            }
            for status, count in counts_sorted
        ],
        "oldest_pending": (
            None
            if pending_job is None
            else {
                "job_id": str(pending_job.id),
                "created_at": _serialize_datetime(pending_job.created_at),
                "age_seconds": _compute_age_seconds(pending_job.created_at),
                "priority": pending_job.priority,
            }
        ),
        "active_workers": [worker_id for (worker_id,) in running_workers if worker_id],
    }


def _compute_age_seconds(created_at: datetime | None) -> float | None:
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created = created_at.replace(tzinfo=timezone.utc)
    else:
        created = created_at.astimezone(timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds()


def _serialize_job(job: EmbeddingJob, include_error: bool) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": str(job.id),
        "pdf_id": str(job.pdf_id),
        "status": (
            job.status.value if isinstance(job.status, JobStatus) else str(job.status)
        ),
        "job_type": (
            job.job_type.value
            if isinstance(job.job_type, JobType)
            else str(job.job_type)
        ),
        "priority": job.priority,
        "retry_count": job.retry_count,
        "progress": job.progress,
        "worker_id": job.worker_id,
        "created_at": _serialize_datetime(job.created_at),
        "started_at": _serialize_datetime(job.started_at),
        "completed_at": _serialize_datetime(job.completed_at),
    }

    if include_error and job.error_log:
        payload["error_log"] = job.error_log[:240]

    return payload


def _fetch_jobs(
    session: Session, statuses: Sequence[str] | None, limit: int
) -> List[EmbeddingJob]:
    query = session.query(EmbeddingJob).order_by(EmbeddingJob.created_at.desc())
    if statuses:
        enum_statuses = [JobStatus(status) for status in statuses]
        query = query.filter(EmbeddingJob.status.in_(enum_statuses))
    return query.limit(limit).all()


def _print_summary_table(summary: Dict[str, Any]) -> None:
    sys.stdout.write(f"Total jobs: {summary['total_jobs']}\n")
    if summary["by_status"]:
        sys.stdout.write("By status:\n")
        for entry in summary["by_status"]:
            sys.stdout.write(f"  - {entry['status']}: {entry['count']}\n")
    else:
        sys.stdout.write("No jobs found.\n")

    if summary["oldest_pending"]:
        pending = summary["oldest_pending"]
        sys.stdout.write(
            "Oldest pending: {job_id} (priority {priority}) pending since {created_at}\n".format(
                job_id=pending["job_id"],
                priority=pending["priority"],
                created_at=pending["created_at"] or "unknown",
            )
        )
    else:
        sys.stdout.write("No pending jobs.\n")

    workers = summary["active_workers"]
    if workers:
        worker_list = ", ".join(sorted(workers))
        sys.stdout.write(f"Active workers: {worker_list}\n")
    else:
        sys.stdout.write("No active workers.\n")


def _print_jobs_table(jobs: List[Dict[str, Any]], include_error: bool) -> None:
    if not jobs:
        sys.stdout.write("No jobs matched the filters.\n")
        return

    columns = [
        "id",
        "status",
        "job_type",
        "priority",
        "progress",
        "worker_id",
        "created_at",
    ]
    if include_error:
        columns.append("error_log")

    widths = {
        col: max(len(col), *(len(str(job.get(col, ""))) for job in jobs))
        for col in columns
    }

    header = "  ".join(f"{col.upper():{widths[col]}}" for col in columns)
    sys.stdout.write(header + "\n")
    sys.stdout.write("  ".join("-" * widths[col] for col in columns) + "\n")

    for job in jobs:
        row = []
        for col in columns:
            value = job.get(col, "")
            if value is None:
                value = ""
            row.append(f"{str(value):{widths[col]}}")
        sys.stdout.write("  ".join(row) + "\n")


def _print_output(payload: Any, output_format: str, include_error: bool) -> None:
    if output_format == "json":
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    if isinstance(payload, dict):
        if "by_status" in payload:
            _print_summary_table(payload)
        else:
            sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return

    if isinstance(payload, list):
        _print_jobs_table(payload, include_error)
        return

    sys.stdout.write(str(payload) + "\n")


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv or sys.argv[1:])

    try:
        engine = create_engine(args.database_url)
    except OperationalError as exc:  # pragma: no cover - connection string errors
        sys.stderr.write(f"Failed to create engine: {exc}\n")
        sys.exit(1)

    try:
        with _session_scope(engine) as session:
            if args.command == "summary":
                summary = _fetch_summary(session)
                _print_output(summary, args.format, include_error=False)
            elif args.command == "list":
                jobs = _fetch_jobs(session, args.status, args.limit)
                serialized = [
                    _serialize_job(job, include_error=args.include_error)
                    for job in jobs
                ]
                _print_output(serialized, args.format, include_error=args.include_error)
            else:  # pragma: no cover - defensive
                sys.stderr.write(f"Unknown command: {args.command}\n")
                sys.exit(2)
    except OperationalError as exc:
        sys.stderr.write(f"Database error: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
