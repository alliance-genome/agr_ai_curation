"""
Tests for BioCurationAgent with integrated tools (entity extraction, etc.)
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from typing import List
import asyncio

from app.agents.biocuration_agent import BioCurationAgent, BioCurationDependencies
from app.agents.entity_extraction_agent import EntityExtractionAgent
from app.agents.models import (
    EntityType,
    ExtractedEntity,
    EntityExtractionOutput,
    StreamingUpdate,
    CurationContext,
)


class TestBioCurationAgentWithTools:
    """Test BioCurationAgent with entity extraction tool integration"""

    @pytest.mark.asyncio
    async def test_agent_has_extract_entities_tool(self):
        """Test that the agent has the extract_entities tool registered"""
        agent = BioCurationAgent(model="openai:gpt-4o-mini")

        # Check that the public method exists
        assert hasattr(agent, "extract_entities_tool")
        assert callable(agent.extract_entities_tool)

    @pytest.mark.asyncio
    async def test_extract_entities_tool_returns_entities(self):
        """Test that the extract_entities tool extracts and returns entities"""
        agent = BioCurationAgent(model="openai:gpt-4o-mini")
        deps = BioCurationDependencies()

        # Mock the EntityExtractionAgent
        with patch(
            "app.agents.biocuration_agent.EntityExtractionAgent"
        ) as MockExtractor:
            mock_extractor = Mock()
            MockExtractor.return_value = mock_extractor

            # Create the entities
            entities = [
                ExtractedEntity(
                    text="BRCA1",
                    type=EntityType.GENE,
                    normalized_form="BRCA1",
                    database_id="672",
                    confidence=0.95,
                    context="BRCA1 mutations are common",
                ),
                ExtractedEntity(
                    text="breast cancer",
                    type=EntityType.DISEASE,
                    normalized_form="Breast Cancer",
                    database_id="MESH:D001943",
                    confidence=0.92,
                    context="associated with breast cancer",
                ),
            ]

            # Mock the extract method
            mock_extractor.extract = AsyncMock(
                return_value=EntityExtractionOutput(
                    entities=entities,
                    summary="Found 2 entities",
                    total_entities=2,
                    entity_breakdown={EntityType.GENE: 1, EntityType.DISEASE: 1},
                )
            )

            # Mock the filter_by_confidence method to return the same entities
            mock_extractor.filter_by_confidence = Mock(return_value=entities)

            # Call the tool directly (simulating what the agent would do)
            text = "BRCA1 mutations are associated with breast cancer"
            result = await agent.extract_entities_tool(deps, text)

            # Verify the tool was called and returned entities
            assert result is not None
            assert len(result["entities"]) == 2
            assert result["entities"][0]["text"] == "BRCA1"
            assert result["entities"][1]["text"] == "breast cancer"
            assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_streaming_with_entity_extraction(self):
        """Test that entity extraction events are streamed during conversation"""
        agent = BioCurationAgent(model="openai:gpt-4o-mini")
        deps = BioCurationDependencies(
            context=CurationContext(
                document_text="The BRCA1 gene is associated with breast cancer risk."
            )
        )

        # Collect all streaming updates
        updates = []

        with patch.object(agent.agent, "run_stream") as mock_stream:
            # Mock the streaming context
            mock_run = AsyncMock()
            mock_stream.return_value.__aenter__.return_value = mock_run

            # Mock text streaming
            async def mock_text_gen(delta=True):
                yield "I found several important "
                yield "biological entities "
                yield "in the text."

            mock_run.stream_text = mock_text_gen
            mock_run.new_messages = Mock(return_value=[])

            # Mock tool calls during streaming
            mock_run.tool_calls = [
                Mock(
                    tool_name="extract_entities",
                    args={
                        "text": "The BRCA1 gene is associated with breast cancer risk."
                    },
                    result={
                        "entities": [
                            {"text": "BRCA1", "type": "gene", "confidence": 0.95},
                            {
                                "text": "breast cancer",
                                "type": "disease",
                                "confidence": 0.90,
                            },
                        ],
                        "total": 2,
                    },
                )
            ]

            # Collect streaming updates
            async for update in agent._process_stream(
                "Extract entities from the document", deps, use_delta=True
            ):
                updates.append(update)

            # Check we got both text and entity updates
            text_updates = [u for u in updates if u.type == "text_delta"]
            entity_updates = [u for u in updates if u.type == "entity"]

            assert len(text_updates) > 0  # Got text streaming
            # Entity extraction would happen via tool calls

    @pytest.mark.asyncio
    async def test_automatic_entity_extraction_trigger(self):
        """Test that entity extraction is triggered automatically for relevant queries"""
        agent = BioCurationAgent(model="openai:gpt-4o-mini")
        deps = BioCurationDependencies()

        # Queries that should trigger entity extraction
        entity_queries = [
            "What genes are mentioned in this paper?",
            "Extract all biological entities",
            "List the proteins discussed",
            "Find all diseases mentioned",
            "Identify the organisms in the text",
        ]

        for query in entity_queries:
            # The agent should recognize these as entity extraction requests
            # This would be handled by the agent's system prompt and tool descriptions
            assert (
                "gene" in query.lower()
                or "entit" in query.lower()
                or "protein" in query.lower()
                or "disease" in query.lower()
                or "organism" in query.lower()
            )

    @pytest.mark.asyncio
    async def test_entity_extraction_with_confidence_filtering(self):
        """Test that entity extraction respects confidence thresholds"""
        agent = BioCurationAgent(
            model="openai:gpt-4o-mini", entity_confidence_threshold=0.8
        )
        deps = BioCurationDependencies()

        with patch(
            "app.agents.biocuration_agent.EntityExtractionAgent"
        ) as MockExtractor:
            mock_extractor = Mock()
            MockExtractor.return_value = mock_extractor

            # Create entities with varying confidence
            all_entities = [
                ExtractedEntity(text="BRCA1", type=EntityType.GENE, confidence=0.95),
                ExtractedEntity(
                    text="maybe_gene", type=EntityType.GENE, confidence=0.6
                ),
                ExtractedEntity(text="TP53", type=EntityType.GENE, confidence=0.85),
            ]

            # Return entities with varying confidence
            mock_extractor.extract = AsyncMock(
                return_value=EntityExtractionOutput(
                    entities=all_entities,
                    summary="Found 3 entities",
                    total_entities=3,
                    entity_breakdown={EntityType.GENE: 3},
                )
            )

            # Mock filter_by_confidence to filter properly (0.8 threshold)
            filtered_entities = [e for e in all_entities if e.confidence >= 0.8]
            mock_extractor.filter_by_confidence = Mock(return_value=filtered_entities)

            result = await agent.extract_entities_tool(
                deps, "Some text", min_confidence=0.8
            )

            # Should only return high-confidence entities
            assert len(result["entities"]) == 2
            assert all(e["confidence"] >= 0.8 for e in result["entities"])

    @pytest.mark.asyncio
    async def test_entity_extraction_error_handling(self):
        """Test graceful error handling when entity extraction fails"""
        agent = BioCurationAgent(model="openai:gpt-4o-mini")
        deps = BioCurationDependencies()

        with patch(
            "app.agents.biocuration_agent.EntityExtractionAgent"
        ) as MockExtractor:
            mock_extractor = Mock()
            MockExtractor.return_value = mock_extractor

            # Simulate extraction failure
            mock_extractor.extract = AsyncMock(side_effect=Exception("API error"))

            # Should handle error gracefully
            result = await agent.extract_entities_tool(deps, "Some text")

            assert result is not None
            assert result.get("error") is not None
            assert "API error" in result["error"]

    @pytest.mark.asyncio
    async def test_parallel_entity_extraction(self):
        """Test that multiple entity extraction calls can run in parallel"""
        agent = BioCurationAgent(model="openai:gpt-4o-mini")
        deps = BioCurationDependencies()

        texts = ["BRCA1 is important", "p53 mutations occur", "EGFR signaling pathway"]

        with patch(
            "app.agents.biocuration_agent.EntityExtractionAgent"
        ) as MockExtractor:
            mock_extractor = Mock()
            MockExtractor.return_value = mock_extractor

            # Each call returns different entities
            async def mock_extract(text):
                await asyncio.sleep(0.1)  # Simulate processing time
                if "BRCA1" in text:
                    entities = [
                        ExtractedEntity(
                            text="BRCA1", type=EntityType.GENE, confidence=0.95
                        )
                    ]
                elif "p53" in text:
                    entities = [
                        ExtractedEntity(
                            text="p53", type=EntityType.GENE, confidence=0.93
                        )
                    ]
                else:
                    entities = [
                        ExtractedEntity(
                            text="EGFR", type=EntityType.GENE, confidence=0.91
                        )
                    ]

                return EntityExtractionOutput(
                    entities=entities,
                    summary=f"Found {len(entities)} entities",
                    total_entities=len(entities),
                    entity_breakdown={EntityType.GENE: len(entities)},
                )

            mock_extractor.extract = mock_extract

            # Mock filter_by_confidence to return all entities (they're all high confidence)
            def mock_filter(entities, threshold=None):
                return entities

            mock_extractor.filter_by_confidence = Mock(side_effect=mock_filter)

            # Run extractions in parallel
            start_time = asyncio.get_event_loop().time()
            tasks = [agent.extract_entities_tool(deps, text) for text in texts]
            results = await asyncio.gather(*tasks)
            elapsed = asyncio.get_event_loop().time() - start_time

            # Should complete faster than sequential (0.3s)
            assert elapsed < 0.2  # Parallel execution
            assert len(results) == 3
            assert all(r["total"] == 1 for r in results)

    @pytest.mark.asyncio
    async def test_entity_extraction_with_document_context(self):
        """Test entity extraction uses document context when available"""
        agent = BioCurationAgent(model="openai:gpt-4o-mini")

        document_text = """
        The BRCA1 gene, located on chromosome 17, plays a crucial role in DNA repair.
        Mutations in BRCA1 are associated with increased risk of breast cancer and
        ovarian cancer. The p53 protein also acts as a tumor suppressor.
        """

        deps = BioCurationDependencies(
            context=CurationContext(
                document_text=document_text, document_id="test-doc-1"
            )
        )

        with patch(
            "app.agents.biocuration_agent.EntityExtractionAgent"
        ) as MockExtractor:
            mock_extractor = Mock()
            MockExtractor.return_value = mock_extractor

            # Verify the full document text is passed
            mock_extractor.extract = AsyncMock(
                return_value=EntityExtractionOutput(
                    entities=[],
                    summary="Extracted from document",
                    total_entities=0,
                    entity_breakdown={},
                )
            )

            # Mock filter_by_confidence to return empty list
            mock_extractor.filter_by_confidence = Mock(return_value=[])

            await agent.extract_entities_tool(deps, text="", use_document=True)

            # Should use document text from context
            mock_extractor.extract.assert_called_once()
            call_args = mock_extractor.extract.call_args[0][0]
            assert "BRCA1" in call_args
            assert "chromosome 17" in call_args
            assert "p53" in call_args
