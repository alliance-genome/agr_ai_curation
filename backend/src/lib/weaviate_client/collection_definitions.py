"""The search-index collections the backend creates, defined once.

Backend startup creates these when they are missing, and the read-only
settings report compares a server's stored collections against them.
"""

from __future__ import annotations

from typing import Any, Dict

from weaviate.classes.config import Configure, DataType, Property


def required_collection_definitions(embedding_model: str) -> Dict[str, Dict[str, Any]]:
    """Return ``client.collections.create`` keyword arguments per collection name."""
    return {
        "DocumentChunk": {
            # Use text2vec-openai for server-side embeddings
            "vectorizer_config": Configure.Vectorizer.text2vec_openai(
                model=embedding_model,
                vectorize_collection_name=False,
            ),
            "vector_index_config": Configure.VectorIndex.hnsw(),
            "multi_tenancy_config": Configure.multi_tenancy(enabled=True),
            "properties": [
                Property(name="documentId", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="chunkIndex", data_type=DataType.INT, skip_vectorization=True),
                Property(name="content", data_type=DataType.TEXT, vectorize_property_name=False),  # Vectorize content only
                Property(name="contentPreview", data_type=DataType.TEXT, vectorize_property_name=False, skip_vectorization=True),  # Keep preview out of vectorization
                Property(name="elementType", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="pageNumber", data_type=DataType.INT, skip_vectorization=True),
                Property(name="sectionTitle", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="sectionPath", data_type=DataType.TEXT_ARRAY, skip_vectorization=True),
                Property(name="parentSection", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="subsection", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="isTopLevel", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="contentType", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="metadata", data_type=DataType.TEXT, skip_vectorization=True),
                Property(name="embeddingTimestamp", data_type=DataType.DATE, skip_vectorization=True),
                Property(name="docItemProvenance", data_type=DataType.TEXT, skip_vectorization=True),  # For chunk highlighting
            ],
        },
        "PDFDocument": {
            "vectorizer_config": Configure.Vectorizer.none(),
            "multi_tenancy_config": Configure.multi_tenancy(enabled=True),
            "properties": [
                Property(name="filename", data_type=DataType.TEXT),
                Property(name="fileSize", data_type=DataType.INT),
                Property(name="uploadDate", data_type=DataType.DATE),
                Property(name="creationDate", data_type=DataType.DATE),
                Property(name="lastAccessedDate", data_type=DataType.DATE),
                Property(name="processingStatus", data_type=DataType.TEXT),
                Property(name="embeddingStatus", data_type=DataType.TEXT),
                Property(name="chunkCount", data_type=DataType.INT),
                Property(name="vectorCount", data_type=DataType.INT),
                Property(name="metadata", data_type=DataType.TEXT),
            ],
        },
    }
