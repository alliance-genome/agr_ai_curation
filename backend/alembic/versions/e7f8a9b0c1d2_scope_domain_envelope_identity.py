"""Scope domain envelope identity and mutation ownership.

Revision ID: e7f8a9b0c1d2
Revises: c3d4e5f6a7b8
Create Date: 2026-07-11 16:00:00.000000
"""

# pyright: reportAttributeAccessIssue=false

from collections.abc import Sequence
from typing import Union

from alembic import op
from pydantic import ValidationError
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

    # Legacy extraction reruns could reuse one envelope across several review
    # sessions. Materialize one complete envelope graph per owner before adding the
    # composite candidate/envelope ownership constraint. The deterministic clone ID
    # keeps the repair auditable, while retaining the original ID for the existing
    # owner (or the lexicographically first legacy owner when it was unscoped).
    op.execute(
        """
        CREATE TEMP TABLE domain_envelope_legacy_owner_map
        ON COMMIT DROP
        AS
        WITH owners AS (
          SELECT envelope_id AS source_envelope_id,
                 session_id AS owner_session_id
          FROM domain_envelopes
          WHERE session_id IS NOT NULL
          UNION
          SELECT envelope_id AS source_envelope_id,
                 session_id AS owner_session_id
          FROM curation_candidates
          WHERE envelope_id IS NOT NULL
          UNION
          SELECT envelope_id AS source_envelope_id,
                 session_id AS owner_session_id
          FROM validation_snapshots
          WHERE envelope_id IS NOT NULL
        )
        SELECT owner.source_envelope_id,
               owner.owner_session_id,
               COALESCE(
                 envelope.session_id,
                 (
                   SELECT min(candidate_owner.owner_session_id::text)::uuid
                   FROM owners AS candidate_owner
                   WHERE candidate_owner.source_envelope_id = owner.source_envelope_id
                 )
               ) AS canonical_session_id
        FROM owners AS owner
        JOIN domain_envelopes AS envelope
          ON envelope.envelope_id = owner.source_envelope_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM domain_envelopes
            WHERE envelope_json->>'envelope_id' IS DISTINCT FROM envelope_id
          ) THEN
            RAISE EXCEPTION
              'domain envelope scope migration blocked: row and payload identities differ';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM domain_envelope_legacy_owner_map AS owner
            JOIN domain_envelopes AS envelope
              ON envelope.envelope_id = owner.source_envelope_id
            JOIN curation_review_sessions AS review_session
              ON review_session.id = owner.owner_session_id
            WHERE envelope.document_id IS NOT NULL
              AND envelope.document_id IS DISTINCT FROM review_session.document_id
          ) THEN
            RAISE EXCEPTION
              'domain envelope scope migration blocked: owner sessions span documents';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM validation_snapshots AS snapshot
            JOIN curation_candidates AS candidate
              ON candidate.id = snapshot.candidate_id
            WHERE snapshot.envelope_id IS NOT NULL
              AND snapshot.session_id IS DISTINCT FROM candidate.session_id
          ) THEN
            RAISE EXCEPTION
              'domain envelope scope migration blocked: validation snapshot owner mismatch';
          END IF;
        END $$
        """
    )
    op.execute(
        """
        CREATE TEMP TABLE domain_envelope_legacy_clone_map
        ON COMMIT DROP
        AS
        SELECT source_envelope_id,
               owner_session_id,
               source_envelope_id || ':session:' || owner_session_id::text
                 AS clone_envelope_id
        FROM domain_envelope_legacy_owner_map
        WHERE owner_session_id IS DISTINCT FROM canonical_session_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM domain_envelope_legacy_clone_map AS clone
            JOIN domain_envelopes AS envelope
              ON envelope.envelope_id = clone.clone_envelope_id
          ) THEN
            RAISE EXCEPTION
              'domain envelope scope migration blocked: deterministic clone id already exists';
          END IF;
        END $$
        """
    )
    op.execute(
        """
        INSERT INTO domain_envelopes (
          envelope_id, revision, project_key, domain_pack_key,
          domain_pack_version, adapter_key, source_extraction_result_id,
          source_payload_hash, status, document_id, session_id, flow_run_id,
          schema_provider, schema_ref_json, object_model_ref_json,
          model_field_ref_json, envelope_json, created_at, updated_at,
          checkpointed_at
        )
        SELECT clone.clone_envelope_id, envelope.revision, envelope.project_key,
               envelope.domain_pack_key, envelope.domain_pack_version,
               NULL, NULL, NULL, envelope.status, envelope.document_id,
               clone.owner_session_id, envelope.flow_run_id,
               envelope.schema_provider, envelope.schema_ref_json,
               envelope.object_model_ref_json, envelope.model_field_ref_json,
               jsonb_set(
                 envelope.envelope_json,
                 '{envelope_id}',
                 to_jsonb(clone.clone_envelope_id),
                 true
               ),
               envelope.created_at, envelope.updated_at,
               envelope.checkpointed_at
        FROM domain_envelope_legacy_clone_map AS clone
        JOIN domain_envelopes AS envelope
          ON envelope.envelope_id = clone.source_envelope_id
        """
    )
    op.execute(
        """
        INSERT INTO domain_envelope_objects (
          id, envelope_id, object_id, pending_ref_id, envelope_revision,
          object_index, object_type, status, validation_state,
          schema_provider, schema_ref_json, object_model_ref_json,
          model_field_ref_json, payload_json, object_json, created_at,
          updated_at
        )
        SELECT gen_random_uuid(), clone.clone_envelope_id, child.object_id,
               child.pending_ref_id, child.envelope_revision,
               child.object_index, child.object_type, child.status,
               child.validation_state, child.schema_provider,
               child.schema_ref_json, child.object_model_ref_json,
               child.model_field_ref_json, child.payload_json,
               child.object_json, child.created_at, child.updated_at
        FROM domain_envelope_legacy_clone_map AS clone
        JOIN domain_envelope_objects AS child
          ON child.envelope_id = clone.source_envelope_id
        """
    )
    op.execute(
        """
        INSERT INTO domain_envelope_history (
          envelope_id, event_id, envelope_revision, event_index,
          event_type, occurred_at, actor_type, actor_id, object_id,
          field_path, model_field_ref_json, event_json, created_at
        )
        SELECT clone.clone_envelope_id, child.event_id,
               child.envelope_revision, child.event_index, child.event_type,
               child.occurred_at, child.actor_type, child.actor_id,
               child.object_id, child.field_path, child.model_field_ref_json,
               child.event_json, child.created_at
        FROM domain_envelope_legacy_clone_map AS clone
        JOIN domain_envelope_history AS child
          ON child.envelope_id = clone.source_envelope_id
        """
    )
    op.execute(
        """
        INSERT INTO domain_envelope_projection_index (
          id, envelope_id, object_id, envelope_revision, object_type,
          projection_type, projection_key, projection_status,
          schema_provider, schema_ref_json, object_model_ref_json,
          model_field_ref_json, projection_json, created_at, updated_at
        )
        SELECT gen_random_uuid(), clone.clone_envelope_id, child.object_id,
               child.envelope_revision, child.object_type,
               child.projection_type, child.projection_key,
               child.projection_status, child.schema_provider,
               child.schema_ref_json, child.object_model_ref_json,
               child.model_field_ref_json, child.projection_json,
               child.created_at, child.updated_at
        FROM domain_envelope_legacy_clone_map AS clone
        JOIN domain_envelope_projection_index AS child
          ON child.envelope_id = clone.source_envelope_id
        """
    )
    op.execute(
        """
        INSERT INTO domain_validation_findings (
          id, envelope_id, finding_id, envelope_revision, finding_index,
          object_id, field_path, severity, status, code,
          object_model_ref_json, model_field_ref_json, finding_json,
          created_at, updated_at
        )
        SELECT gen_random_uuid(), clone.clone_envelope_id, child.finding_id,
               child.envelope_revision, child.finding_index, child.object_id,
               child.field_path, child.severity, child.status, child.code,
               child.object_model_ref_json, child.model_field_ref_json,
               child.finding_json, child.created_at, child.updated_at
        FROM domain_envelope_legacy_clone_map AS clone
        JOIN domain_validation_findings AS child
          ON child.envelope_id = clone.source_envelope_id
        """
    )
    op.execute(
        """
        UPDATE curation_candidates AS candidate
        SET envelope_id = clone.clone_envelope_id
        FROM domain_envelope_legacy_clone_map AS clone
        WHERE candidate.envelope_id = clone.source_envelope_id
          AND candidate.session_id = clone.owner_session_id
        """
    )
    op.execute(
        """
        UPDATE validation_snapshots AS snapshot
        SET envelope_id = clone.clone_envelope_id
        FROM domain_envelope_legacy_clone_map AS clone
        WHERE snapshot.envelope_id = clone.source_envelope_id
          AND snapshot.session_id = clone.owner_session_id
        """
    )
    op.execute(
        """
        UPDATE domain_envelopes AS envelope
        SET session_id = owner.canonical_session_id
        FROM (
          SELECT DISTINCT source_envelope_id, canonical_session_id
          FROM domain_envelope_legacy_owner_map
        ) AS owner
        WHERE envelope.envelope_id = owner.source_envelope_id
        """
    )
    op.execute(
        """
        UPDATE domain_envelopes
        SET adapter_key = envelope_json->'metadata'->>'source_adapter_key'
        WHERE envelope_json->'metadata' IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE domain_envelopes AS envelope
        SET source_extraction_result_id =
              envelope.envelope_json->'metadata'->>'source_extraction_result_id'
        WHERE envelope.envelope_json->'metadata' IS NOT NULL
          AND NOT EXISTS (
            SELECT 1
            FROM domain_envelope_legacy_clone_map AS clone
            WHERE clone.clone_envelope_id = envelope.envelope_id
          )
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
    # A legacy extraction could materialize several envelopes in one session. The
    # new source-scope index represents only the one-envelope form, so retain the
    # session owner and clear the redundant derived source column on those rows.
    # Source provenance remains in envelope_json and curation_candidates. Any
    # duplicate that has no session owner remains below as a fail-closed blocker.
    op.execute(
        """
        WITH duplicate_source_scopes AS (
          SELECT source_extraction_result_id, adapter_key, domain_pack_key
          FROM domain_envelopes
          WHERE source_extraction_result_id IS NOT NULL
          GROUP BY source_extraction_result_id, adapter_key, domain_pack_key
          HAVING count(*) > 1
        )
        UPDATE domain_envelopes AS envelope
        SET source_extraction_result_id = NULL
        FROM duplicate_source_scopes AS duplicate
        WHERE envelope.source_extraction_result_id =
                duplicate.source_extraction_result_id
          AND envelope.adapter_key = duplicate.adapter_key
          AND envelope.domain_pack_key = duplicate.domain_pack_key
          AND envelope.session_id IS NOT NULL
        """
    )
    connection = op.get_bind()
    legacy_envelopes = connection.execute(
        sa.text("SELECT envelope_id, envelope_json FROM domain_envelopes")
    ).mappings()
    for legacy_envelope in legacy_envelopes:
        envelope_payload = legacy_envelope["envelope_json"]
        try:
            hash_payload = DomainEnvelope.model_validate(envelope_payload).model_dump(
                mode="json"
            )
        except ValidationError:
            # Earlier persisted envelopes used keys such as ``objects`` that are no
            # longer accepted by the strict runtime model. Preserve and hash those
            # payloads exactly instead of blocking a schema-only ownership upgrade.
            hash_payload = envelope_payload
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
                    hash_payload
                ),
            },
        )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM curation_candidates AS candidate
            JOIN domain_envelopes AS envelope
              ON envelope.envelope_id = candidate.envelope_id
            WHERE candidate.envelope_id IS NOT NULL
              AND candidate.session_id IS DISTINCT FROM envelope.session_id
          ) THEN
            RAISE EXCEPTION
              'domain envelope scope migration blocked: candidate owner mismatch remains';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM validation_snapshots AS snapshot
            JOIN domain_envelopes AS envelope
              ON envelope.envelope_id = snapshot.envelope_id
            WHERE snapshot.envelope_id IS NOT NULL
              AND snapshot.session_id IS DISTINCT FROM envelope.session_id
          ) THEN
            RAISE EXCEPTION
              'domain envelope scope migration blocked: validation owner mismatch remains';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM domain_envelopes
            WHERE source_extraction_result_id IS NOT NULL
            GROUP BY source_extraction_result_id, adapter_key, domain_pack_key
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION
              'domain envelope scope migration blocked: duplicate source scope remains';
          END IF;
        END $$
        """
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
