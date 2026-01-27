"""Weaviate client library for document and chunk operations."""

from .connection import (
    WeaviateConnection,
    connect_to_weaviate,
    close_connection,
    health_check,
    get_collection_info
)

from .documents import (
    async_list_documents,
    get_document,
    delete_document,
    re_embed_document,
    update_document_status,
    search_similar,
    create_document,
    update_document_status_detailed
)

from .chunks import (
    store_chunks,
    get_chunks,
    hybrid_search_chunks,
    delete_chunks,
    update_chunk_embeddings
)

from .settings import (
    get_embedding_config,
    update_embedding_config,
    get_collection_settings,
    update_schema,
    get_available_models
)

__all__ = [
    # Connection
    'WeaviateConnection',
    'connect_to_weaviate',
    'close_connection',
    'health_check',
    'get_collection_info',
    # Documents
    'async_list_documents',
    'get_document',
    'delete_document',
    're_embed_document',
    'update_document_status',
    'update_document_status_detailed',
    'search_similar',
    'create_document',
    # Chunks
    'store_chunks',
    'get_chunks',
    'hybrid_search_chunks',
    'delete_chunks',
    'update_chunk_embeddings',
    # Settings
    'get_embedding_config',
    'update_embedding_config',
    'get_collection_settings',
    'update_schema',
    'get_available_models'
]
