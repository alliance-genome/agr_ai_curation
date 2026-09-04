"""Preserve immutable package capability versions used by profile mappings.

Revision ID: h5c6d7e8f9a0
Revises: g4b5c6d7e8f9
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "h5c6d7e8f9a0"
down_revision = "g4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("profile_validator_capabilities",
        sa.Column("fingerprint", sa.String(71), primary_key=True),
        *[sa.Column(name, sa.Text(), nullable=False) for name in
          ("package_id", "package_version", "domain_pack_id", "domain_pack_version", "binding_id")],
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.UniqueConstraint("package_id", "package_version", "domain_pack_id", "domain_pack_version",
                            "binding_id", name="uq_profile_capability_version"),
        sa.CheckConstraint("fingerprint ~ '^sha256:[a-f0-9]{64}$'", name="ck_profile_capability_fingerprint"),
    )
    op.create_table("profile_validator_capability_references",
        sa.Column("profile_revision_id", UUID(as_uuid=True), sa.ForeignKey(
            "generic_extraction_profile_revisions.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("mapping_id", sa.Text(), primary_key=True),
        sa.Column("capability_fingerprint", sa.String(71), sa.ForeignKey(
            "profile_validator_capabilities.fingerprint", ondelete="RESTRICT"), nullable=False),
    )
    for table in ("profile_validator_capabilities", "profile_validator_capability_references"):
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_generic_profile_revision_update()""")
    # JSON contracts and normalized references must agree even for non-API inserts.
    op.execute("""
        CREATE FUNCTION check_profile_validator_references() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mapping jsonb;
        BEGIN
          FOR mapping IN SELECT jsonb_array_elements(COALESCE(NEW.contract->'validator_mappings', '[]'::jsonb)) LOOP
            IF NOT EXISTS (
              SELECT 1 FROM profile_validator_capability_references r
              JOIN profile_validator_capabilities c ON c.fingerprint = r.capability_fingerprint
              WHERE r.profile_revision_id = NEW.id
                AND r.mapping_id = mapping->>'mapping_id'
                AND c.fingerprint = mapping->>'capability_fingerprint'
                AND c.snapshot->'ref' = mapping->'capability_ref'
            ) THEN RAISE EXCEPTION 'Profile mapping requires an immutable capability reference'; END IF;
          END LOOP;
          RETURN NEW;
        END;
        $$;
        CREATE CONSTRAINT TRIGGER profile_validator_references_complete
        AFTER INSERT ON generic_extraction_profile_revisions
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
        EXECUTE FUNCTION check_profile_validator_references();
    """)


def downgrade():
    op.execute("DROP TRIGGER profile_validator_references_complete ON generic_extraction_profile_revisions")
    op.execute("DROP FUNCTION check_profile_validator_references()")
    op.drop_table("profile_validator_capability_references")
    op.drop_table("profile_validator_capabilities")
