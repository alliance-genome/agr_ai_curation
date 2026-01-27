"""Add audit triggers to remaining tables

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2025-01-08

Extends audit logging coverage to ALL critical application tables.
The audit_trigger_func() already exists from migration h2i3j4k5l6m7.

This migration adds audit triggers (INSERT, UPDATE, DELETE) to:
- curation_flows: User-defined workflows (CRITICAL - we have a bug where saves don't persist!)
- prompt_templates: Versioned prompt storage
- prompt_execution_log: Prompt usage audit trail
- file_outputs: Generated CSV/TSV/JSON files

Previously audited tables (from h2i3j4k5l6m7):
- pdf_documents
- users
- feedback_reports

Note: Ontology tables (ontologies, ontology_terms, term_synonyms, term_relationships,
term_metadata) are not present in this database and are excluded from this migration.

Why this matters:
- Debugging data persistence issues (like curation_flows save bug)
- Tracking prompt version changes and rollbacks
- Auditing file generation and downloads
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'n8o9p0q1r2s3'
down_revision = 'm7n8o9p0q1r2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add audit triggers to all remaining tables."""

    # =============================================================================
    # CRITICAL APPLICATION TABLES (must have audit logging)
    # =============================================================================

    # --- curation_flows table (CRITICAL - we have a save persistence bug!) ---
    op.execute("""
        CREATE TRIGGER audit_curation_flows_insert
            AFTER INSERT ON curation_flows
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_curation_flows_update
            AFTER UPDATE ON curation_flows
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_curation_flows_delete
            AFTER DELETE ON curation_flows
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    # --- prompt_templates table ---
    op.execute("""
        CREATE TRIGGER audit_prompt_templates_insert
            AFTER INSERT ON prompt_templates
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_prompt_templates_update
            AFTER UPDATE ON prompt_templates
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_prompt_templates_delete
            AFTER DELETE ON prompt_templates
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    # --- prompt_execution_log table ---
    op.execute("""
        CREATE TRIGGER audit_prompt_execution_log_insert
            AFTER INSERT ON prompt_execution_log
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_prompt_execution_log_update
            AFTER UPDATE ON prompt_execution_log
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_prompt_execution_log_delete
            AFTER DELETE ON prompt_execution_log
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    # --- file_outputs table ---
    op.execute("""
        CREATE TRIGGER audit_file_outputs_insert
            AFTER INSERT ON file_outputs
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_file_outputs_update
            AFTER UPDATE ON file_outputs
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_file_outputs_delete
            AFTER DELETE ON file_outputs
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)



def downgrade() -> None:
    """Remove audit triggers from all tables added in this migration."""

    # Drop triggers in reverse order

    # file_outputs
    op.execute("DROP TRIGGER IF EXISTS audit_file_outputs_delete ON file_outputs;")
    op.execute("DROP TRIGGER IF EXISTS audit_file_outputs_update ON file_outputs;")
    op.execute("DROP TRIGGER IF EXISTS audit_file_outputs_insert ON file_outputs;")

    # prompt_execution_log
    op.execute("DROP TRIGGER IF EXISTS audit_prompt_execution_log_delete ON prompt_execution_log;")
    op.execute("DROP TRIGGER IF EXISTS audit_prompt_execution_log_update ON prompt_execution_log;")
    op.execute("DROP TRIGGER IF EXISTS audit_prompt_execution_log_insert ON prompt_execution_log;")

    # prompt_templates
    op.execute("DROP TRIGGER IF EXISTS audit_prompt_templates_delete ON prompt_templates;")
    op.execute("DROP TRIGGER IF EXISTS audit_prompt_templates_update ON prompt_templates;")
    op.execute("DROP TRIGGER IF EXISTS audit_prompt_templates_insert ON prompt_templates;")

    # curation_flows
    op.execute("DROP TRIGGER IF EXISTS audit_curation_flows_delete ON curation_flows;")
    op.execute("DROP TRIGGER IF EXISTS audit_curation_flows_update ON curation_flows;")
    op.execute("DROP TRIGGER IF EXISTS audit_curation_flows_insert ON curation_flows;")
