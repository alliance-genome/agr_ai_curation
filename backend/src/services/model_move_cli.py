"""Move saved agents, pinned flow steps and editable rows off retired models.

The standing command for every model retirement; the reviewed moves file under
``config/model_moves/`` names each retired model and its replacement. Run it from
the backend image after the catalog no longer offers the retired models, after the
deployment's model settings name only catalog models, and before the backend starts.

    python -m src.services.model_move_cli --moves FILE --list-flow-owners
    python -m src.services.model_move_cli --moves FILE --provider-groups FILE [--apply]
    python -m src.services.model_move_cli --moves FILE --verify

Without ``--apply`` the whole move runs and is rolled back (a dry run). Exit codes:
0 done (or ``--verify`` found nothing left), 1 refused to start (nothing written),
3 something was refused and left unchanged (or ``--verify`` found something left).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    parser.add_argument("--moves", type=Path, required=True, help="Reviewed moves file (YAML).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list-flow-owners", action="store_true",
                      help="Print {user_id: auth_sub} for every owner whose flows are re-pinned.")
    mode.add_argument("--provider-groups", type=Path,
                      help='JSON {"<user_id>": ["<identity-provider group>", ...]} for every flow owner; '
                           "mapped to active groups as a login maps them. Flows are saved as their owner.")
    mode.add_argument("--verify", action="store_true",
                      help="Read-only: count what still names a retired model.")
    parser.add_argument("--apply", action="store_true", help="Commit; without it the run is rolled back.")
    return parser


def _owner_groups(path: Path) -> dict[int, list[str]]:
    from src.lib.group_rules import get_groups_from_provider_groups

    raw = json.loads(path.read_text())
    if not isinstance(raw, dict) or not all(
            isinstance(groups, list) and all(isinstance(group, str) for group in groups)
            for groups in raw.values()):
        raise ValueError('The provider groups file must be {"<user_id>": ["<group>", ...]}')
    return {int(user_id): get_groups_from_provider_groups(groups) for user_id, groups in raw.items()}


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _refuse(detail: str) -> int:
    print(json.dumps({"refused": detail}, indent=2), file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.apply and args.provider_groups is None:
        parser.error("--apply needs --provider-groups")
    from src.lib.agent_studio import retired_model_conversion as conversion
    from src.models.sql import database
    from src.models.sql.user import User
    from sqlalchemy import select

    try:
        moves = conversion.load_moves(args.moves)
        owner_groups = _owner_groups(args.provider_groups) if args.provider_groups else {}
    except (OSError, ValueError) as error:
        return _refuse(str(error))
    with database.SessionLocal() as db:
        if args.verify:
            report = conversion.verify(db, moves)
            _print(report)
            return 0 if report["blocking"] == 0 else 3
        if args.list_flow_owners:
            owners = conversion.flow_owners(
                db, frozenset(model_id for move in moves for model_id in move.from_ids))
            subjects = dict(db.execute(select(User.id, User.auth_sub).where(User.id.in_(owners))).all())
            _print({"flow_owners": {str(user_id): subjects.get(user_id) for user_id in owners}})
            return 0
        try:
            report = conversion.run_moves(db, moves, owner_groups=owner_groups)
        except conversion.ConversionRefused as error:
            db.rollback()
            return _refuse(str(error))
        if args.apply:
            db.commit()
        else:
            db.rollback()
    _print({"committed": args.apply,
            "owner_groups": {str(user_id): groups for user_id, groups in sorted(owner_groups.items())},
            **report})
    counts = report["counts"]
    refused = counts["agents_refused"] + counts["flows_refused"] + counts["editable_rows_refused"]
    return 3 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
