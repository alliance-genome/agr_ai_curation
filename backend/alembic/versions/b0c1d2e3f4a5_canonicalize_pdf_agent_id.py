"""Canonicalize PDF agent identity to pdf_extraction.

Revision ID: b0c1d2e3f4a5
Revises: 1f2e3d4c5b6a
Create Date: 2026-03-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b0c1d2e3f4a5"
down_revision = "1f2e3d4c5b6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # If both keys exist, keep canonical row and drop legacy duplicate.
    op.execute(
        sa.text(
            """
            DELETE FROM agents
            WHERE agent_key = 'pdf'
              AND EXISTS (
                SELECT 1 FROM agents a2 WHERE a2.agent_key = 'pdf_extraction'
              )
            """
        )
    )

    # System-agent key canonicalization in unified agents table.
    op.execute(
        sa.text(
            """
            UPDATE agents
            SET agent_key = 'pdf_extraction'
            WHERE agent_key = 'pdf'
            """
        )
    )

    # Ensure component/template pointers use canonical key.
    op.execute(
        sa.text(
            """
            UPDATE agents
            SET group_rules_component = 'pdf_extraction'
            WHERE group_rules_component = 'pdf'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE agents
            SET template_source = 'pdf_extraction'
            WHERE template_source = 'pdf'
            """
        )
    )

    # Remove potential uniqueness conflicts where both legacy and canonical rows exist.
    op.execute(
        sa.text(
            """
            DELETE FROM prompt_templates legacy
            USING prompt_templates canonical
            WHERE legacy.agent_name = 'pdf'
              AND canonical.agent_name = 'pdf_extraction'
              AND legacy.prompt_type = canonical.prompt_type
              AND legacy.version = canonical.version
              AND (
                legacy.group_id = canonical.group_id
                OR (legacy.group_id IS NULL AND canonical.group_id IS NULL)
              )
            """
        )
    )

    # Canonicalize prompt ownership keys.
    op.execute(
        sa.text(
            """
            UPDATE prompt_templates
            SET agent_name = 'pdf_extraction'
            WHERE agent_name = 'pdf'
            """
        )
    )

    # Preserve consistency for prompt execution logs when table exists.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'prompt_execution_log'
                  AND column_name = 'agent_name'
              ) THEN
                UPDATE prompt_execution_log
                SET agent_name = 'pdf_extraction'
                WHERE agent_name = 'pdf';
              END IF;
            END $$;
            """
        )
    )

    # Update persisted flow JSON definitions to canonical agent_id.
    op.execute(
        sa.text(
            """
            UPDATE curation_flows
            SET flow_definition = regexp_replace(
                flow_definition::text,
                '("agent_id"\\s*:\\s*)"pdf"',
                E'\\1"pdf_extraction"',
                'g'
            )::jsonb
            WHERE flow_definition::text ~ '("agent_id"\\s*:\\s*)"pdf"'
            """
        )
    )


def downgrade() -> None:
    # Revert persisted flow JSON definitions.
    # Remove potential uniqueness conflicts before renaming back to `pdf`.
    op.execute(
        sa.text(
            """
            DELETE FROM prompt_templates canonical
            USING prompt_templates legacy
            WHERE canonical.agent_name = 'pdf_extraction'
              AND legacy.agent_name = 'pdf'
              AND canonical.prompt_type = legacy.prompt_type
              AND canonical.version = legacy.version
              AND (
                canonical.group_id = legacy.group_id
                OR (canonical.group_id IS NULL AND legacy.group_id IS NULL)
              )
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE curation_flows
            SET flow_definition = regexp_replace(
                flow_definition::text,
                '("agent_id"\\s*:\\s*)"pdf_extraction"',
                E'\\1"pdf"',
                'g'
            )::jsonb
            WHERE flow_definition::text ~ '("agent_id"\\s*:\\s*)"pdf_extraction"'
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE prompt_templates
            SET agent_name = 'pdf'
            WHERE agent_name = 'pdf_extraction'
            """
        )
    )

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'prompt_execution_log'
                  AND column_name = 'agent_name'
              ) THEN
                UPDATE prompt_execution_log
                SET agent_name = 'pdf'
                WHERE agent_name = 'pdf_extraction';
              END IF;
            END $$;
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE agents
            SET group_rules_component = 'pdf'
            WHERE group_rules_component = 'pdf_extraction'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE agents
            SET template_source = 'pdf'
            WHERE template_source = 'pdf_extraction'
            """
        )
    )

    # If both keys exist, keep legacy row and drop canonical duplicate.
    op.execute(
        sa.text(
            """
            DELETE FROM agents
            WHERE agent_key = 'pdf_extraction'
              AND EXISTS (
                SELECT 1 FROM agents a2 WHERE a2.agent_key = 'pdf'
              )
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE agents
            SET agent_key = 'pdf'
            WHERE agent_key = 'pdf_extraction'
            """
        )
    )
