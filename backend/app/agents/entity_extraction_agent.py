"""
Entity Extraction Agent - A specialized sub-agent for extracting biological entities

This agent is designed to extract structured biological entities from text,
including genes, proteins, diseases, chemicals, and more. It returns
structured data (EntityExtractionOutput) rather than plain text.
"""

import logging
from typing import List, Optional, Dict, Any
from pydantic_ai import Agent
from pydantic import BaseModel, Field

from .models import (
    ExtractedEntity,
    EntityExtractionOutput,
    EntityType,
)

logger = logging.getLogger(__name__)


class EntityExtractionAgent:
    """
    Specialized agent for extracting biological entities from text.

    This agent uses structured output to ensure consistent entity extraction
    with proper typing, confidence scores, and database identifiers.
    """

    def __init__(
        self,
        model: str = "openai:gpt-4o-mini",
        system_prompt: Optional[str] = None,
        min_confidence: float = 0.7,
        context_window: int = 100,
    ):
        """
        Initialize the EntityExtractionAgent.

        Args:
            model: The AI model to use (default: gpt-4o-mini for speed)
            system_prompt: Optional custom system prompt
            min_confidence: Minimum confidence threshold for entities
            context_window: Characters of context to capture around entities
        """
        self.model = model
        self.min_confidence = min_confidence
        self.context_window = context_window

        # Default system prompt optimized for entity extraction
        if system_prompt is None:
            system_prompt = """You are a specialized biological entity extraction system.
Your task is to identify and extract biological entities from scientific text.

For each entity, you must:
1. Identify the exact text as it appears in the document
2. Classify it into the correct entity type (gene, protein, disease, etc.)
3. Provide the normalized/standard form when possible
4. Include database identifiers if known (e.g., NCBI Gene ID, UniProt ID, MESH ID)
5. Assign a confidence score (0.0-1.0) based on context clarity
6. Capture surrounding context for disambiguation

Entity Types to Extract:
- GENE: Gene symbols and names (e.g., BRCA1, TP53)
- PROTEIN: Protein names and symbols (e.g., p53, HER2)
- DISEASE: Diseases and conditions (e.g., breast cancer, diabetes)
- PHENOTYPE: Observable characteristics (e.g., drug resistance, tumor growth)
- CHEMICAL: Drugs and compounds (e.g., tamoxifen, glucose)
- PATHWAY: Biological pathways (e.g., MAPK pathway, apoptosis)
- ORGANISM: Species and organisms (e.g., Homo sapiens, E. coli)
- CELL_TYPE: Cell types (e.g., T cells, neurons)
- ANATOMICAL: Body parts and tissues (e.g., liver, hippocampus)

Guidelines:
- Be conservative with confidence scores - only use >0.9 for unambiguous entities
- Capture gene/protein synonyms and aliases
- Distinguish between genes and their protein products when context allows
- Include both abbreviated and full forms when present
- Preserve exact capitalization and formatting from the source text"""

        self.system_prompt = system_prompt

        # Create the PydanticAI agent with structured output
        self.agent = Agent(
            model,
            output_type=EntityExtractionOutput,  # Structured output!
            system_prompt=self.system_prompt,
        )

    async def extract(self, text: str) -> EntityExtractionOutput:
        """
        Extract biological entities from the given text.

        Args:
            text: The text to extract entities from

        Returns:
            EntityExtractionOutput with all extracted entities
        """
        if not text or not text.strip():
            # Return empty result for empty text
            return EntityExtractionOutput(
                entities=[],
                summary="No entities found in the text",
                total_entities=0,
                entity_breakdown={},
            )

        try:
            # Run the extraction
            result = await self.agent.run(text)
            return result.output
        except Exception as e:
            logger.error(f"Error during entity extraction: {e}")
            raise

    async def extract_specific_types(
        self, text: str, entity_types: List[EntityType]
    ) -> EntityExtractionOutput:
        """
        Extract only specific types of entities from text.

        Args:
            text: The text to extract entities from
            entity_types: List of entity types to extract

        Returns:
            EntityExtractionOutput with filtered entities
        """
        # Create a custom prompt for specific entity types
        types_str = ", ".join([t.value for t in entity_types])
        custom_prompt = f"{self.system_prompt}\n\nFOCUS: Only extract entities of these types: {types_str}"

        # Create a temporary agent with the custom prompt
        temp_agent = Agent(
            self.model,
            output_type=EntityExtractionOutput,
            system_prompt=custom_prompt,
        )

        result = await temp_agent.run(text)
        return result.output

    def filter_by_confidence(
        self, entities: List[ExtractedEntity], threshold: Optional[float] = None
    ) -> List[ExtractedEntity]:
        """
        Filter entities by confidence threshold.

        Args:
            entities: List of extracted entities
            threshold: Confidence threshold (uses self.min_confidence if not provided)

        Returns:
            Filtered list of entities above the threshold
        """
        threshold = threshold or self.min_confidence
        return [e for e in entities if e.confidence >= threshold]

    def deduplicate_entities(
        self, entities: List[ExtractedEntity]
    ) -> List[ExtractedEntity]:
        """
        Remove duplicate entity mentions, keeping the highest confidence version.

        Args:
            entities: List of extracted entities

        Returns:
            Deduplicated list of entities
        """
        # Group by text and type
        entity_map: Dict[tuple, ExtractedEntity] = {}

        for entity in entities:
            key = (entity.text.lower(), entity.type)

            # Keep the version with highest confidence
            if key not in entity_map or entity.confidence > entity_map[key].confidence:
                entity_map[key] = entity

        return list(entity_map.values())

    async def batch_extract(self, texts: List[str]) -> List[EntityExtractionOutput]:
        """
        Extract entities from multiple text segments.

        Args:
            texts: List of text segments to process

        Returns:
            List of EntityExtractionOutput for each text
        """
        results = []
        for text in texts:
            result = await self.extract(text)
            results.append(result)
        return results

    def merge_results(
        self, results: List[EntityExtractionOutput]
    ) -> EntityExtractionOutput:
        """
        Merge multiple extraction results into a single output.

        Args:
            results: List of EntityExtractionOutput to merge

        Returns:
            Merged EntityExtractionOutput
        """
        all_entities = []
        entity_breakdown: Dict[EntityType, int] = {}

        for result in results:
            all_entities.extend(result.entities)

            # Merge entity breakdown counts
            for entity_type, count in result.entity_breakdown.items():
                entity_breakdown[entity_type] = (
                    entity_breakdown.get(entity_type, 0) + count
                )

        # Deduplicate merged entities
        unique_entities = self.deduplicate_entities(all_entities)

        # Recalculate breakdown after deduplication
        final_breakdown: Dict[EntityType, int] = {}
        for entity in unique_entities:
            final_breakdown[entity.type] = final_breakdown.get(entity.type, 0) + 1

        return EntityExtractionOutput(
            entities=unique_entities,
            summary=f"Merged extraction: found {len(unique_entities)} unique entities",
            total_entities=len(unique_entities),
            entity_breakdown=final_breakdown,
        )

    def format_for_display(self, entity: ExtractedEntity) -> Dict[str, Any]:
        """
        Format an entity for display in the UI.

        Args:
            entity: The entity to format

        Returns:
            Dictionary suitable for JSON serialization
        """
        return {
            "text": entity.text,
            "type": entity.type.value,
            "normalized": entity.normalized_form,
            "database_id": entity.database_id,
            "confidence": round(entity.confidence, 2),
            "context": entity.context,
        }

    def group_by_type(
        self, entities: List[ExtractedEntity]
    ) -> Dict[EntityType, List[ExtractedEntity]]:
        """
        Group entities by their type.

        Args:
            entities: List of extracted entities

        Returns:
            Dictionary mapping entity types to lists of entities
        """
        grouped: Dict[EntityType, List[ExtractedEntity]] = {}

        for entity in entities:
            if entity.type not in grouped:
                grouped[entity.type] = []
            grouped[entity.type].append(entity)

        return grouped
