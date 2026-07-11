"""Scope domain envelope identity and mutation ownership.

Revision ID: e7f8a9b0c1d2
Revises: c3d4e5f6a7b8
Create Date: 2026-07-11 16:00:00.000000
"""

# pyright: reportAttributeAccessIssue=false

from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from src.lib.domain_envelope_payload_hash import canonical_domain_envelope_payload_hash
from src.schemas.domain_envelope import DomainEnvelope


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("domain_envelopes", sa.Column("adapter_key", sa.String(), nullable=True))
    op.add_column(
        "domain_envelopes",
        sa.Column("source_extraction_result_id", sa.String(), nullable=True),
    )
    op.add_column(
        "domain_envelopes",
        sa.Column("source_payload_hash", sa.String(length=64), nullable=True),
    )

    # Repair only associations that are unambiguous. Rows linked to multiple review
    # states are collision blockers and must be resolved deliberately by an operator.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM curation_candidates
            WHERE envelope_id IS NOT NULL
            GROUP BY envelope_id
            HAVING count(DISTINCT session_id) > 1
          ) OR EXISTS (
            SELECT 1
            FROM domain_envelopes AS envelope
            JOIN curation_candidates AS candidate
              ON candidate.envelope_id = envelope.envelope_id
            WHERE envelope.session_id IS NOT NULL
              AND envelope.session_id IS DISTINCT FROM candidate.session_id
          ) THEN
            RAISE EXCEPTION
              'domain envelope scope migration blocked: envelope linked to multiple review sessions';
          END IF;
        END $$
        """
    )
    op.execute(
        """
        UPDATE domain_envelopes
        SET adapter_key = envelope_json->'metadata'->>'source_adapter_key',
            source_extraction_result_id =
              envelope_json->'metadata'->>'source_extraction_result_id'
        WHERE envelope_json->'metadata' IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE domain_envelopes AS envelope
        SET session_id = linked.session_id
        FROM (
          SELECT envelope_id, min(session_id::text)::uuid AS session_id
          FROM curation_candidates
          WHERE envelope_id IS NOT NULL
          GROUP BY envelope_id
        ) AS linked
        WHERE linked.envelope_id = envelope.envelope_id
          AND envelope.session_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE domain_envelopes AS envelope
        SET source_extraction_result_id = linked.extraction_result_id
        FROM (
          SELECT envelope_id, min(extraction_result_id::text) AS extraction_result_id
          FROM curation_candidates
          WHERE envelope_id IS NOT NULL AND extraction_result_id IS NOT NULL
          GROUP BY envelope_id
          HAVING count(DISTINCT extraction_result_id) = 1
        ) AS linked
        WHERE linked.envelope_id = envelope.envelope_id
        """
    )
    op.execute(
        """
        UPDATE domain_envelopes AS envelope
        SET adapter_key = linked.adapter_key
        FROM (
          SELECT envelope_id, min(adapter_key) AS adapter_key
          FROM curation_candidates
          WHERE envelope_id IS NOT NULL
          GROUP BY envelope_id
          HAVING count(DISTINCT adapter_key) = 1
        ) AS linked
        WHERE linked.envelope_id = envelope.envelope_id
        """
    )
    op.execute(
        """
        UPDATE domain_envelopes AS envelope
        SET document_id = review_session.document_id
        FROM curation_review_sessions AS review_session
        WHERE review_session.id = envelope.session_id
          AND envelope.document_id IS NULL
        """
    )
    connection = op.get_bind()
    legacy_envelopes = connection.execute(
        sa.text("SELECT envelope_id, envelope_json FROM domain_envelopes")
    ).mappings()
    for legacy_envelope in legacy_envelopes:
        connection.execute(
            sa.text(
                """
                UPDATE domain_envelopes
                SET source_payload_hash = :source_payload_hash
                WHERE envelope_id = :envelope_id
                """
            ),
            {
                "envelope_id": legacy_envelope["envelope_id"],
                "source_payload_hash": canonical_domain_envelope_payload_hash(
                    DomainEnvelope.model_validate(
                        legacy_envelope["envelope_json"]
                    ).model_dump(mode="json")
                ),
            },
        )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM domain_envelopes
            WHERE document_id IS NULL
               OR adapter_key IS NULL
               OR source_payload_hash IS NULL
               OR (session_id IS NULL AND source_extraction_result_id IS NULL)
          ) THEN
            RAISE EXCEPTION
              'domain envelope scope migration blocked: null or unbound row requires explicit repair';
          END IF;
        END $$
        """
    )

    op.alter_column("domain_envelopes", "document_id", existing_type=postgresql.UUID(), nullable=False)
    op.alter_column("domain_envelopes", "adapter_key", existing_type=sa.String(), nullable=False)
    op.alter_column(
        "domain_envelopes",
        "source_payload_hash",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_domain_envelopes_owner_association",
        "domain_envelopes",
        "session_id IS NOT NULL OR source_extraction_result_id IS NOT NULL",
    )
    op.create_unique_constraint(
        "uq_domain_envelopes_session_owner",
        "domain_envelopes",
        ["envelope_id", "session_id"],
    )
    op.create_index(
        "uq_domain_envelopes_source_scope",
        "domain_envelopes",
        ["source_extraction_result_id", "adapter_key", "domain_pack_key"],
        unique=True,
        postgresql_where=sa.text("source_extraction_result_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION enforce_domain_envelope_scope_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF OLD.project_key IS DISTINCT FROM NEW.project_key
             OR OLD.domain_pack_key IS DISTINCT FROM NEW.domain_pack_key
             OR OLD.domain_pack_version IS DISTINCT FROM NEW.domain_pack_version
             OR OLD.document_id IS DISTINCT FROM NEW.document_id
             OR OLD.flow_run_id IS DISTINCT FROM NEW.flow_run_id
             OR OLD.adapter_key IS DISTINCT FROM NEW.adapter_key
             OR OLD.source_extraction_result_id IS DISTINCT FROM NEW.source_extraction_result_id
             OR OLD.source_payload_hash IS DISTINCT FROM NEW.source_payload_hash
             OR (OLD.session_id IS NOT NULL AND OLD.session_id IS DISTINCT FROM NEW.session_id)
          THEN
            RAISE EXCEPTION
              'domain envelope identity scope is immutable after materialization';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_domain_envelope_scope_immutable
        BEFORE UPDATE ON domain_envelopes
        FOR EACH ROW EXECUTE FUNCTION enforce_domain_envelope_scope_immutable()
        """
    )
    op.drop_constraint(
        "fk_curation_candidates_envelope_id_domain_envelopes",
        "curation_candidates",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_curation_candidates_envelope_session_owner",
        "curation_candidates",
        "domain_envelopes",
        ["envelope_id", "session_id"],
        ["envelope_id", "session_id"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_domain_envelope_scope_immutable ON domain_envelopes"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_domain_envelope_scope_immutable()")
    op.drop_constraint(
        "fk_curation_candidates_envelope_session_owner",
        "curation_candidates",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_curation_candidates_envelope_id_domain_envelopes",
        "curation_candidates",
        "domain_envelopes",
        ["envelope_id"],
        ["envelope_id"],
        ondelete="NO ACTION",
    )
    op.drop_index("uq_domain_envelopes_source_scope", table_name="domain_envelopes")
    op.drop_constraint("uq_domain_envelopes_session_owner", "domain_envelopes", type_="unique")
    op.drop_constraint("ck_domain_envelopes_owner_association", "domain_envelopes", type_="check")
    op.alter_column("domain_envelopes", "document_id", existing_type=postgresql.UUID(), nullable=True)
    op.drop_column("domain_envelopes", "source_payload_hash")
    op.drop_column("domain_envelopes", "source_extraction_result_id")
    op.drop_column("domain_envelopes", "adapter_key")
