"""Add owned closed generic extraction profile revisions.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "generic_extraction_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
        ),
        sa.Column("visibility", sa.String(20), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("semantic_class", sa.Text(), nullable=False),
        sa.Column("head_revision", sa.Integer(), nullable=False),
        sa.Column(
            "archived", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "(visibility = 'private' AND project_id IS NULL) OR "
            "(visibility = 'project' AND project_id IS NOT NULL)",
            name="ck_generic_profile_visibility",
        ),
        sa.CheckConstraint(
            "head_revision > 0", name="ck_generic_profile_head_positive"
        ),
    )
    op.create_table(
        "generic_extraction_profile_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("generic_extraction_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(71), nullable=False),
        sa.Column("contract", JSONB(), nullable=False),
        sa.Column(
            "creator_id",
            sa.Integer(),
            sa.ForeignKey("users.user_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "profile_id", "revision", name="uq_generic_profile_revision"
        ),
        sa.UniqueConstraint(
            "id", "fingerprint", name="uq_generic_profile_revision_identity"
        ),
        sa.CheckConstraint("revision > 0", name="ck_generic_profile_revision_positive"),
        sa.CheckConstraint(
            "fingerprint ~ '^sha256:[a-f0-9]{64}$'",
            name="ck_generic_profile_fingerprint",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(contract) = 'object'",
            name="ck_generic_profile_contract_object",
        ),
    )
    op.create_foreign_key(
        "fk_generic_profile_head",
        "generic_extraction_profiles",
        "generic_extraction_profile_revisions",
        ["id", "head_revision"],
        ["profile_id", "revision"],
        deferrable=True,
        initially="DEFERRED",
        ondelete="NO ACTION",
    )
    op.create_index(
        "ix_generic_profiles_owner_archive",
        "generic_extraction_profiles",
        ["owner_id", "archived", "id"],
    )
    op.create_index(
        "ix_generic_profiles_project_archive",
        "generic_extraction_profiles",
        ["project_id", "archived", "id"],
    )
    op.create_index(
        "ix_generic_profile_revision_fingerprint",
        "generic_extraction_profile_revisions",
        ["profile_id", "fingerprint"],
    )
    op.execute("""
        CREATE FUNCTION reject_generic_profile_revision_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Generic profile revisions are immutable';
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER generic_profile_revision_immutable
        BEFORE UPDATE ON generic_extraction_profile_revisions
        FOR EACH ROW EXECUTE FUNCTION reject_generic_profile_revision_update();
    """)


def downgrade():
    op.drop_constraint(
        "fk_generic_profile_head", "generic_extraction_profiles", type_="foreignkey"
    )
    op.drop_table("generic_extraction_profile_revisions")
    op.execute("DROP FUNCTION reject_generic_profile_revision_update()")
    op.drop_table("generic_extraction_profiles")
