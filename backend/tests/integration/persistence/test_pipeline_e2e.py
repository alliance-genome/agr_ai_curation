"""End-to-end pipeline test: Docling fixture -> chunking -> Weaviate storage."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.lib.pipeline.chunk import chunk_parsed_document
from src.lib.pipeline.store import store_to_weaviate
from src.models.strategy import ChunkingStrategy

from .conftest import TEST_USER_ID, count_persisted_chunks, fetch_persisted_chunks

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"
DOCLING_FIXTURE = FIXTURES_DIR / "micropub-biology-001725_docling.json"


@pytest.mark.integration
class TestPipelineE2E:
    """End-to-end pipeline test with real Weaviate."""

    @pytest.fixture
    def docling_elements(self):
        """Load pre-computed Docling elements from fixture."""
        if not DOCLING_FIXTURE.exists():
            pytest.skip(
                f"Docling fixture not found: {DOCLING_FIXTURE}. "
                "Generate it by running the test PDF through Docling. "
                "See PLAN_chunk_persistence_tests.md Step 5."
            )
        with open(DOCLING_FIXTURE, encoding="utf-8") as fixture_file:
            return json.load(fixture_file)

    @pytest.mark.asyncio
    async def test_chunked_pdf_fully_persists(
        self,
        weaviate_connection,
        setup_collections,
        clean_chunks,
        docling_elements,
    ):
        """Full pipeline: parse fixture -> chunk -> store -> verify persistence."""
        document_id = "test-e2e-pipeline-001"

        strategy = ChunkingStrategy.get_research_strategy()
        chunks = await chunk_parsed_document(docling_elements, strategy, document_id)
        expected_count = len(chunks)
        assert expected_count > 0, "Chunking produced zero chunks from Docling fixture"

        with patch("src.lib.pipeline.store.update_document_status_detailed", new=AsyncMock()):
            stats = await store_to_weaviate(
                chunks, document_id, weaviate_connection, user_id=TEST_USER_ID
            )

        persisted_count = count_persisted_chunks(weaviate_connection, document_id)

        assert persisted_count == expected_count, (
            f"PIPELINE PERSISTENCE FAILURE: Chunking produced {expected_count} chunks "
            f"but only {persisted_count} persisted in Weaviate."
        )
        assert stats["stored_chunks"] == expected_count

        objects = fetch_persisted_chunks(weaviate_connection, document_id)

        indices = sorted(obj.properties.get("chunkIndex") for obj in objects)
        assert indices == list(range(expected_count)), "Non-contiguous chunk indices after pipeline"

        all_content = " ".join(obj.properties.get("content", "") for obj in objects)
        assert "drosophila" in all_content.lower(), "No Drosophila content found in persisted chunks"

        has_non_reference = any(
            obj.properties.get("sectionTitle", "") != "References"
            for obj in objects
            if obj.properties.get("sectionTitle")
        )
        if not has_non_reference:
            import warnings

            warnings.warn(
                "All persisted chunks have sectionTitle='References' or None. "
                "This may indicate a v0.2.0 regression."
            )
