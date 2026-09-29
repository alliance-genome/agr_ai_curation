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

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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

    if args.command != "inventory":
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
        if args.command == "inventory":
            db.execute(text("SET TRANSACTION READ ONLY"))
            print(json.dumps(migration.inventory(db), indent=2, sort_keys=True))
            db.rollback()
            return 0
        try:
            if args.command == "apply":
                outcome = migration.apply(db, plan)
                report = outcome.model_dump(mode="json")
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
                handle.write(outcome.model_dump_json(indent=2))
            try:
                db.commit()
            except BaseException:
                args.result.unlink()
                raise
        else:
            db.commit()
        print(json.dumps({"committed": args.commit, args.command: report}, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
