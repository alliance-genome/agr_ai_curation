"""Isolated conftest for persistence integration tests.

This conftest does NOT inherit the parent integration conftest's autouse
session-scoped mocks. It provides REAL Weaviate connections for testing
actual chunk persistence.

IMPORTANT: Environment variables must be set before store.py is imported,
because store.py validates them at module level.
"""

import os

import pytest
from weaviate.classes.config import Configure, DataType, Property
from weaviate.classes.query import Filter
from weaviate.classes.tenants import Tenant

from src.lib.weaviate_client.connection import WeaviateConnection

# Environment variables required by store.py (must be set BEFORE import)
os.environ.setdefault("EMBEDDING_MODEL", "text-embedding-3-small")
os.environ.setdefault("EMBEDDING_TOKEN_PREFLIGHT_ENABLED", "false")
os.environ.setdefault("EMBEDDING_MODEL_TOKEN_LIMIT", "8191")
os.environ.setdefault("EMBEDDING_TOKEN_SAFETY_MARGIN", "500")
os.environ.setdefault("CONTENT_PREVIEW_CHARS", "1600")
os.environ.setdefault("WEAVIATE_BATCH_REQUESTS_PER_MINUTE", "5000")
os.environ["EMBEDDING_TOKEN_PREFLIGHT_ENABLED"] = "false"

TEST_WEAVIATE_HOST = os.environ.get("WEAVIATE_HOST", "weaviate-test")
TEST_WEAVIATE_PORT = int(os.environ.get("WEAVIATE_PORT", "8080"))
TEST_WEAVIATE_SCHEME = os.environ.get("WEAVIATE_SCHEME", "http")
TEST_TENANT_NAME = "test_persistence_user"
TEST_USER_ID = "test-persistence-user"


@pytest.fixture(scope="session")
def weaviate_url() -> str:
    """Build the Weaviate URL from environment."""
    return f"{TEST_WEAVIATE_SCHEME}://{TEST_WEAVIATE_HOST}:{TEST_WEAVIATE_PORT}"


@pytest.fixture(scope="session")
def weaviate_connection(weaviate_url):
    """Create a real Weaviate connection for the test session."""
    WeaviateConnection._instance = None

    connection = WeaviateConnection(url=weaviate_url)
    connection.connect()

    with connection.session() as client:
        assert client.is_ready(), (
            f"Weaviate at {weaviate_url} is not ready. "
            "Ensure docker-compose.test.yml weaviate-test service is running."
        )

    yield connection

    try:
        connection.close()
    except Exception:
        pass
    finally:
        WeaviateConnection._instance = None


@pytest.fixture(scope="session")
def setup_collections(weaviate_connection):
    """Create DocumentChunk and PDFDocument collections with vectorizer=none."""
    with weaviate_connection.session() as client:
        for name in ["DocumentChunk", "PDFDocument"]:
            try:
                client.collections.delete(name)
            except Exception:
                pass

        client.collections.create(
            name="DocumentChunk",
            vectorizer_config=Configure.Vectorizer.none(),
            multi_tenancy_config=Configure.multi_tenancy(enabled=True),
            properties=[
                Property(name="documentId", data_type=DataType.TEXT),
                Property(name="chunkIndex", data_type=DataType.INT),
                Property(name="content", data_type=DataType.TEXT),
                Property(name="contentPreview", data_type=DataType.TEXT),
                Property(name="elementType", data_type=DataType.TEXT),
                Property(name="pageNumber", data_type=DataType.INT),
                Property(name="sectionTitle", data_type=DataType.TEXT),
                Property(name="sectionPath", data_type=DataType.TEXT_ARRAY),
                Property(name="parentSection", data_type=DataType.TEXT),
                Property(name="subsection", data_type=DataType.TEXT),
                Property(name="isTopLevel", data_type=DataType.TEXT),
                Property(name="contentType", data_type=DataType.TEXT),
                Property(name="metadata", data_type=DataType.TEXT),
                Property(name="embeddingTimestamp", data_type=DataType.DATE),
                Property(name="docItemProvenance", data_type=DataType.TEXT),
            ],
        )

        client.collections.create(
            name="PDFDocument",
            vectorizer_config=Configure.Vectorizer.none(),
            multi_tenancy_config=Configure.multi_tenancy(enabled=True),
            properties=[
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
        )

        chunk_col = client.collections.get("DocumentChunk")
        pdf_col = client.collections.get("PDFDocument")
        chunk_col.tenants.create([Tenant(name=TEST_TENANT_NAME)])
        pdf_col.tenants.create([Tenant(name=TEST_TENANT_NAME)])

    yield

    with weaviate_connection.session() as client:
        for name in ["DocumentChunk", "PDFDocument"]:
            try:
                client.collections.delete(name)
            except Exception:
                pass


@pytest.fixture
def clean_chunks(weaviate_connection, setup_collections):
    """Delete all chunks from the test tenant after each test."""
    yield

    with weaviate_connection.session() as client:
        collection = client.collections.get("DocumentChunk").with_tenant(TEST_TENANT_NAME)
        try:
            result = collection.query.fetch_objects(limit=10000)
            for obj in result.objects:
                collection.data.delete_by_id(obj.uuid)
        except Exception:
            pass


@pytest.fixture
def chunk_collection(weaviate_connection, setup_collections):
    """Get tenant-scoped DocumentChunk collection for direct queries."""
    with weaviate_connection.session() as client:
        yield client.collections.get("DocumentChunk").with_tenant(TEST_TENANT_NAME)


def count_persisted_chunks(weaviate_connection, document_id: str) -> int:
    """Independently count persisted chunks for a document."""
    with weaviate_connection.session() as client:
        collection = client.collections.get("DocumentChunk").with_tenant(TEST_TENANT_NAME)
        result = collection.query.fetch_objects(
            filters=Filter.by_property("documentId").equal(document_id),
            limit=10000,
        )
        return len(result.objects)


def fetch_persisted_chunks(weaviate_connection, document_id: str):
    """Independently fetch all persisted chunks for a document."""
    with weaviate_connection.session() as client:
        collection = client.collections.get("DocumentChunk").with_tenant(TEST_TENANT_NAME)
        result = collection.query.fetch_objects(
            filters=Filter.by_property("documentId").equal(document_id),
            limit=10000,
        )
        return result.objects
