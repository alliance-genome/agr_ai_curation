"""Integration tests for chunk persistence in Weaviate."""

import pytest

from src.lib.pipeline.store import store_chunks_to_weaviate

from .conftest import TEST_USER_ID, count_persisted_chunks, fetch_persisted_chunks


def _make_test_chunks(n: int, document_id: str = "test-doc") -> list:
    """Generate N test chunks in the store_chunks_to_weaviate input format."""
    sections = [
        "Abstract",
        "Introduction",
        "Methods",
        "Results",
        "Discussion",
        "References",
    ]
    return [
        {
            "chunk_index": i,
            "content": (
                f"Section: {sections[i % len(sections)]}. "
                f"This is test chunk {i} for document {document_id}. "
                "Drosophila melanogaster ovarian follicle cells exhibit "
                f"distinct transcriptional profiles at developmental stage {i % 14 + 1}. "
                f"Content marker: CHUNK_{i:04d}_PRESENT"
            ),
            "element_type": "NarrativeText",
            "page_number": (i // 5) + 1,
            "section_title": sections[i % len(sections)],
            "metadata": {},
        }
        for i in range(n)
    ]


@pytest.mark.integration
class TestChunkPersistence:
    """Verify that chunks sent to Weaviate actually persist."""

    @pytest.mark.asyncio
    async def test_all_chunks_persist_small_batch(
        self, weaviate_connection, setup_collections, clean_chunks
    ):
        """Verify all chunks persist for a small batch."""
        chunk_count = 25
        document_id = "test-persist-small-001"
        chunks = _make_test_chunks(chunk_count, document_id)

        result = await store_chunks_to_weaviate(
            chunks, document_id, weaviate_connection, TEST_USER_ID
        )

        persisted_count = count_persisted_chunks(weaviate_connection, document_id)

        assert persisted_count == chunk_count, (
            f"PERSISTENCE FAILURE: Expected {chunk_count} chunks persisted, "
            f"got {persisted_count}. This is the v0.2.0 failure mode."
        )
        assert result["stored_count"] == chunk_count
        assert result["failed_count"] == 0

    @pytest.mark.asyncio
    async def test_all_chunks_persist_large_batch(
        self, weaviate_connection, setup_collections, clean_chunks
    ):
        """Verify all chunks persist for a batch matching the v0.2.0 chunk count."""
        chunk_count = 161
        document_id = "test-persist-large-001"
        chunks = _make_test_chunks(chunk_count, document_id)

        result = await store_chunks_to_weaviate(
            chunks, document_id, weaviate_connection, TEST_USER_ID
        )

        persisted_count = count_persisted_chunks(weaviate_connection, document_id)

        assert persisted_count == chunk_count, (
            f"PERSISTENCE FAILURE: Expected {chunk_count} chunks (matching v0.2.0 bug), "
            f"got {persisted_count}."
        )
        assert result["stored_count"] == chunk_count
        assert result["failed_count"] == 0

    @pytest.mark.asyncio
    async def test_chunk_indices_are_contiguous(
        self, weaviate_connection, setup_collections, clean_chunks
    ):
        """Verify chunkIndex values are contiguous 0..N-1 after persistence."""
        chunk_count = 20
        document_id = "test-persist-indices-001"
        chunks = _make_test_chunks(chunk_count, document_id)

        await store_chunks_to_weaviate(
            chunks, document_id, weaviate_connection, TEST_USER_ID
        )

        objects = fetch_persisted_chunks(weaviate_connection, document_id)
        indices = sorted(obj.properties.get("chunkIndex") for obj in objects)

        assert indices == list(range(chunk_count)), (
            f"Chunk indices are not contiguous 0..{chunk_count - 1}. Got: {indices}"
        )

    @pytest.mark.asyncio
    async def test_non_reference_content_persists(
        self, weaviate_connection, setup_collections, clean_chunks
    ):
        """Verify chunks from all sections persist, not just references."""
        chunk_count = 30
        document_id = "test-persist-sections-001"
        chunks = _make_test_chunks(chunk_count, document_id)

        await store_chunks_to_weaviate(
            chunks, document_id, weaviate_connection, TEST_USER_ID
        )

        objects = fetch_persisted_chunks(weaviate_connection, document_id)
        sections_present = set()
        all_sections = [
            "Abstract",
            "Introduction",
            "Methods",
            "Results",
            "Discussion",
            "References",
        ]
        for obj in objects:
            content = obj.properties.get("content", "")
            for section in all_sections:
                if f"Section: {section}." in content:
                    sections_present.add(section)

        expected_sections = set(all_sections)
        assert sections_present == expected_sections, (
            f"Missing sections in persisted chunks: {expected_sections - sections_present}. "
            f"Only found: {sections_present}. "
            "This matches the v0.2.0 bug where only References survived."
        )

    @pytest.mark.asyncio
    async def test_content_integrity_after_persistence(
        self, weaviate_connection, setup_collections, clean_chunks
    ):
        """Verify chunk content survives storage unchanged."""
        chunk_count = 10
        document_id = "test-persist-content-001"
        chunks = _make_test_chunks(chunk_count, document_id)

        await store_chunks_to_weaviate(
            chunks, document_id, weaviate_connection, TEST_USER_ID
        )

        objects = fetch_persisted_chunks(weaviate_connection, document_id)

        persisted_markers = set()
        for obj in objects:
            content = obj.properties.get("content", "")
            for i in range(chunk_count):
                marker = f"CHUNK_{i:04d}_PRESENT"
                if marker in content:
                    persisted_markers.add(i)

        expected_markers = set(range(chunk_count))
        assert persisted_markers == expected_markers, (
            "Content integrity failure. "
            f"Missing chunk markers: {expected_markers - persisted_markers}"
        )

    @pytest.mark.asyncio
    async def test_store_return_value_matches_persisted_count(
        self, weaviate_connection, setup_collections, clean_chunks
    ):
        """Verify store return value matches actual persisted count."""
        chunk_count = 15
        document_id = "test-persist-return-001"
        chunks = _make_test_chunks(chunk_count, document_id)

        result = await store_chunks_to_weaviate(
            chunks, document_id, weaviate_connection, TEST_USER_ID
        )

        persisted_count = count_persisted_chunks(weaviate_connection, document_id)

        assert result["stored_count"] == persisted_count, (
            f"Return value mismatch: function reports stored_count={result['stored_count']} "
            f"but only {persisted_count} chunks are in Weaviate. "
            "This is the v0.2.0 fake-success-count bug."
        )

    @pytest.mark.asyncio
    async def test_deterministic_uuids_prevent_duplicates(
        self, weaviate_connection, setup_collections, clean_chunks
    ):
        """Verify re-inserting the same chunks does not create duplicates."""
        chunk_count = 10
        document_id = "test-persist-idempotent-001"
        chunks = _make_test_chunks(chunk_count, document_id)

        await store_chunks_to_weaviate(
            chunks, document_id, weaviate_connection, TEST_USER_ID
        )
        count_after_first = count_persisted_chunks(weaviate_connection, document_id)

        await store_chunks_to_weaviate(
            chunks, document_id, weaviate_connection, TEST_USER_ID
        )
        count_after_second = count_persisted_chunks(weaviate_connection, document_id)

        assert count_after_first == chunk_count
        assert count_after_second == chunk_count, (
            f"Duplicate chunks created: expected {chunk_count}, got {count_after_second}"
        )
