"""CLI interface for Weaviate operations."""

import asyncio
import json
import logging
from pathlib import Path
import sys
from typing import Any, Optional
from uuid import UUID

import click

from .connection import connect_to_weaviate, health_check, get_collection_info
from .documents import (
    _delete_sql_row,
    delete_document as delete_document,
    delete_paper_chunks,
    delete_paper_object,
    get_document,
    list_documents,
    re_embed_document,
)
from .chunks import get_chunks
from .settings import get_embedding_config, get_available_models

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@click.group()
@click.option('--url', default='http://localhost:8080', help='Weaviate instance URL')
@click.option('--api-key', help='Weaviate API key (if required)')
@click.pass_context
def cli(ctx, url: str, api_key: Optional[str]):
    """Weaviate client CLI for document and chunk operations."""
    ctx.ensure_object(dict)
    try:
        connect_to_weaviate(url, api_key)
        ctx.obj['connected'] = True
    except Exception as e:
        logger.error('Failed to connect to Weaviate: %s', e)
        ctx.obj['connected'] = False


@cli.command()
@click.option('--user-id', required=True, help='User ID for tenant scoping (FR-011, FR-014)')
@click.option('--page', default=1, type=int, help='Page number (1-indexed)')
@click.option('--page-size', default=20, type=int, help='Items per page')
@click.option('--search', help='Search term for filename/metadata')
@click.option('--status', help='Filter by embedding status (comma-separated)')
@click.option('--sort-by', default='creationDate',
              type=click.Choice(['filename', 'creationDate', 'fileSize', 'vectorCount']),
              help='Sort field')
@click.option('--sort-order', default='desc',
              type=click.Choice(['asc', 'desc']),
              help='Sort direction')
@click.option('--output-format', type=click.Choice(['json', 'table']), default='table',
              help='Output format')
