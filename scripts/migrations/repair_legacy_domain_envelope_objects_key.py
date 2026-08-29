#!/usr/bin/env python3
"""Audit or repair legacy ``objects`` keys in persisted domain envelopes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit or atomically rename legacy domain-envelope 'objects' keys to "
            "'extracted_objects'. The default is a read-only dry-run."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the repair in one transaction (default: dry-run only).",
    )
    parser.add_argument("--expect-envelopes", type=int)
    parser.add_argument("--expect-candidate-references", type=int)
    parser.add_argument("--expect-sessions", type=int)
    parser.add_argument("--expect-objects", type=int)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the content-free audit summary as JSON.",
    )
    return parser


def _expectations_from_args(parser: argparse.ArgumentParser, args):
    from src.lib.domain_envelopes.legacy_payload_repair import (
        LegacyDomainEnvelopeRepairExpectations,
    )

    values = (
        args.expect_envelopes,
        args.expect_candidate_references,
        args.expect_sessions,
        args.expect_objects,
    )
    if args.apply and any(value is None for value in values):
        parser.error(
            "--apply requires --expect-envelopes, --expect-candidate-references, "
            "--expect-sessions, and --expect-objects from a fresh dry-run"
        )
    if not args.apply:
        if any(value is not None for value in values):
            parser.error("--expect-* options are valid only with --apply")
        return None
    return LegacyDomainEnvelopeRepairExpectations(
        envelopes=args.expect_envelopes,
        candidate_references=args.expect_candidate_references,
        sessions=args.expect_sessions,
        objects=args.expect_objects,
    )


def _print_text_summary(summary) -> None:
    print("Legacy domain-envelope payload repair")
    for key, value in summary.to_json().items():
        if key == "envelope_ids":
            print("  envelope_ids:")
            for envelope_id in value:
                print(f"    - {envelope_id}")
        else:
            print(f"  {key}: {value}")
    if not summary.applied:
        print("No rows changed. Re-run with --apply and all printed counts to repair.")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    from src.lib.domain_envelopes.legacy_payload_repair import (
        repair_legacy_domain_envelope_payloads,
    )
    from src.models.sql.database import SessionLocal

    expectations = _expectations_from_args(parser, args)
    db = SessionLocal()
    try:
        summary = repair_legacy_domain_envelope_payloads(
            db,
            apply=args.apply,
            expectations=expectations,
        )
        if args.apply:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if args.json:
        print(json.dumps(summary.to_json(), indent=2, sort_keys=True))
    else:
        _print_text_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
