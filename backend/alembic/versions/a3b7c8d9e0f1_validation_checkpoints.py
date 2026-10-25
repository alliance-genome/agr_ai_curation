"""Preserve exact known checkpoints without inventing missing earlier revisions."""

from hashlib import sha256
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert, JSONB

revision = "a3b7c8d9e0f1"
down_revision = "f2a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "validation_checkpoints",
        sa.Column("envelope_id", sa.String, primary_key=True),
        sa.Column("revision", sa.BigInteger, primary_key=True),
        sa.Column("document_id", sa.String, nullable=False),
        sa.Column("source_extraction_result_id", sa.String),
        sa.Column(
            "body_sha",
            sa.String(64),
            sa.ForeignKey("validation_bodies.sha256"),
            nullable=False,
        ),
        sa.Column("provenance", sa.String, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    db = op.get_bind()
    bodies = sa.table(
        "validation_bodies", sa.column("sha256"), sa.column("content", JSONB)
    )
    checkpoints = sa.table(
        "validation_checkpoints",
        *(
            sa.column(name)
            for name in (
                "envelope_id",
                "revision",
                "document_id",
                "source_extraction_result_id",
                "body_sha",
                "provenance",
            )
        ),
    )
    for row in db.execute(
        sa.text(
            "SELECT envelope_id, revision, document_id, source_extraction_result_id, envelope_json FROM domain_envelopes"
        )
    ).mappings():
        encoded = json.dumps(
            row["envelope_json"],
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        digest = sha256(encoded.encode()).hexdigest()
        db.execute(
            insert(bodies)
            .values(sha256=digest, content=row["envelope_json"])
            .on_conflict_do_nothing()
        )
        db.execute(
            insert(checkpoints).values(
                envelope_id=row["envelope_id"],
                revision=row["revision"],
                document_id=str(row["document_id"]),
                source_extraction_result_id=row["source_extraction_result_id"],
                body_sha=digest,
                provenance="historical_observed_checkpoint",
            )
        )
    op.execute(
        "CREATE TRIGGER immutable_validation_history BEFORE UPDATE OR DELETE ON validation_checkpoints FOR EACH ROW EXECUTE FUNCTION reject_validation_history_mutation()"
    )


def downgrade():
    raise RuntimeError("Historical checkpoints cannot be destructively downgraded")
