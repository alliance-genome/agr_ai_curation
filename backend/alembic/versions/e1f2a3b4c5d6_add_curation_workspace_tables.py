"""Add curation workspace tables.

Revision ID: e1f2a3b4c5d6
Revises: d4e5f6a7b8c9
Create Date: 2026-03-20 23:50:00.000000
"""

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)
JSONB_EMPTY_ARRAY = sa.text("'[]'::jsonb")
JSONB_EMPTY_OBJECT = sa.text("'{}'::jsonb")

CURATION_SESSION_STATUSES = (
    "new",
    "in_progress",
    "paused",
    "ready_for_submission",
    "submitted",
    "rejected",
)
CURATION_CANDIDATE_STATUSES = ("pending", "accepted", "rejected")
CURATION_CANDIDATE_SOURCES = ("extracted", "manual", "imported")
CURATION_VALIDATION_SNAPSHOT_STATES = (
    "not_requested",
    "pending",
    "completed",
    "failed",
    "stale",
)
CURATION_VALIDATION_SCOPES = ("candidate", "session")
CURATION_ACTION_TYPES = (
    "session_created",
    "session_status_updated",
    "session_assigned",
    "candidate_created",
    "candidate_updated",
    "candidate_accepted",
    "candidate_rejected",
    "candidate_reset",
    "validation_requested",
    "validation_completed",
    "evidence_recomputed",
    "evidence_manual_added",
    "submission_previewed",
    "submission_executed",
    "submission_retried",
)
CURATION_ACTOR_TYPES = ("user", "system", "adapter")
CURATION_EVIDENCE_SOURCES = ("extracted", "manual", "recomputed")
SUBMISSION_MODES = ("preview", "export", "direct_submit")
CURATION_SUBMISSION_STATUSES = (
    "preview_ready",
    "export_ready",
    "queued",
    "accepted",
    "validation_errors",
    "conflict",
    "manual_review_required",
    "failed",
)
CURATION_EXTRACTION_SOURCE_KINDS = ("chat", "flow", "manual_import")
CURATION_SESSION_SORT_FIELDS = (
    "prepared_at",
    "last_worked_at",
    "status",
    "document_title",
    "candidate_count",
    "validation",
    "evidence",
    "curator",
)
CURATION_SORT_DIRECTIONS = ("asc", "desc")


def _enum_values(values: Sequence[str]) -> list[str]:
    return list(values)


def _enum_check(
    constraint_name: str,
    column_name: str,
    values: Sequence[str],
    *,
    nullable: bool = False,
) -> sa.CheckConstraint:
    allowed_values = ", ".join(f"'{value}'" for value in _enum_values(values))
    expression = f"{column_name} IN ({allowed_values})"
    if nullable:
        expression = f"{column_name} IS NULL OR {expression}"
    return sa.CheckConstraint(expression, name=constraint_name)


