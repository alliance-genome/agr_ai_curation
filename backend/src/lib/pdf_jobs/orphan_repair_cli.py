"""Manual entry point for pending PDF documents that have no durable job."""

from __future__ import annotations

import argparse
import json

from src.lib.pdf_jobs.service import reconcile_pending_documents_without_jobs


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report or reconcile aged pending PDF documents that have no durable "
            "processing job. The default mode comes from "
            "PDF_NO_JOB_ORPHAN_REPAIR_APPLY and is dry-run by default."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        dest="apply",
        help="Create failed repair jobs and reconcile qualifying documents.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_false",
        dest="apply",
        help="Report qualifying documents without changing the database.",
    )
    parser.set_defaults(apply=None)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the content-free repair report as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = reconcile_pending_documents_without_jobs(apply=args.apply)
    payload = summary.to_json()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("Pending PDF no-job orphan reconciliation")
        print(f"  dry_run: {summary.dry_run}")
        print(f"  cutoff: {payload['cutoff']}")
        print(f"  batch_size: {summary.batch_size}")
        print(f"  qualifying_count: {summary.qualifying_count}")
        for record in summary.records:
            print(
                f"  - document_id={record.document_id} status={record.status} "
                f"job_id={record.job_id or '-'} reason={record.reason}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
