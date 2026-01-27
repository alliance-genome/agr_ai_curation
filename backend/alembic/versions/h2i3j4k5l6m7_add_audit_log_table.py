"""Add comprehensive audit_log table with triggers

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2025-12-12

This migration creates a comprehensive audit logging system that captures
ALL database changes (INSERT, UPDATE, DELETE) on key tables with full
before/after state for debugging document lifecycle issues.

Audited tables:
- pdf_documents: Document uploads, status changes, deletions
- users: User provisioning and changes
- feedback_reports: Feedback submissions
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision = 'h2i3j4k5l6m7'
down_revision = 'd2e3f4a5b6c7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the audit_log table
    op.create_table(
        'audit_log',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('event_id', UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('event_timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),

        # What happened
        sa.Column('table_name', sa.String(100), nullable=False),
        sa.Column('operation', sa.String(10), nullable=False),  # INSERT, UPDATE, DELETE
        sa.Column('row_id', sa.String(100), nullable=True),  # Primary key of affected row (as string for flexibility)

        # Full row data
        sa.Column('old_data', JSONB, nullable=True),  # Previous state (for UPDATE/DELETE)
        sa.Column('new_data', JSONB, nullable=True),  # New state (for INSERT/UPDATE)

        # Change details (for UPDATE - shows only changed fields)
        sa.Column('changed_fields', JSONB, nullable=True),  # {field: {old: x, new: y}}

        # Context
        sa.Column('db_user', sa.String(255), nullable=True),  # PostgreSQL session user (current_user)
        sa.Column('client_ip', sa.String(50), nullable=True),  # Client IP if available
        sa.Column('application_name', sa.String(255), nullable=True),  # Application identifier
        sa.Column('transaction_id', sa.BigInteger, nullable=True),  # PostgreSQL transaction ID

        # Extra debugging info
        sa.Column('query_text', sa.Text, nullable=True),  # The actual SQL query (if available)
        sa.Column('call_stack', sa.Text, nullable=True),  # For debugging
        sa.Column('extra_context', JSONB, nullable=True),  # Any additional context
    )

    # Create indexes for common queries
    op.create_index('ix_audit_log_event_timestamp', 'audit_log', ['event_timestamp'])
    op.create_index('ix_audit_log_table_name', 'audit_log', ['table_name'])
    op.create_index('ix_audit_log_operation', 'audit_log', ['operation'])
    op.create_index('ix_audit_log_row_id', 'audit_log', ['row_id'])
    op.create_index('ix_audit_log_table_operation', 'audit_log', ['table_name', 'operation'])
    op.create_index('ix_audit_log_table_row', 'audit_log', ['table_name', 'row_id'])

    # Create the audit trigger function
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_trigger_func()
        RETURNS TRIGGER AS $$
        DECLARE
            old_data JSONB := NULL;
            new_data JSONB := NULL;
            changed_fields JSONB := NULL;
            row_id_value TEXT := NULL;
            key_name TEXT;
            old_val JSONB;
            new_val JSONB;
        BEGIN
            -- Capture old data for UPDATE and DELETE
            IF (TG_OP = 'UPDATE' OR TG_OP = 'DELETE') THEN
                old_data := to_jsonb(OLD);
                -- Try to get the primary key value
                IF old_data ? 'id' THEN
                    row_id_value := old_data->>'id';
                ELSIF old_data ? 'user_id' THEN
                    row_id_value := old_data->>'user_id';
                END IF;
            END IF;

            -- Capture new data for INSERT and UPDATE
            IF (TG_OP = 'INSERT' OR TG_OP = 'UPDATE') THEN
                new_data := to_jsonb(NEW);
                -- Try to get the primary key value
                IF new_data ? 'id' THEN
                    row_id_value := new_data->>'id';
                ELSIF new_data ? 'user_id' THEN
                    row_id_value := new_data->>'user_id';
                END IF;
            END IF;

            -- For UPDATE, calculate which fields changed
            IF (TG_OP = 'UPDATE') THEN
                changed_fields := '{}'::JSONB;
                FOR key_name IN SELECT jsonb_object_keys(new_data)
                LOOP
                    old_val := old_data->key_name;
                    new_val := new_data->key_name;
                    -- Compare values (handling NULL cases)
                    IF (old_val IS DISTINCT FROM new_val) THEN
                        changed_fields := changed_fields || jsonb_build_object(
                            key_name,
                            jsonb_build_object('old', old_val, 'new', new_val)
                        );
                    END IF;
                END LOOP;

                -- If no fields actually changed, still record it but note that
                IF changed_fields = '{}'::JSONB THEN
                    changed_fields := '{"_note": "UPDATE called but no fields changed"}'::JSONB;
                END IF;
            END IF;

            -- Insert the audit record
            INSERT INTO audit_log (
                table_name,
                operation,
                row_id,
                old_data,
                new_data,
                changed_fields,
                db_user,
                client_ip,
                application_name,
                transaction_id,
                extra_context
            ) VALUES (
                TG_TABLE_NAME,
                TG_OP,
                row_id_value,
                old_data,
                new_data,
                changed_fields,
                current_user,
                inet_client_addr()::TEXT,
                current_setting('application_name', true),
                txid_current(),
                jsonb_build_object(
                    'trigger_name', TG_NAME,
                    'trigger_when', TG_WHEN,
                    'trigger_level', TG_LEVEL,
                    'schema_name', TG_TABLE_SCHEMA
                )
            );

            -- Return the appropriate row
            IF (TG_OP = 'DELETE') THEN
                RETURN OLD;
            ELSE
                RETURN NEW;
            END IF;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """)

    # Create triggers for pdf_documents table
    op.execute("""
        CREATE TRIGGER audit_pdf_documents_insert
            AFTER INSERT ON pdf_documents
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_pdf_documents_update
            AFTER UPDATE ON pdf_documents
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_pdf_documents_delete
            AFTER DELETE ON pdf_documents
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    # Create triggers for users table
    op.execute("""
        CREATE TRIGGER audit_users_insert
            AFTER INSERT ON users
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_users_update
            AFTER UPDATE ON users
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    op.execute("""
        CREATE TRIGGER audit_users_delete
            AFTER DELETE ON users
            FOR EACH ROW
            EXECUTE FUNCTION audit_trigger_func();
    """)

    # Create triggers for feedback_reports table (only if table exists - it may have been moved to ai_curation)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'feedback_reports') THEN
                CREATE TRIGGER audit_feedback_reports_insert
                    AFTER INSERT ON feedback_reports
                    FOR EACH ROW
                    EXECUTE FUNCTION audit_trigger_func();
                CREATE TRIGGER audit_feedback_reports_update
                    AFTER UPDATE ON feedback_reports
                    FOR EACH ROW
                    EXECUTE FUNCTION audit_trigger_func();
                CREATE TRIGGER audit_feedback_reports_delete
                    AFTER DELETE ON feedback_reports
                    FOR EACH ROW
                    EXECUTE FUNCTION audit_trigger_func();
            END IF;
        END $$;
    """)

    # Create a helper view for easier querying
    op.execute("""
        CREATE VIEW audit_log_readable AS
        SELECT
            id,
            event_id,
            event_timestamp AT TIME ZONE 'UTC' as event_timestamp_utc,
            event_timestamp AT TIME ZONE 'America/New_York' as event_timestamp_eastern,
            table_name,
            operation,
            row_id,
            CASE
                WHEN operation = 'INSERT' THEN 'Created new ' || table_name || ' record'
                WHEN operation = 'UPDATE' THEN 'Updated ' || table_name || ' record'
                WHEN operation = 'DELETE' THEN 'Deleted ' || table_name || ' record'
            END as description,
            changed_fields,
            old_data,
            new_data,
            db_user,
            application_name,
            transaction_id
        FROM audit_log
        ORDER BY event_timestamp DESC;
    """)

    # Create a function to get recent activity for a specific document
    op.execute("""
        CREATE OR REPLACE FUNCTION get_document_audit_history(doc_id TEXT)
        RETURNS TABLE (
            event_time TIMESTAMPTZ,
            operation TEXT,
            changed_fields JSONB,
            old_data JSONB,
            new_data JSONB,
            db_user TEXT
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT
                event_timestamp,
                al.operation,
                al.changed_fields,
                al.old_data,
                al.new_data,
                al.db_user::TEXT
            FROM audit_log al
            WHERE al.table_name = 'pdf_documents'
              AND al.row_id = doc_id
            ORDER BY event_timestamp DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # Create a function to get all activity in a time range
    op.execute("""
        CREATE OR REPLACE FUNCTION get_audit_activity(
            start_time TIMESTAMPTZ DEFAULT NOW() - INTERVAL '1 hour',
            end_time TIMESTAMPTZ DEFAULT NOW(),
            filter_table TEXT DEFAULT NULL
        )
        RETURNS TABLE (
            event_time TIMESTAMPTZ,
            table_name TEXT,
            operation TEXT,
            row_id TEXT,
            changed_fields JSONB,
            db_user TEXT
        ) AS $$
        BEGIN
            RETURN QUERY
            SELECT
                al.event_timestamp,
                al.table_name::TEXT,
                al.operation::TEXT,
                al.row_id::TEXT,
                al.changed_fields,
                al.db_user::TEXT
            FROM audit_log al
            WHERE al.event_timestamp BETWEEN start_time AND end_time
              AND (filter_table IS NULL OR al.table_name = filter_table)
            ORDER BY al.event_timestamp DESC;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    # Drop helper functions
    op.execute("DROP FUNCTION IF EXISTS get_audit_activity(TIMESTAMPTZ, TIMESTAMPTZ, TEXT);")
    op.execute("DROP FUNCTION IF EXISTS get_document_audit_history(TEXT);")

    # Drop the view
    op.execute("DROP VIEW IF EXISTS audit_log_readable;")

    # Drop triggers for feedback_reports
    op.execute("DROP TRIGGER IF EXISTS audit_feedback_reports_delete ON feedback_reports;")
    op.execute("DROP TRIGGER IF EXISTS audit_feedback_reports_update ON feedback_reports;")
    op.execute("DROP TRIGGER IF EXISTS audit_feedback_reports_insert ON feedback_reports;")

    # Drop triggers for users
    op.execute("DROP TRIGGER IF EXISTS audit_users_delete ON users;")
    op.execute("DROP TRIGGER IF EXISTS audit_users_update ON users;")
    op.execute("DROP TRIGGER IF EXISTS audit_users_insert ON users;")

    # Drop triggers for pdf_documents
    op.execute("DROP TRIGGER IF EXISTS audit_pdf_documents_delete ON pdf_documents;")
    op.execute("DROP TRIGGER IF EXISTS audit_pdf_documents_update ON pdf_documents;")
    op.execute("DROP TRIGGER IF EXISTS audit_pdf_documents_insert ON pdf_documents;")

    # Drop the trigger function
    op.execute("DROP FUNCTION IF EXISTS audit_trigger_func();")

    # Drop indexes
    op.drop_index('ix_audit_log_table_row', table_name='audit_log')
    op.drop_index('ix_audit_log_table_operation', table_name='audit_log')
    op.drop_index('ix_audit_log_row_id', table_name='audit_log')
    op.drop_index('ix_audit_log_operation', table_name='audit_log')
    op.drop_index('ix_audit_log_table_name', table_name='audit_log')
    op.drop_index('ix_audit_log_event_timestamp', table_name='audit_log')

    # Drop the audit_log table
    op.drop_table('audit_log')
