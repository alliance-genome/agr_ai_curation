"""Add private/project flow sharing.

Revision ID: o1p2q3r4s5t6
Revises: n0o1p2q3r4s5
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "o1p2q3r4s5t6"
down_revision = "n0o1p2q3r4s5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("curation_flows", sa.Column(
        "visibility", sa.String(20), nullable=False, server_default="private"
    ))
    op.add_column("curation_flows", sa.Column(
        "project_id", postgresql.UUID(as_uuid=True), nullable=True
    ))
    op.add_column("curation_flows", sa.Column(
        "shared_at", sa.DateTime(timezone=True), nullable=True
    ))
    op.create_foreign_key("fk_flows_project", "curation_flows", "projects", ["project_id"], ["id"])
    op.create_check_constraint("ck_flows_visibility", "curation_flows", "visibility IN ('private', 'project')")
    op.create_check_constraint(
        "ck_flows_visibility_project", "curation_flows",
        "(visibility = 'private' AND project_id IS NULL AND shared_at IS NULL) OR "
        "(visibility = 'project' AND project_id IS NOT NULL AND shared_at IS NOT NULL)",
    )
    op.create_index(
        "idx_curation_flows_project_active_updated", "curation_flows",
        ["project_id", "updated_at"],
        postgresql_where=sa.text("is_active IS TRUE AND visibility = 'project'"),
    )


def downgrade():
    op.drop_index("idx_curation_flows_project_active_updated", table_name="curation_flows")
    op.drop_constraint("ck_flows_visibility_project", "curation_flows", type_="check")
    op.drop_constraint("ck_flows_visibility", "curation_flows", type_="check")
    op.drop_constraint("fk_flows_project", "curation_flows", type_="foreignkey")
    op.drop_column("curation_flows", "shared_at")
    op.drop_column("curation_flows", "project_id")
    op.drop_column("curation_flows", "visibility")
