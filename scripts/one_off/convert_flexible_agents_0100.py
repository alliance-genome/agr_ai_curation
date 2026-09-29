#!/usr/bin/env python3
"""One-off: convert Flexible-extraction custom agents to Custom Output Structures.

See scripts/README.md ("One-off data migrations"). Every command is a dry run
unless --commit is given; inventory never writes. apply and rollback read only
the plan file whose sha256 digest was recorded at review (--plan-sha256).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Mounted at /app/scripts/one_off in the backend image (backend at /app/backend), or
# run from a checkout. Backend modules also import the runtime helpers under its
# src directory, which the production image does not put on PYTHONPATH.
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
for _import_root in (BACKEND_ROOT / "src", BACKEND_ROOT):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory", help="Read-only report and draft plans (JSON on stdout).")
    for name in ("apply", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("--plan", type=Path, required=True)
        command.add_argument("--plan-sha256", required=True,
                             help="sha256 of the plan file as reviewed with the agent's owner.")
        command.add_argument("--result", type=Path, required=True,
                             help="apply writes it (with --commit, never overwriting); rollback reads it.")
        command.add_argument("--commit", action="store_true")
    return parser


def _refuse(detail) -> int:
    print(json.dumps({"refused": detail}, indent=2, default=str), file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from fastapi import HTTPException
    from pydantic import ValidationError
    from sqlalchemy import text

    from src.lib.agent_studio import flexible_migration as migration
    from src.lib.agent_studio.custom_agent_service import CustomAgentError
    from src.models.sql.database import SessionLocal

    if args.command == "inventory":
        with SessionLocal() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            print(json.dumps(migration.inventory(db), indent=2, sort_keys=True))
            db.rollback()
            return 0

    try:
        plan = migration.load_reviewed_plan(args.plan.read_bytes(), args.plan_sha256)
        recorded = (migration.ConversionResult.model_validate_json(args.result.read_bytes())
                    if args.command == "rollback" else None)
    except ValidationError as error:
        return _refuse(error.errors(include_url=False, include_input=False))
    except (OSError, ValueError) as error:
        return _refuse(str(error))
    if args.command == "apply" and args.commit and args.result.exists():
        return _refuse(f"{args.result} already exists; it records an earlier apply")

    with SessionLocal() as db:
        try:
            if recorded is None:
                report = migration.apply(db, plan).model_dump(mode="json")
            else:
                report = [step.model_dump(mode="json")
                          for step in migration.rollback(db, plan, recorded)]
        except HTTPException as error:
            # The flow's own validation findings, as a curator save would report them.
            db.rollback()
            return _refuse(error.detail)
        except (ValueError, CustomAgentError) as error:
            db.rollback()
            return _refuse(str(error))
        if not args.commit:
            db.rollback()
        elif args.command == "apply":
            # Record the old and new revisions before committing, so a committed
            # conversion always has the result file its rollback needs.
            with args.result.open("x") as handle:
                handle.write(json.dumps(report, indent=2))
            try:
                db.commit()
            except Exception as error:
                # The commit may or may not have landed; keep the result file.
                print(json.dumps({"error": "commit outcome unknown; check the agent head before retrying",
                                  "result": str(args.result), "detail": str(error)}, indent=2),
                      file=sys.stderr)
                return 2
        else:
            db.commit()
        print(json.dumps({"committed": args.commit, args.command: report}, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
