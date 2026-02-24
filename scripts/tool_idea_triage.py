#!/usr/bin/env python3
"""Developer triage CLI for Agent Workshop tool idea requests.

Usage examples:
  python scripts/tool_idea_triage.py list --status submitted --limit 20
  python scripts/tool_idea_triage.py queue
  python scripts/tool_idea_triage.py update <request_id> --status reviewed --notes "Looks feasible"
"""

import argparse
import os
import sys
import uuid
from datetime import datetime
from typing import Iterable, List

from sqlalchemy import select

# Add backend to path to allow imports when running from repo root.
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))

from src.models.sql.database import SessionLocal  # noqa: E402
from src.models.sql.tool_idea_request import ToolIdeaRequest  # noqa: E402


VALID_STATUSES = ["submitted", "reviewed", "in_progress", "completed", "declined"]
OPEN_STATUSES = ["submitted", "reviewed", "in_progress"]


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M")


def _print_requests(rows: Iterable[ToolIdeaRequest]) -> None:
    rows = list(rows)
    if not rows:
        print("No matching tool idea requests found.")
        return

    print(
        f"{'ID':36}  {'Status':12}  {'User':>4}  {'Created':16}  {'Title'}"
    )
    print("-" * 96)
    for row in rows:
        print(
            f"{str(row.id):36}  {row.status:12}  {row.user_id:>4}  "
            f"{_format_timestamp(row.created_at):16}  {row.title}"
        )
        if row.developer_notes:
            print(f"    Notes: {row.developer_notes}")
        if row.resulting_tool_key:
            print(f"    Tool:  {row.resulting_tool_key}")


def cmd_list(statuses: List[str], limit: int, project_id: str | None) -> int:
    session = SessionLocal()
    try:
        stmt = select(ToolIdeaRequest).order_by(
            ToolIdeaRequest.created_at.desc(),
            ToolIdeaRequest.updated_at.desc(),
        )
        if statuses:
            stmt = stmt.where(ToolIdeaRequest.status.in_(statuses))
        if project_id:
            stmt = stmt.where(ToolIdeaRequest.project_id == uuid.UUID(project_id))
        if limit > 0:
            stmt = stmt.limit(limit)

        rows = session.execute(stmt).scalars().all()
        _print_requests(rows)
        return 0
    finally:
        session.close()


def cmd_update(
    request_id: str,
    status: str | None,
    notes: str | None,
    resulting_tool_key: str | None,
) -> int:
    session = SessionLocal()
    try:
        try:
            request_uuid = uuid.UUID(request_id)
        except ValueError:
            print(f"Invalid request_id: {request_id}")
            return 2

        record = (
            session.query(ToolIdeaRequest)
            .filter(ToolIdeaRequest.id == request_uuid)
            .first()
        )
        if not record:
            print(f"Tool idea request not found: {request_id}")
            return 1

        changed = False
        if status and record.status != status:
            record.status = status
            changed = True
        if notes is not None and record.developer_notes != notes:
            record.developer_notes = notes
            changed = True
        if resulting_tool_key is not None and record.resulting_tool_key != resulting_tool_key:
            record.resulting_tool_key = resulting_tool_key
            changed = True

        if not changed:
            print("No updates requested.")
            return 0

        session.add(record)
        session.commit()
        session.refresh(record)

        print("Updated tool idea request:")
        _print_requests([record])
        return 0
    finally:
        session.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Triage tool idea requests submitted from Agent Workshop."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List tool idea requests")
    list_parser.add_argument(
        "--status",
        action="append",
        choices=VALID_STATUSES,
        default=[],
        help="Filter by one or more statuses (repeat flag to add more).",
    )
    list_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum rows to display (default: 50). Use 0 for no limit.",
    )
    list_parser.add_argument(
        "--project-id",
        type=str,
        default=None,
        help="Optional project UUID filter.",
    )

    queue_parser = subparsers.add_parser(
        "queue",
        help="List open queue items (submitted/reviewed/in_progress).",
    )
    queue_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum rows to display (default: 50). Use 0 for no limit.",
    )
    queue_parser.add_argument(
        "--project-id",
        type=str,
        default=None,
        help="Optional project UUID filter.",
    )

    update_parser = subparsers.add_parser("update", help="Update one request")
    update_parser.add_argument("request_id", type=str, help="Tool idea request UUID")
    update_parser.add_argument(
        "--status",
        choices=VALID_STATUSES,
        default=None,
        help="New request status.",
    )
    update_parser.add_argument(
        "--notes",
        type=str,
        default=None,
        help="Developer notes for the curator-visible status panel.",
    )
    update_parser.add_argument(
        "--resulting-tool-key",
        type=str,
        default=None,
        help="Tool key shipped for this request (if completed).",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "list":
        return cmd_list(args.status, args.limit, args.project_id)
    if args.command == "queue":
        return cmd_list(OPEN_STATUSES, args.limit, args.project_id)
    if args.command == "update":
        if args.status is None and args.notes is None and args.resulting_tool_key is None:
            print("No updates specified. Provide at least one of --status, --notes, --resulting-tool-key.")
            return 2
        return cmd_update(
            request_id=args.request_id,
            status=args.status,
            notes=args.notes,
            resulting_tool_key=args.resulting_tool_key,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
