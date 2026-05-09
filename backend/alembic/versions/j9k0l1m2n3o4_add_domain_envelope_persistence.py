"""Add domain envelope persistence tables.

Revision ID: j9k0l1m2n3o4
Revises: i8j9k0l1m2n3
Create Date: 2026-05-09 16:45:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "j9k0l1m2n3o4"
down_revision: Union[str, Sequence[str], None] = "i8j9k0l1m2n3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)
JSONB_EMPTY_OBJECT = sa.text("'{}'::jsonb")
FK_ON_DELETE_NO_ACTION = "NO ACTION"

DOMAIN_ENVELOPE_STATUSES = (
    "extraction_pending",
    "extracted",
    "validating",
    "validated",
    "ready_for_export",
    "exported",
    "submitted",
    "failed",
)
CURATABLE_OBJECT_STATUSES = (
    "pending",
    "extracted",
    "needs_review",
    "validating",
    "validated",
    "ready_for_export",
    "rejected",
)
VALIDATION_FINDING_SEVERITIES = ("info", "warning", "error", "blocker")
VALIDATION_FINDING_STATUSES = ("open", "resolved", "waived")
HISTORY_EVENT_KINDS = (
    "created",
    "object_extracted",
    "object_updated",
    "field_updated",
    "validation_finding_added",
    "status_changed",
    "exported",
    "submitted",
)
HISTORY_ACTOR_TYPES = ("system", "agent", "human", "tool")


def _enum_check(
    constraint_name: str,
    column_name: str,
    values: Sequence[str],
) -> sa.CheckConstraint:
    allowed_values = ", ".join(f"'{value}'" for value in values)
    return sa.CheckConstraint(
        f"{column_name} IN ({allowed_values})",
        name=constraint_name,
    )


def _uuid_pk(column_name: str = "id") -> sa.Column:
    return sa.Column(
        column_name,
        UUID,
        primary_key=True,
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )


def _fk(target: str) -> sa.ForeignKey:
    return sa.ForeignKey(target, ondelete=FK_ON_DELETE_NO_ACTION)


def upgrade() -> None:
    op.create_table(
        "domain_envelopes",
        sa.Column("envelope_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("project_key", sa.String(), nullable=False),
        sa.Column("domain_pack_key", sa.String(), nullable=False),
        sa.Column("domain_pack_version", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("document_id", UUID, _fk("pdf_documents.id"), nullable=True),
        sa.Column("session_id", UUID, _fk("curation_review_sessions.id"), nullable=True),
        sa.Column("flow_run_id", sa.String(), nullable=True),
        sa.Column("schema_provider", sa.String(), nullable=True),
        sa.Column("schema_ref_json", JSONB, nullable=False, server_default=JSONB_EMPTY_OBJECT),
        sa.Column(
            "object_model_ref_json",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_OBJECT,
        ),
        sa.Column(
            "model_field_ref_json",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_OBJECT,
        ),
        sa.Column("envelope_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("checkpointed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _enum_check("ck_domain_envelopes_status", "status", DOMAIN_ENVELOPE_STATUSES),
        sa.CheckConstraint("revision >= 1", name="ck_domain_envelopes_revision"),
    )
    op.create_index("ix_domain_envelopes_document", "domain_envelopes", ["document_id"], unique=False)
    op.create_index(
        "ix_domain_envelopes_session",
        "domain_envelopes",
        ["session_id"],
        unique=False,
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_envelopes_flow_run",
        "domain_envelopes",
        ["flow_run_id"],
        unique=False,
        postgresql_where=sa.text("flow_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_domain_envelopes_domain_pack_status",
        "domain_envelopes",
        ["project_key", "domain_pack_key", "status"],
        unique=False,
    )

    op.create_table(
        "domain_envelope_objects",
        _uuid_pk(),
        sa.Column("envelope_id", sa.String(), _fk("domain_envelopes.envelope_id"), nullable=False),
        sa.Column("object_id", sa.String(), nullable=False),
        sa.Column("pending_ref_id", sa.String(), nullable=True),
        sa.Column("envelope_revision", sa.Integer(), nullable=False),
        sa.Column("object_index", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("validation_state", sa.String(), nullable=False),
        sa.Column("schema_provider", sa.String(), nullable=True),
        sa.Column("schema_ref_json", JSONB, nullable=False, server_default=JSONB_EMPTY_OBJECT),
        sa.Column(
            "object_model_ref_json",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_OBJECT,
        ),
        sa.Column(
            "model_field_ref_json",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_OBJECT,
        ),
        sa.Column("payload_json", JSONB, nullable=False, server_default=JSONB_EMPTY_OBJECT),
        sa.Column("object_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _enum_check("ck_domain_envelope_objects_status", "status", CURATABLE_OBJECT_STATUSES),
        sa.CheckConstraint(
            "envelope_revision >= 1",
            name="ck_domain_envelope_objects_revision",
        ),
        sa.CheckConstraint("object_index >= 0", name="ck_domain_envelope_objects_index"),
        sa.UniqueConstraint(
            "envelope_id",
            "object_id",
            name="uq_domain_envelope_objects_current",
        ),
    )
    op.create_index(
        "ix_domain_envelope_objects_lookup",
        "domain_envelope_objects",
        ["envelope_id", "envelope_revision", "object_type", "status", "validation_state"],
        unique=False,
    )

    op.create_table(
        "domain_validation_findings",
        _uuid_pk(),
        sa.Column("envelope_id", sa.String(), _fk("domain_envelopes.envelope_id"), nullable=False),
        sa.Column("finding_id", sa.String(), nullable=True),
        sa.Column("envelope_revision", sa.Integer(), nullable=False),
        sa.Column("finding_index", sa.Integer(), nullable=False),
        sa.Column("object_id", sa.String(), nullable=True),
        sa.Column("field_path", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column(
            "object_model_ref_json",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_OBJECT,
        ),
        sa.Column(
            "model_field_ref_json",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_OBJECT,
        ),
        sa.Column("finding_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _enum_check(
            "ck_domain_validation_findings_severity",
            "severity",
            VALIDATION_FINDING_SEVERITIES,
        ),
        _enum_check(
            "ck_domain_validation_findings_status",
            "status",
            VALIDATION_FINDING_STATUSES,
        ),
        sa.CheckConstraint(
            "envelope_revision >= 1",
            name="ck_domain_validation_findings_revision",
        ),
        sa.CheckConstraint(
            "finding_index >= 0",
            name="ck_domain_validation_findings_index",
        ),
        sa.UniqueConstraint(
            "envelope_id",
            "envelope_revision",
            "finding_index",
            name="uq_domain_validation_findings_revision_index",
        ),
    )
    op.create_index(
        "ix_domain_validation_findings_lookup",
        "domain_validation_findings",
        ["envelope_id", "object_id", "field_path", "status", "severity"],
        unique=False,
    )

    op.create_table(
        "domain_envelope_history",
        sa.Column("envelope_id", sa.String(), _fk("domain_envelopes.envelope_id"), primary_key=True),
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("envelope_revision", sa.Integer(), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=True),
        sa.Column("object_id", sa.String(), nullable=True),
        sa.Column("field_path", sa.String(), nullable=True),
        sa.Column(
            "model_field_ref_json",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_OBJECT,
        ),
        sa.Column("event_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _enum_check("ck_domain_envelope_history_event_type", "event_type", HISTORY_EVENT_KINDS),
        _enum_check("ck_domain_envelope_history_actor_type", "actor_type", HISTORY_ACTOR_TYPES),
        sa.CheckConstraint(
            "envelope_revision >= 1",
            name="ck_domain_envelope_history_revision",
        ),
        sa.CheckConstraint("event_index >= 0", name="ck_domain_envelope_history_index"),
    )
    op.execute(
        "CREATE INDEX ix_domain_envelope_history_time "
        "ON domain_envelope_history (envelope_id, occurred_at DESC)"
    )

    op.create_table(
        "domain_envelope_projection_index",
        _uuid_pk(),
        sa.Column("envelope_id", sa.String(), _fk("domain_envelopes.envelope_id"), nullable=False),
        sa.Column("object_id", sa.String(), nullable=False),
        sa.Column("envelope_revision", sa.Integer(), nullable=False),
        sa.Column("object_type", sa.String(), nullable=True),
        sa.Column("projection_type", sa.String(), nullable=False),
        sa.Column("projection_key", sa.String(), nullable=False),
        sa.Column("projection_status", sa.String(), nullable=True),
        sa.Column("schema_provider", sa.String(), nullable=True),
        sa.Column("schema_ref_json", JSONB, nullable=False, server_default=JSONB_EMPTY_OBJECT),
        sa.Column(
            "object_model_ref_json",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_OBJECT,
        ),
        sa.Column(
            "model_field_ref_json",
            JSONB,
            nullable=False,
            server_default=JSONB_EMPTY_OBJECT,
        ),
        sa.Column("projection_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "envelope_revision >= 1",
            name="ck_domain_projection_index_revision",
        ),
        sa.UniqueConstraint(
            "envelope_id",
            "object_id",
            "projection_type",
            "projection_key",
            name="uq_domain_projection_index_key",
        ),
    )
    op.create_index(
        "ix_domain_projection_index_lookup",
        "domain_envelope_projection_index",
        ["envelope_id", "object_id", "envelope_revision", "projection_type"],
        unique=False,
    )

    op.add_column("curation_candidates", sa.Column("envelope_id", sa.String(), nullable=True))
    op.add_column("curation_candidates", sa.Column("object_id", sa.String(), nullable=True))
    op.add_column(
        "curation_candidates",
        sa.Column("envelope_revision", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_curation_candidates_envelope_id_domain_envelopes",
        "curation_candidates",
        "domain_envelopes",
        ["envelope_id"],
        ["envelope_id"],
        ondelete=FK_ON_DELETE_NO_ACTION,
    )
    op.create_check_constraint(
        "ck_curation_candidates_domain_projection_ref",
        "curation_candidates",
        "(envelope_id IS NULL AND object_id IS NULL AND envelope_revision IS NULL) "
        "OR (envelope_id IS NOT NULL AND object_id IS NOT NULL "
        "AND envelope_revision IS NOT NULL AND envelope_revision >= 1)",
    )
    op.create_index(
        "ix_curation_candidates_domain_projection",
        "curation_candidates",
        ["envelope_id", "object_id", "envelope_revision"],
        unique=False,
        postgresql_where=sa.text("envelope_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_curation_candidates_domain_projection", table_name="curation_candidates")
    op.drop_constraint(
        "ck_curation_candidates_domain_projection_ref",
        "curation_candidates",
        type_="check",
    )
    op.drop_constraint(
        "fk_curation_candidates_envelope_id_domain_envelopes",
        "curation_candidates",
        type_="foreignkey",
    )
    op.drop_column("curation_candidates", "envelope_revision")
    op.drop_column("curation_candidates", "object_id")
    op.drop_column("curation_candidates", "envelope_id")

    op.drop_index("ix_domain_projection_index_lookup", table_name="domain_envelope_projection_index")
    op.drop_table("domain_envelope_projection_index")

    op.drop_index("ix_domain_envelope_history_time", table_name="domain_envelope_history")
    op.drop_table("domain_envelope_history")

    op.drop_index("ix_domain_validation_findings_lookup", table_name="domain_validation_findings")
    op.drop_table("domain_validation_findings")

    op.drop_index("ix_domain_envelope_objects_lookup", table_name="domain_envelope_objects")
    op.drop_table("domain_envelope_objects")

    op.drop_index("ix_domain_envelopes_domain_pack_status", table_name="domain_envelopes")
    op.drop_index("ix_domain_envelopes_flow_run", table_name="domain_envelopes")
    op.drop_index("ix_domain_envelopes_session", table_name="domain_envelopes")
    op.drop_index("ix_domain_envelopes_document", table_name="domain_envelopes")
    op.drop_table("domain_envelopes")
