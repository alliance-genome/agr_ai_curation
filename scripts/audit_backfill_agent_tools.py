#!/usr/bin/env python3
"""Audit and optionally backfill missing custom-agent tools from template defaults."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List


REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))


def _collect_candidates(report: Dict[str, object], include_noncritical: bool) -> List[Dict[str, object]]:
    candidates: List[Dict[str, object]] = []
    for row in report.get("agents", []):
        if not isinstance(row, dict):
            continue
        if not bool(row.get("missing_tool_backfill_candidate")):
            continue
        is_critical = bool(row.get("critical_missing_tool_backfill_candidate"))
        if not include_noncritical and not is_critical:
            continue
        suggested_tool_ids = list(row.get("suggested_tool_ids") or [])
        if not suggested_tool_ids:
            continue
        candidates.append(
            {
                "agent_key": str(row.get("agent_key") or ""),
                "template_source": row.get("template_source"),
                "critical": is_critical,
                "suggested_tool_ids": suggested_tool_ids,
            }
        )
    return candidates


def _print_candidates(candidates: List[Dict[str, object]]) -> None:
    if not candidates:
        print("No backfill candidates found.")
        return
    print(f"Found {len(candidates)} backfill candidate(s):")
    for row in candidates:
        critical_label = "CRITICAL" if row["critical"] else "non-critical"
        tools_csv = ", ".join(row["suggested_tool_ids"])
        print(
            f"- {row['agent_key']} ({critical_label}) "
            f"template={row.get('template_source') or 'n/a'} "
            f"suggested_tools=[{tools_csv}]"
        )


def _apply_backfill(candidates: List[Dict[str, object]], agent_key_filters: List[str]) -> Dict[str, int]:
    from src.models.sql.agent import Agent as DBAgent
    from src.models.sql.database import SessionLocal

    candidate_by_key: Dict[str, Dict[str, object]] = {
        str(row["agent_key"]): row for row in candidates if row.get("agent_key")
    }
    if agent_key_filters:
        allowed = {item.strip() for item in agent_key_filters if item.strip()}
        candidate_by_key = {
            key: value for key, value in candidate_by_key.items() if key in allowed
        }

    if not candidate_by_key:
        return {"updated": 0, "skipped": 0}

    session = SessionLocal()
    updated = 0
    skipped = 0
    try:
        rows = (
            session.query(DBAgent)
            .filter(DBAgent.agent_key.in_(list(candidate_by_key.keys())))
            .filter(DBAgent.is_active == True)  # noqa: E712
            .all()
        )

        for row in rows:
            candidate = candidate_by_key.get(str(row.agent_key))
            if candidate is None:
                skipped += 1
                continue
            if str(row.visibility) not in {"private", "project"}:
                skipped += 1
                continue
            existing_tool_ids = list(row.tool_ids or [])
            if existing_tool_ids:
                skipped += 1
                continue

            suggested_tool_ids = list(candidate.get("suggested_tool_ids") or [])
            if not suggested_tool_ids:
                skipped += 1
                continue

            row.tool_ids = suggested_tool_ids
            updated += 1

        session.commit()
        return {"updated": updated, "skipped": skipped}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit and optionally backfill missing custom-agent tools from template defaults."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply updates to database (default is dry-run audit only).",
    )
    parser.add_argument(
        "--include-noncritical",
        action="store_true",
        help="Include non-critical template-tool drift candidates (default: critical only).",
    )
    parser.add_argument(
        "--agent-key",
        action="append",
        default=[],
        help="Limit apply scope to specific agent_key (can be repeated).",
    )
    args = parser.parse_args()

    from src.lib.agent_studio.runtime_validation import build_agent_runtime_report

    strict_mode = os.getenv("AGENT_RUNTIME_STRICT_MODE", "false")
    print(
        f"Building runtime report with strict_mode disabled for audit "
        f"(env AGENT_RUNTIME_STRICT_MODE={strict_mode})...",
        flush=True,
    )
    try:
        report = build_agent_runtime_report(strict_mode=False)
    except Exception as exc:
        print(
            "Failed to build agent runtime report. "
            "Check DATABASE_URL and database connectivity.",
            file=sys.stderr,
        )
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(
        "Report status:",
        report.get("status"),
        "| errors:",
        len(report.get("errors", [])),
        "| warnings:",
        len(report.get("warnings", [])),
    )
    print("Summary:", report.get("summary", {}))

    candidates = _collect_candidates(
        report,
        include_noncritical=bool(args.include_noncritical),
    )
    _print_candidates(candidates)

    if not args.apply:
        print("Dry-run complete. Re-run with --apply to perform backfill.")
        return

    result = _apply_backfill(candidates, args.agent_key or [])
    print(
        f"Backfill apply complete. Updated: {result['updated']} | Skipped: {result['skipped']}"
    )


if __name__ == "__main__":
    main()
