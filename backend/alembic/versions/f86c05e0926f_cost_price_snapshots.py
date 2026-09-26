"""Immutable reviewed pricing snapshots, separate from immutable usage facts.

Revision ID: f86c05e0926f
Revises: e75c05e0926e
"""
from alembic import op
import sqlalchemy as sa

revision = "f86c05e0926f"
down_revision = "e75c05e0926e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("cost_price_snapshots",
                    sa.Column("id", sa.Text(), primary_key=True),
                    sa.Column("payload", sa.Text(), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.execute("""CREATE FUNCTION reject_cost_price_snapshot_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
          RAISE EXCEPTION 'Cost price snapshots are immutable';
        END $$""")
    op.execute("""CREATE TRIGGER cost_price_snapshot_immutable BEFORE UPDATE OR DELETE
        ON cost_price_snapshots FOR EACH ROW EXECUTE FUNCTION reject_cost_price_snapshot_mutation()""")


def downgrade():
    raise RuntimeError("Pricing provenance requires a forward migration; do not discard it")
