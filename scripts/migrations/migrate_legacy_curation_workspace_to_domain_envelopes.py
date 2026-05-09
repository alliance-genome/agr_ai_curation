#!/usr/bin/env python3
"""Run the one-off legacy curation workspace domain-envelope migration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_PROJECT_KEY = "agr_ai_curation"
DEFAULT_LEGACY_DOMAIN_PACK_ID = "legacy_curation_workspace"
DEFAULT_LEGACY_DOMAIN_PACK_VERSION = "0.7.0"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert retained legacy AI Curation workspace rows into domain envelopes, "
            "domain projections, and append-only history."
        )
    )
    parser.add_argument("--project-key", default=DEFAULT_PROJECT_KEY)
    parser.add_argument("--domain-pack-id", default=DEFAULT_LEGACY_DOMAIN_PACK_ID)
    parser.add_argument(
        "--domain-pack-version",
        default=DEFAULT_LEGACY_DOMAIN_PACK_VERSION,
    )
    parser.add_argument(
        "--actor-id",
        default="legacy_curation_workspace_migration_script",
        help="Actor identifier recorded on migration history events.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate migration envelopes without writing database rows.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the full migration summary as JSON.",
    )
    return parser


def _print_text_summary(summary) -> None:
    print("Legacy curation workspace migration summary")
    print(f"  dry_run: {summary.dry_run}")
    print(f"  inspected_sessions: {summary.inspected_sessions}")
    print(f"  inspected_extraction_results: {summary.inspected_extraction_results}")
    print(f"  migrated_envelopes: {summary.migrated_envelopes}")
    print(f"  would_migrate_envelopes: {summary.would_migrate_envelopes}")
    print(
        "  skipped_already_migrated_sources: "
        f"{summary.skipped_already_migrated_sources}"
    )
    print(
        "  linked_candidate_projection_refs: "
        f"{summary.linked_candidate_projection_refs}"
    )
    print(f"  blocker_count: {summary.blocker_count}")
    if summary.blockers:
        print("Migration blockers:")
        for blocker in summary.blockers:
            print(f"  - {blocker.source_table}/{blocker.source_id}: {blocker.reason}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    from src.lib.domain_envelopes.migration import (  # noqa: E402
        LegacyCurationWorkspaceMigrationOptions,
        migrate_legacy_curation_workspace_to_domain_envelopes,
    )
    from src.models.sql.database import SessionLocal  # noqa: E402

    options = LegacyCurationWorkspaceMigrationOptions(
        project_key=args.project_key,
        domain_pack_id=args.domain_pack_id,
        domain_pack_version=args.domain_pack_version,
        actor_id=args.actor_id,
        dry_run=args.dry_run,
    )

    db = SessionLocal()
    try:
        summary = migrate_legacy_curation_workspace_to_domain_envelopes(
            db,
            options=options,
        )
    finally:
        db.close()

    if args.json:
        print(json.dumps(summary.to_json(), indent=2, sort_keys=True))
    else:
        _print_text_summary(summary)

    return 1 if summary.has_blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
