"""Canonicalize PDF agent identity to pdf_extraction.

Revision ID: a9b0c1d2e3f4
Revises: z8a9b0c1d2e3
Create Date: 2026-03-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a9b0c1d2e3f4"
down_revision = "z8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # System-agent key canonicalization in unified agents table.
    op.execute(
        sa.text(
            """
            UPDATE agents
            SET agent_key = 'pdf_extraction'
            WHERE agent_key = 'pdf'
              AND NOT EXISTS (
                SELECT 1 FROM agents a2 WHERE a2.agent_key = 'pdf_extraction'
              )
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
                WHERE table_name = 'prompt_execution_logs'
                  AND column_name = 'agent_name'
              ) THEN
                UPDATE prompt_execution_logs
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
                WHERE table_name = 'prompt_execution_logs'
                  AND column_name = 'agent_name'
              ) THEN
                UPDATE prompt_execution_logs
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

    op.execute(
        sa.text(
            """
            UPDATE agents
            SET agent_key = 'pdf'
            WHERE agent_key = 'pdf_extraction'
              AND NOT EXISTS (
                SELECT 1 FROM agents a2 WHERE a2.agent_key = 'pdf'
              )
            """
        )
    )