def _uuid_pk(column_name: str = "id") -> sa.Column:
    return sa.Column(
        column_name,
        UUID,
        primary_key=True,
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    op.create_table(
        "curation_review_sessions",
        _uuid_pk(),
        sa.Column("status", sa.String(), nullable=False, server_default="new"),
        sa.Column("adapter_key", sa.String(), nullable=False),
        sa.Column("profile_key", sa.String(), nullable=True),
        sa.Column("document_id", UUID, sa.ForeignKey("pdf_documents.id"), nullable=False),
        sa.Column("flow_run_id", sa.String(), nullable=True),
        sa.Column("current_candidate_id", UUID, nullable=True),
        sa.Column("assigned_curator_id", sa.String(), nullable=True),
        sa.Column("created_by_id", sa.String(), nullable=True),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tags", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("total_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reviewed_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manual_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("warnings", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_worked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _enum_check(
            "ck_curation_review_sessions_status",
            "status",
            CURATION_SESSION_STATUSES,
        ),
        sa.CheckConstraint("session_version >= 1", name="ck_curation_review_sessions_version"),
        sa.CheckConstraint(
            "total_candidates >= 0 AND reviewed_candidates >= 0 AND pending_candidates >= 0 "
            "AND accepted_candidates >= 0 AND rejected_candidates >= 0 "
            "AND manual_candidates >= 0",
            name="ck_curation_review_sessions_progress_nonnegative",
        ),
    )

    op.create_index("ix_curation_sessions_status", "curation_review_sessions", ["status"], unique=False)
    op.create_index(
        "ix_curation_sessions_adapter_key",
        "curation_review_sessions",
        ["adapter_key"],
        unique=False,
    )
    op.create_index(
        "ix_curation_sessions_flow_run_id",
        "curation_review_sessions",
        ["flow_run_id"],
        unique=False,
        postgresql_where=sa.text("flow_run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_curation_sessions_assigned_curator",
        "curation_review_sessions",
        ["assigned_curator_id"],
        unique=False,
    )
    op.execute(
        "CREATE INDEX ix_curation_sessions_prepared_at "
        "ON curation_review_sessions (prepared_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_curation_sessions_last_worked "
        "ON curation_review_sessions (last_worked_at DESC NULLS LAST)"
    )
    op.create_index(
        "ix_curation_sessions_document",
        "curation_review_sessions",
        ["document_id"],
        unique=False,
    )

    op.create_table(
        "extraction_results",
        _uuid_pk(),
        sa.Column("document_id", UUID, sa.ForeignKey("pdf_documents.id"), nullable=False),
        sa.Column("adapter_key", sa.String(), nullable=True),
        sa.Column("profile_key", sa.String(), nullable=True),
        sa.Column("domain_key", sa.String(), nullable=True),
        sa.Column("agent_key", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("origin_session_id", sa.String(), nullable=True),
        sa.Column("trace_id", sa.String(), nullable=True),
        sa.Column("flow_run_id", sa.String(), nullable=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conversation_summary", sa.Text(), nullable=True),
        sa.Column("payload_json", JSONB, nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default=JSONB_EMPTY_OBJECT),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _enum_check(
            "ck_extraction_results_source_kind",
            "source_kind",
            CURATION_EXTRACTION_SOURCE_KINDS,
        ),
        sa.CheckConstraint("candidate_count >= 0", name="ck_extraction_results_candidate_count"),
    )

    op.create_index("ix_extraction_results_document", "extraction_results", ["document_id"], unique=False)
    op.create_index(
        "ix_extraction_results_flow_run",
        "extraction_results",
        ["flow_run_id"],
        unique=False,
        postgresql_where=sa.text("flow_run_id IS NOT NULL"),
    )

    op.create_table(
        "curation_candidates",
        _uuid_pk(),
        sa.Column("session_id", UUID, sa.ForeignKey("curation_review_sessions.id"), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("adapter_key", sa.String(), nullable=False),
        sa.Column("profile_key", sa.String(), nullable=True),
        sa.Column("display_label", sa.String(), nullable=True),
        sa.Column("secondary_label", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("conversation_summary", sa.Text(), nullable=True),
        sa.Column("unresolved_ambiguities", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("extraction_result_id", UUID, sa.ForeignKey("extraction_results.id"), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=JSONB_EMPTY_OBJECT),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        _enum_check("ck_curation_candidates_source", "source", CURATION_CANDIDATE_SOURCES),
        _enum_check("ck_curation_candidates_status", "status", CURATION_CANDIDATE_STATUSES),
        sa.CheckConstraint('"order" >= 0', name="ck_curation_candidates_order"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_curation_candidates_confidence",
        ),
    )

    op.create_index(
        "ix_curation_candidates_session",
        "curation_candidates",
        ["session_id", "order"],
        unique=False,
    )
    op.create_index(
        "ix_curation_candidates_status",
        "curation_candidates",
        ["session_id", "status"],
        unique=False,
    )

    op.create_table(
        "evidence_anchors",
        _uuid_pk(),
        sa.Column("candidate_id", UUID, sa.ForeignKey("curation_candidates.id"), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("field_keys", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("field_group_keys", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("anchor", JSONB, nullable=False),
        sa.Column("warnings", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _enum_check("ck_evidence_anchors_source", "source", CURATION_EVIDENCE_SOURCES),
    )

    op.create_index("ix_evidence_anchors_candidate", "evidence_anchors", ["candidate_id"], unique=False)

    op.create_table(
        "annotation_drafts",
        _uuid_pk(),
        sa.Column("candidate_id", UUID, sa.ForeignKey("curation_candidates.id"), nullable=False),
        sa.Column("adapter_key", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("fields", JSONB, nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=JSONB_EMPTY_OBJECT),
        sa.UniqueConstraint("candidate_id", name="uq_annotation_drafts_candidate_id"),
        sa.CheckConstraint("version >= 1", name="ck_annotation_drafts_version"),
    )

    op.create_table(
        "validation_snapshots",
        _uuid_pk(),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("session_id", UUID, sa.ForeignKey("curation_review_sessions.id"), nullable=False),
        sa.Column("candidate_id", UUID, sa.ForeignKey("curation_candidates.id"), nullable=True),
        sa.Column("adapter_key", sa.String(), nullable=True),
        sa.Column(
            "state",
            sa.String(),
            nullable=False,
            server_default="not_requested",
        ),
        sa.Column("field_results", JSONB, nullable=False, server_default=JSONB_EMPTY_OBJECT),
        sa.Column("summary", JSONB, nullable=False),
        sa.Column("warnings", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _enum_check("ck_validation_snapshots_scope", "scope", CURATION_VALIDATION_SCOPES),
        _enum_check(
            "ck_validation_snapshots_state",
            "state",
            CURATION_VALIDATION_SNAPSHOT_STATES,
        ),
        sa.CheckConstraint(
            "scope <> 'candidate' OR candidate_id IS NOT NULL",
            name="ck_validation_snapshots_candidate_scope",
        ),
    )

    op.create_index(
        "ix_validation_snapshots_session",
        "validation_snapshots",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_validation_snapshots_candidate",
        "validation_snapshots",
        ["candidate_id"],
        unique=False,
        postgresql_where=sa.text("candidate_id IS NOT NULL"),
    )

    op.create_table(
        "curation_submissions",
        _uuid_pk(),
        sa.Column("session_id", UUID, sa.ForeignKey("curation_review_sessions.id"), nullable=False),
        sa.Column("adapter_key", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("target_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("readiness", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("external_reference", sa.String(), nullable=True),
        sa.Column("response_message", sa.Text(), nullable=True),
        sa.Column("validation_errors", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("warnings", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _enum_check("ck_curation_submissions_mode", "mode", SUBMISSION_MODES),
        _enum_check(
            "ck_curation_submissions_status",
            "status",
            CURATION_SUBMISSION_STATUSES,
        ),
    )

    op.execute(
        "CREATE INDEX ix_submissions_session "
        "ON curation_submissions (session_id, requested_at DESC)"
    )

    op.create_table(
        "curation_action_log",
        _uuid_pk(),
        sa.Column("session_id", UUID, sa.ForeignKey("curation_review_sessions.id"), nullable=False),
        sa.Column("candidate_id", UUID, sa.ForeignKey("curation_candidates.id"), nullable=True),
        sa.Column("draft_id", UUID, sa.ForeignKey("annotation_drafts.id"), nullable=True),
        sa.Column("action_type", sa.String(), nullable=False),
        sa.Column("actor_type", sa.String(), nullable=False),
        sa.Column("actor", JSONB, nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_session_status", sa.String(), nullable=True),
        sa.Column("new_session_status", sa.String(), nullable=True),
        sa.Column("previous_candidate_status", sa.String(), nullable=True),
        sa.Column("new_candidate_status", sa.String(), nullable=True),
        sa.Column("changed_field_keys", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("evidence_anchor_ids", JSONB, nullable=False, server_default=JSONB_EMPTY_ARRAY),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default=JSONB_EMPTY_OBJECT),
        _enum_check("ck_curation_action_log_action_type", "action_type", CURATION_ACTION_TYPES),
        _enum_check("ck_curation_action_log_actor_type", "actor_type", CURATION_ACTOR_TYPES),
        _enum_check(
            "ck_curation_action_log_previous_session_status",
            "previous_session_status",
            CURATION_SESSION_STATUSES,
            nullable=True,
        ),
        _enum_check(
            "ck_curation_action_log_new_session_status",
            "new_session_status",
            CURATION_SESSION_STATUSES,
            nullable=True,
        ),
        _enum_check(
            "ck_curation_action_log_previous_candidate_status",
            "previous_candidate_status",
            CURATION_CANDIDATE_STATUSES,
            nullable=True,
        ),
        _enum_check(
            "ck_curation_action_log_new_candidate_status",
            "new_candidate_status",
            CURATION_CANDIDATE_STATUSES,
            nullable=True,
        ),
    )

    op.execute(
        "CREATE INDEX ix_action_log_session "
        "ON curation_action_log (session_id, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_action_log_candidate "
        "ON curation_action_log (candidate_id, occurred_at DESC) "
        "WHERE candidate_id IS NOT NULL"
    )

    op.create_table(
        "curation_saved_views",
        _uuid_pk(),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("filters", JSONB, nullable=False),
        sa.Column("sort_by", sa.String(), nullable=False),
        sa.Column("sort_direction", sa.String(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _enum_check("ck_curation_saved_views_sort_by", "sort_by", CURATION_SESSION_SORT_FIELDS),
        _enum_check(
            "ck_curation_saved_views_sort_direction",
            "sort_direction",
            CURATION_SORT_DIRECTIONS,
        ),
    )

    op.create_index(
        "ix_saved_views_created_by",
        "curation_saved_views",
        ["created_by_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_saved_views_created_by", table_name="curation_saved_views")
    op.drop_table("curation_saved_views")

    op.drop_index("ix_action_log_candidate", table_name="curation_action_log")
    op.drop_index("ix_action_log_session", table_name="curation_action_log")
    op.drop_table("curation_action_log")

    op.drop_index("ix_submissions_session", table_name="curation_submissions")
    op.drop_table("curation_submissions")

    op.drop_index("ix_validation_snapshots_candidate", table_name="validation_snapshots")
    op.drop_index("ix_validation_snapshots_session", table_name="validation_snapshots")
    op.drop_table("validation_snapshots")

    op.drop_table("annotation_drafts")

    op.drop_index("ix_evidence_anchors_candidate", table_name="evidence_anchors")
    op.drop_table("evidence_anchors")

    op.drop_index("ix_curation_candidates_status", table_name="curation_candidates")
    op.drop_index("ix_curation_candidates_session", table_name="curation_candidates")
    op.drop_table("curation_candidates")

    op.drop_index("ix_extraction_results_flow_run", table_name="extraction_results")
    op.drop_index("ix_extraction_results_document", table_name="extraction_results")
    op.drop_table("extraction_results")

    op.drop_index("ix_curation_sessions_document", table_name="curation_review_sessions")
    op.drop_index("ix_curation_sessions_last_worked", table_name="curation_review_sessions")
    op.drop_index("ix_curation_sessions_prepared_at", table_name="curation_review_sessions")
    op.drop_index("ix_curation_sessions_assigned_curator", table_name="curation_review_sessions")
    op.drop_index("ix_curation_sessions_flow_run_id", table_name="curation_review_sessions")
    op.drop_index("ix_curation_sessions_adapter_key", table_name="curation_review_sessions")
    op.drop_index("ix_curation_sessions_status", table_name="curation_review_sessions")
    op.drop_table("curation_review_sessions")
