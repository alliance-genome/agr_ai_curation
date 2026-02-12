"""CLI interface for Weaviate operations."""

import json
import logging
import sys
from typing import Optional

import click

from .connection import connect_to_weaviate, close_connection, health_check, get_collection_info
from .documents import list_documents, get_document, delete_document, re_embed_document
from .chunks import get_chunks
from .settings import get_embedding_config, get_collection_settings, get_available_models

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
            pagination = result.get('pagination', {})

            if not docs:
                click.echo("No documents found")
                return

            click.echo(f"\nDocuments (Page {pagination['currentPage']}/{pagination['totalPages']}):")
            click.echo("-" * 80)

            for doc in docs:
                click.echo(f"ID: {doc.get('id', 'N/A')}")
                click.echo(f"  Filename: {doc.get('filename', 'N/A')}")
                click.echo(f"  Size: {doc.get('file_size', 0):,} bytes")
                click.echo(f"  Status: {doc.get('embedding_status', 'N/A')}")
                click.echo(f"  Chunks: {doc.get('chunk_count', 0)}")
                click.echo(f"  Vectors: {doc.get('vector_count', 0)}")
                click.echo("-" * 80)

            click.echo(f"\nTotal: {pagination['totalItems']} documents")

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
            embeddings = result.get('embeddings', {})

            click.echo(f"\nDocument Details:")
            click.echo("-" * 50)
            click.echo(f"ID: {document_id}")
            click.echo(f"Filename: {doc.get('filename', 'N/A')}")
            click.echo(f"Size: {doc.get('fileSize', 0):,} bytes")
            click.echo(f"Processing Status: {doc.get('processingStatus', 'N/A')}")
            click.echo(f"Embedding Status: {doc.get('embeddingStatus', 'N/A')}")
            click.echo(f"Created: {doc.get('creationDate', 'N/A')}")
            click.echo(f"Last Accessed: {doc.get('lastAccessedDate', 'N/A')}")
            click.echo(f"\nEmbeddings:")
            click.echo(f"  Total Chunks: {embeddings.get('totalChunks', 0)}")
            click.echo(f"  Embedded Chunks: {embeddings.get('embeddedChunks', 0)}")

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


@cli.command()
@click.argument('document_id')
@click.option('--user-id', required=True, help='User ID for tenant scoping (FR-011, FR-014)')
@click.confirmation_option(prompt='Are you sure you want to delete this document?')
def delete_document_cmd(document_id: str, user_id: str):
    """Delete a document and all its chunks.

    Requires --user-id to enforce tenant isolation (FR-011, FR-014).
    """
    try:
        import asyncio
        result = asyncio.run(delete_document(user_id, document_id))

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
    """Get chunks for a document.

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

        result = asyncio.run(get_chunks(
            document_id=document_id,
            pagination=pagination,
            user_id=user_id
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