@click.pass_context
def list_documents_cmd(ctx, user_id: str, page: int, page_size: int, search: Optional[str],
                       status: Optional[str], sort_by: str, sort_order: str,
                       output_format: str):
    """List documents with pagination and filtering.

    Requires --user-id to enforce tenant isolation (FR-011, FR-014).
    """
    if not ctx.obj.get('connected'):
        click.echo("Error: Not connected to Weaviate", err=True)
        sys.exit(1)

    try:
        embedding_status = status.split(',') if status else None
        result = list_documents(
            user_id,
            page=page,
            page_size=page_size,
            search_term=search,
            embedding_status=embedding_status,
            sort_by=sort_by,
            sort_order=sort_order
        )

        if output_format == 'json':
            click.echo(json.dumps(result, indent=2))
        else:
            # Table format
            docs = result.get('documents', [])

            if not docs:
                click.echo("No documents found")
                return

            total = result['total']
            limit = result['limit']
            offset = result['offset']
            current_page = offset // limit + 1
            total_pages = (total + limit - 1) // limit

            click.echo(f"\nDocuments (Page {current_page}/{total_pages}):")
            click.echo("-" * 80)

            for doc in docs:
                click.echo(f"ID: {doc.get('document_id', 'N/A')}")
                click.echo(f"  Filename: {doc.get('filename', 'N/A')}")
                size = doc.get("file_size_bytes")
                # A paper of supplements only has no main file and so no size.
                click.echo(f"  Size: {size:,} bytes" if size is not None else "  Size: no main file")
                click.echo(f"  Status: {doc.get('embedding_status', 'N/A')}")
                click.echo(f"  Chunks: {doc.get('chunk_count', 0)}")
                click.echo(f"  Vectors: {doc.get('vector_count', 0)}")
                click.echo("-" * 80)

            click.echo(f"\nTotal: {total} documents")

    except Exception as e:
        click.echo(f"Error listing documents: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument('document_id')
@click.option('--user-id', required=True, help='User ID for tenant scoping (FR-011, FR-014)')
@click.option('--show-chunks', is_flag=True, help='Show chunk preview')
@click.option('--output-format', type=click.Choice(['json', 'text']), default='text',
              help='Output format')
def get_document_cmd(document_id: str, user_id: str, show_chunks: bool, output_format: str):
    """Get detailed information about a specific document.

    Requires --user-id to enforce tenant isolation (FR-011, FR-014).
    """
    try:
        import asyncio
        result = asyncio.run(get_document(user_id, document_id))

        if output_format == 'json':
            click.echo(json.dumps(result, indent=2))
        else:
            doc = result.get('document', {})
            chunks = result.get('chunks', [])

            click.echo("\nDocument Details:")
            click.echo("-" * 50)
            click.echo(f"ID: {document_id}")
            click.echo(f"Filename: {doc.get('filename', 'N/A')}")
            click.echo(f"Size: {doc.get('fileSize', 0):,} bytes")
            click.echo(f"Processing Status: {doc.get('processingStatus', 'N/A')}")
            click.echo(f"Embedding Status: {doc.get('embeddingStatus', 'N/A')}")
            click.echo(f"Total Chunks: {result['total_chunks']}")
            click.echo(f"Created: {doc.get('creationDate', 'N/A')}")
            click.echo(f"Last Accessed: {doc.get('lastAccessedDate', 'N/A')}")

            if show_chunks and chunks:
                click.echo(f"\nChunk Preview (first {len(chunks)} chunks):")
                click.echo("-" * 50)
                for i, chunk in enumerate(chunks, 1):
                    content = chunk.get('content', '')[:100]
                    click.echo(f"[{i}] Page {chunk.get('pageNumber', 'N/A')} - "
                             f"{chunk.get('elementType', 'N/A')}")
                    click.echo(f"    {content}...")

    except Exception as e:
        click.echo(f"Error getting document: {e}", err=True)
        sys.exit(1)


def _tenant_paper_dir_has_artifacts(base_storage: Path, user_id: str, document_id: str) -> tuple[bool, str | None]:
    """Return ``(has_artifacts_or_unverifiable, error_message)`` for an orphan tenant paper dir."""
    from src.lib.document_files.storage_paths import (
        StoredPathOutsideOwnerStorage,
        owner_storage_path,
    )

    try:
        owner_root = owner_storage_path(base_storage / user_id, base_storage, user_id)
    except StoredPathOutsideOwnerStorage:
        return True, "Access denied: file path validation failed"

    paper_dir = owner_root / document_id
    try:
        if paper_dir.is_symlink():
            return (
                True,
                "Cannot delete orphan Weaviate document while tenant storage entry still exists",
            )
        if not paper_dir.exists():
            return False, None
        if not paper_dir.is_dir() or any(paper_dir.iterdir()):
            return (
                True,
                "Cannot delete orphan Weaviate document while tenant storage directory still contains stored files",
            )
        return False, None
    except OSError:
        return (
            True,
            "Cannot verify tenant storage directory is empty before deleting orphan Weaviate document",
        )


def _inspect_cli_sql_owner(
    user_id: str,
    document_uuid: UUID,
    *,
    connection: Any = None,
) -> tuple[str | None, bool]:
    """Validate SQL ownership and removable paths before touching jobs or Weaviate.

    Returns ``(error_message, has_sql_owner)``.
    """
    from sqlalchemy import select
    from src.config import get_pdf_storage_path
    from src.lib.document_files.paper_lock import open_paper_lock_session
    from src.lib.document_files.service import (
        paper_cleanup_snapshot,
        require_removable_paper_paths,
    )
    from src.lib.document_files.storage_paths import StoredPathOutsideOwnerStorage
    from src.models.sql.database import get_db
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    db_gen = None
    bound_session = open_paper_lock_session(connection)
    if bound_session is not None:
        db = bound_session
    else:
        db_gen = get_db()
        db = next(db_gen)
    try:
        base_storage = Path(get_pdf_storage_path())
        pdf_doc = db.get(PDFDocument, document_uuid)
        if pdf_doc is None:
            has_artifacts, dir_error = _tenant_paper_dir_has_artifacts(
                base_storage,
                user_id,
                str(document_uuid),
            )
            if has_artifacts:
                return dir_error, False
            return None, False
        db_user = db.execute(select(User).where(User.auth_sub == user_id)).scalar_one_or_none()
        if db_user is None or pdf_doc.user_id != db_user.id:
            return "Document ownership mismatch in PostgreSQL", False
        cleanup_snapshot = paper_cleanup_snapshot(pdf_doc)
        try:
            require_removable_paper_paths(cleanup_snapshot, base_storage, user_id)
        except StoredPathOutsideOwnerStorage:
            return "Access denied: file path validation failed", True
        return None, True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        close_gen = getattr(db_gen, "close", None)
        if callable(close_gen):
            close_gen()


def _remove_cli_owned_paper_files(
    user_id: str,
    document_uuid: UUID,
    *,
    connection: Any = None,
) -> None:
    """Lock the owned SQL paper row and remove all stored files before owner deletion."""
    from sqlalchemy import select
    from src.config import get_pdf_storage_path
    from src.lib.document_files.paper_lock import open_paper_lock_session
    from src.lib.document_files.service import (
        paper_cleanup_snapshot,
        remove_paper_stored_files,
    )
    from src.models.sql.database import get_db
    from src.models.sql.pdf_document import PDFDocument
    from src.models.sql.user import User

    db_gen = None
    bound_session = open_paper_lock_session(connection)
    if bound_session is not None:
        db = bound_session
    else:
        db_gen = get_db()
        db = next(db_gen)
    try:
        pdf_doc = db.execute(
            select(PDFDocument).where(PDFDocument.id == document_uuid).with_for_update()
        ).scalar_one_or_none()
        if pdf_doc is None:
            return
        db_user = db.execute(select(User).where(User.auth_sub == user_id)).scalar_one_or_none()
        if db_user is None or pdf_doc.user_id != db_user.id:
            raise PermissionError("Document ownership mismatch in PostgreSQL")
        cleanup_values = paper_cleanup_snapshot(pdf_doc)
        remove_paper_stored_files(
            cleanup_values,
            base_storage=Path(get_pdf_storage_path()),
            user_sub=user_id,
            document_id=str(document_uuid),
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        close_gen = getattr(db_gen, "close", None)
        if callable(close_gen):
            close_gen()


async def _delete_document_for_cli(user_id: str, document_id: str) -> dict[str, Any]:
    """Delete a document via CLI under paper_operation_lock with ownership, active-job, and files-before-owner checks."""
    from src.lib.document_cleanup import (
        delete_drain_context,
        drain_shielded,
        report_sql_cleanup_failure,
        run_thread_drained,
    )
    from src.lib.document_files.paper_lock import PaperOperationInProgress, paper_operation_lock
    from src.lib.pdf_jobs import service as pdf_job_service

    if not user_id:
        raise ValueError("user_id is required for tenant-scoped document deletion (FR-011, FR-014)")
    document_uuid = UUID(str(document_id))
    canonical_document_id = str(document_uuid)

    try:
        async with paper_operation_lock(document_uuid) as lock_conn:
            # Verify SQL ownership and stored-file preflight FIRST so a wrong user can
            # never mutate or disclose another owner's job state via stale reconciliation.
            preflight_error, has_sql_owner = await run_thread_drained(
                _inspect_cli_sql_owner,
                user_id,
                document_uuid,
                connection=lock_conn,
            )
            if preflight_error is not None:
                return {
                    "success": False,
                    "message": preflight_error,
                    "documentId": canonical_document_id,
                    "postgres_deleted": False,
                }

            active_job = await run_thread_drained(
                pdf_job_service.get_active_job_for_document,
                document_id=document_uuid,
                reconcile_stale=True,
                connection=lock_conn,
            )
            if active_job is not None:
                return {
                    "success": False,
                    "message": (
                        "Cannot delete document while processing job is active "
                        f"(status '{active_job.status}')"
                    ),
                    "documentId": canonical_document_id,
                    "postgres_deleted": False,
                }

            with delete_drain_context() as drain_state:
                async def _complete() -> dict[str, Any]:
                    chunk_result = await delete_paper_chunks(user_id, canonical_document_id)
                    if not chunk_result.get("success"):
                        return {**chunk_result, "postgres_deleted": False}

                    if has_sql_owner:
                        try:
                            await run_thread_drained(
                                _remove_cli_owned_paper_files,
                                user_id,
                                document_uuid,
                                connection=lock_conn,
                                cancellable_before_start=False,
                            )
                        except Exception as file_err:
                            logger.warning(
                                "CLI delete failed to remove stored files (%s)",
                                type(file_err).__name__,
                                extra={"sentry_skip_event": True},
                            )
                            return {
                                "success": False,
                                "message": f"Failed to remove stored files ({type(file_err).__name__})",
                                "documentId": canonical_document_id,
                                "chunks_deleted": chunk_result.get("chunks_deleted", 0),
                                "chunks_matched": chunk_result.get("chunks_matched", 0),
                                "postgres_deleted": False,
                            }

                    parent_result = await delete_paper_object(
                        user_id,
                        canonical_document_id,
                        allow_missing=True,
                    )
                    if not parent_result.get("success"):
                        return {
                            **parent_result,
                            "chunks_deleted": chunk_result.get("chunks_deleted", 0),
                            "chunks_matched": chunk_result.get("chunks_matched", 0),
                            "postgres_deleted": False,
                        }

                    sql_delete_failed = False
                    try:
                        postgres_deleted = await run_thread_drained(
                            _delete_sql_row,
                            user_id,
                            canonical_document_id,
                            connection=lock_conn,
                            cancellable_before_start=False,
                        )
                    except Exception as db_err:
                        report_sql_cleanup_failure(
                            component="weaviate_client",
                            document_id=canonical_document_id,
                            exception_type=type(db_err).__name__,
                        )
                        postgres_deleted = False
                        sql_delete_failed = True

                    if has_sql_owner and (sql_delete_failed or not postgres_deleted):
                        return {
                            "success": False,
                            "message": "Failed to delete document record from PostgreSQL",
                            "documentId": canonical_document_id,
                            "chunks_deleted": chunk_result.get("chunks_deleted", 0),
                            "chunks_matched": chunk_result.get("chunks_matched", 0),
                            "postgres_deleted": False,
                        }

                    deleted_any = bool(parent_result.get("deleted") or postgres_deleted)
                    return {
                        "success": deleted_any,
                        "message": (
                            "Document and chunks deleted successfully"
                            if deleted_any
                            else "Document deletion reported as unsuccessful"
                        ),
                        "documentId": canonical_document_id,
                        "chunks_deleted": chunk_result.get("chunks_deleted", 0),
                        "chunks_matched": chunk_result.get("chunks_matched", 0),
                        "postgres_deleted": postgres_deleted,
                    }

                def _mark_cancelled() -> None:
                    drain_state.cancelled = True

                return await drain_shielded(
                    _complete(),
                    started=lambda: drain_state.started,
                    on_cancel=_mark_cancelled,
                )
    except PaperOperationInProgress:
        return {
            "success": False,
            "message": "Another operation on this paper is in progress; try again in a moment.",
            "documentId": canonical_document_id,
            "postgres_deleted": False,
        }


@cli.command()
@click.argument('document_id')
@click.option('--user-id', required=True, help='User ID for tenant scoping (FR-011, FR-014)')
@click.confirmation_option(prompt='Are you sure you want to delete this document?')
def delete_document_cmd(document_id: str, user_id: str):
    """Delete a document and all its chunks.

    Requires --user-id to enforce tenant isolation (FR-011, FR-014).
    """
    try:
        result = asyncio.run(_delete_document_for_cli(user_id, document_id))

        if result['success']:
            click.echo(f"✓ {result['message']}")
        else:
            click.echo(f"✗ {result['message']}", err=True)
            if 'error' in result:
                click.echo(f"  Error: {result['error']['details']}", err=True)
            sys.exit(1)

    except Exception as e:
        click.echo(f"Error deleting document: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument('document_id')
@click.option('--user-id', required=True, help='User ID for tenant scoping (FR-011, FR-014)')
def re_embed_document_cmd(document_id: str, user_id: str):
    """Trigger re-embedding for a document.

    Requires --user-id to enforce tenant isolation (FR-011, FR-014).
    """
    try:
        import asyncio
        result = asyncio.run(re_embed_document(document_id, user_id))

        if result['success']:
            click.echo(f"✓ {result['message']}")
        else:
            click.echo(f"✗ {result['message']}", err=True)
            if 'error' in result:
                click.echo(f"  Error: {result['error']['details']}", err=True)
            sys.exit(1)

    except Exception as e:
        click.echo(f"Error triggering re-embedding: {e}", err=True)
        sys.exit(1)


@cli.command()
def health_check_cmd():
    """Check Weaviate cluster health."""
    try:
        result = health_check()

        if result.get('healthy'):
            click.echo("✓ Weaviate is healthy")
            click.echo(f"  Version: {result.get('version', 'unknown')}")
            click.echo(f"  Nodes: {len(result.get('nodes', []))}")

            modules = result.get('modules', {})
            if modules:
                click.echo(f"  Modules: {', '.join(modules.keys())}")
        else:
            click.echo("✗ Weaviate is unhealthy", err=True)
            click.echo(f"  Error: {result.get('error', 'Unknown error')}", err=True)
            sys.exit(1)

    except Exception as e:
        click.echo(f"Error checking health: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument('collection_name', default='PDFDocument')
def collection_info(collection_name: str):
    """Get information about a Weaviate collection."""
    try:
        result = get_collection_info(collection_name)

        if 'error' in result:
            click.echo(f"Error: {result['error']}", err=True)
            sys.exit(1)

        click.echo(f"\nCollection: {result.get('name', 'N/A')}")
        click.echo(f"Vectorizer: {result.get('vectorizer', 'none')}")
        click.echo(f"Object Count: {result.get('object_count', 0):,}")

        properties = result.get('properties', [])
        if properties:
            click.echo(f"\nProperties ({len(properties)}):")
            for prop in properties:
                click.echo(f"  - {prop.get('name', 'N/A')} ({', '.join(prop.get('dataType', []))})")

    except Exception as e:
        click.echo(f"Error getting collection info: {e}", err=True)
        sys.exit(1)


@cli.command()
def embedding_config():
    """Show current embedding configuration."""
    try:
        config = get_embedding_config()
        click.echo("\nEmbedding Configuration:")
        click.echo("-" * 40)
        for key, value in config.items():
            click.echo(f"{key}: {value}")

    except Exception as e:
        click.echo(f"Error getting embedding config: {e}", err=True)
        sys.exit(1)


@cli.command()
def available_models():
    """List available embedding models."""
    try:
        models = get_available_models()
        click.echo("\nAvailable Embedding Models:")
        click.echo("-" * 60)

        current_provider = None
        for model in models:
            if model['provider'] != current_provider:
                current_provider = model['provider']
                click.echo(f"\n{current_provider.upper()}:")

            click.echo(f"  - {model['modelName']}")
            click.echo(f"    Dimensions: {model['dimensions']}")
            click.echo(f"    Max Tokens: {model['maxTokens']}")

    except Exception as e:
        click.echo(f"Error listing models: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument('document_id')
@click.option('--user-id', required=True, help='User ID for tenant scoping (FR-011, FR-014)')
@click.option('--page', default=1, type=int, help='Page number')
@click.option('--page-size', default=50, type=int, help='Items per page')
@click.option('--include-metadata', is_flag=True, default=True, help='Include chunk metadata')
def get_chunks_cmd(document_id: str, user_id: str, page: int, page_size: int,
                  include_metadata: bool):
    """Get the chunks of a document's ready files.

    Requires --user-id to enforce tenant isolation (FR-011, FR-014).
    """
    try:
        # T038: Build pagination dict and pass user_id for tenant scoping
        import asyncio
        pagination = {
            'page': page,
            'page_size': page_size,
            'include_metadata': include_metadata
        }

        from src.lib.document_files.readable import resolve_readable_files

        result = asyncio.run(get_chunks(
            document_id=document_id,
            pagination=pagination,
            user_id=user_id,
            readable=resolve_readable_files(document_id, user_id),
        ))

        chunks = result.get('chunks', [])
        total = result.get('total', 0)

        # Calculate pagination info from response
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0
        current_page = page

        click.echo(f"\nChunks for document {document_id}:")
        click.echo(f"(Page {current_page}/{total_pages})")
        click.echo("-" * 60)

        for chunk in chunks:
            # Use snake_case keys from new response schema
            content_preview = chunk.get('content', '')[:100]
            click.echo(f"[{chunk.get('chunk_index', 0)}] "
                     f"Page {chunk.get('page_number', 'N/A')} - "
                     f"{chunk.get('element_type', 'N/A')}")
            click.echo(f"  {content_preview}...")

        click.echo(f"\nTotal: {total} chunks")

    except Exception as e:
        click.echo(f"Error getting chunks: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
