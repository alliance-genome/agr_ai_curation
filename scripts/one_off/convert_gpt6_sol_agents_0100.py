#!/usr/bin/env python3
"""One-off: move saved custom agents and pinned flow steps from GPT-6 Sol to GPT-6.1 Sol.

See scripts/README.md ("One-off data migrations"). A dry run unless --apply is
given: the whole conversion runs and is rolled back. Run it after `alembic
upgrade head` (s6b7c8d9e0f1 moves the editable agent rows).
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

RETIRED_MODEL_ID = "gpt-6-sol"
TARGET_MODEL_ID = "gpt-6.1-sol"
# The same mapping as Alembic s6b7c8d9e0f1: GPT-6.1 Sol offers low, medium and high.
REASONING_MAP = {"xhigh": "high"}
NOTES = ("v0.10.0 release: GPT-6 Sol was retired; this copy of the saved version runs on "
         "GPT-6.1 Sol (xhigh reasoning becomes high). Nothing else changed.")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    parser.add_argument("--owner-groups", type=Path, required=True,
                        help='JSON {"<user_id>": ["<group id>", ...]}: the reviewed active groups of '
                             "every owner whose flows are re-pinned (flows are saved as their owner).")
    parser.add_argument("--apply", action="store_true", help="Commit; without it the run is rolled back.")
    return parser


def _owner_groups(path: Path) -> dict[int, list[str]]:
    from src.lib.agent_access import normalize_allowed_group_ids

    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("The owner groups file must be a JSON object")
    return {int(user_id): normalize_allowed_group_ids(groups, field_name=f"owner {user_id}")
            for user_id, groups in raw.items()}


def _refuse(detail) -> int:
    print(json.dumps({"refused": detail}, indent=2, default=str), file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    from src.lib.agent_studio import retired_model_conversion as conversion
    from src.models.sql.database import SessionLocal

    try:
        owner_groups = _owner_groups(args.owner_groups)
    except (OSError, ValueError) as error:
        return _refuse(str(error))
    with SessionLocal() as db:
        try:
            report = conversion.convert(
                db, retired_model_id=RETIRED_MODEL_ID, target_model_id=TARGET_MODEL_ID,
                reasoning_map=REASONING_MAP, owner_groups=owner_groups, notes=NOTES)
        except conversion.ConversionRefused as error:
            db.rollback()
            return _refuse(str(error))
        if args.apply:
            db.commit()
        else:
            db.rollback()
    print(json.dumps({"committed": args.apply, **report}, indent=2, default=str))
    # Refused items are left unchanged and listed; a re-run converts what became possible.
    refused = report["counts"]["agents_refused"] + report["counts"]["flows_refused"]
    return 3 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
