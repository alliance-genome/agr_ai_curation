"""Remove exact generated metadata execution groups, retaining full flow audit.

Drain flow/review workers before upgrading. Frozen historical benchmark captures
are intentionally immutable; capture fresh definitions for subsequent runs.
"""
import logging

from alembic import op

from src.lib.flows.unbound_metadata_repair import migrate_metadata_groups

revision = "f4a8b0c2d3e5"
down_revision = "e3f7a9b1c2d4"
branch_labels = None
depends_on = None


def upgrade():
    report = migrate_metadata_groups(op.get_bind(), apply=True)
    logging.getLogger("alembic.runtime.migration").info(
        "Metadata-group repair inventoried %s flows, repaired %s groups; %s unmatched groups retained",
        report["flow_count"], sum(len(r["removed"]) for r in report["flows"]),
        sum(len(r["unmatched"]) for r in report["flows"]),
    )


def downgrade():
    raise RuntimeError("Forward-only flow repair; original definitions remain in audit_log")
