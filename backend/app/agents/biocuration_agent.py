"""
BioCuration Agent using PydanticAI

This agent handles biological curation tasks including entity extraction,
annotation suggestions, and contextual analysis of scientific documents.
"""

import os
import logging
from typing import Optional, List, Dict, Any, AsyncIterator
from dataclasses import dataclass
from datetime import datetime
import time

from pydantic_ai import Agent, RunContext, ModelRetry
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .models import (
    BioCurationOutput,
    ExtractedEntity,
    AnnotationSuggestion,
    CurationContext,
    EntityType,
    HighlightColor,
    StreamingUpdate,
)
from ..database import get_db
from ..models import ChatHistory

logger = logging.getLogger(__name__)


@dataclass
class BioCurationDependencies:
    """Dependencies for the BioCuration agent"""

    db_session: Optional[Session] = None
    session_id: Optional[str] = None
    context: Optional[CurationContext] = None
    user_preferences: Dict[str, Any] = None

    def __post_init__(self):
        if self.user_preferences is None:
            self.user_preferences = {}


class BioCurationAgent:
    """
    Main agent for biological curation tasks.
    Supports entity extraction, annotation suggestions, and contextual analysis.
    """

    def __init__(
        self,
        model: str = "openai:gpt-4o",
        system_prompt: Optional[str] = None,
    ):
        """
        Initialize the BioCuration agent.

        Args:
            model: The AI model to use (e.g., "openai:gpt-4o", "google-gla:gemini-1.5-flash")
            system_prompt: Optional custom system prompt
        """
        self.model = model
        self.system_prompt = system_prompt

        # Default system prompt for biocuration
        if self.system_prompt is None:
            self.system_prompt = """
You are an expert biological curator assistant specializing in analyzing scientific literature
and extracting structured information. Your role is to:

1. Extract and identify biological entities (genes, proteins, diseases, phenotypes, etc.)
2. Suggest appropriate annotations and highlights for important information
3. Provide clear, scientifically accurate explanations
4. Maintain high confidence in your extractions and flag uncertain cases
5. Consider the context of the document when making suggestions

When extracting entities:
- Use standard nomenclature when possible
- Provide database identifiers if known (e.g., NCBI Gene ID, UniProt ID)
- Include confidence scores based on context clarity
- Preserve the exact text as it appears in the document

When suggesting annotations:
- Use appropriate colors for different categories:
  - Yellow: Key findings or conclusions
  - Green: Genes and proteins
  - Blue: Diseases and phenotypes
  - Purple: Methods and techniques
  - Orange: Statistical results
  - Pink: Important citations or references

Always maintain scientific accuracy and clarity in your responses.
"""

        # Create the PydanticAI agent
        self.agent = Agent(
            model,
            deps_type=BioCurationDependencies,
            result_type=BioCurationOutput,
            system_prompt=self.system_prompt,
        )

        # Register agent tools
        self._register_tools()

    def _register_tools(self):
        """Register tools that the agent can use"""

        @self.agent.tool
        async def search_entity_database(
            ctx: RunContext[BioCurationDependencies],
            entity_text: str,
            entity_type: EntityType,
        ) -> Dict[str, Any]:
            """
            Search for an entity in biological databases.
            Returns normalized form and database IDs if found.
            """
            # This is a placeholder for actual database searches
            # In production, this would connect to NCBI, UniProt, etc.
            logger.info(f"Searching for {entity_type} entity: {entity_text}")

            # Simulate database search
            results = {
                "found": True,
                "normalized_form": entity_text.upper(),
                "database_id": f"MOCK_{entity_type.value.upper()}_{abs(hash(entity_text)) % 10000}",
                "synonyms": [],
                "description": f"Mock description for {entity_text}",
            }

            return results

        @self.agent.tool
        async def get_document_context(
            ctx: RunContext[BioCurationDependencies],
            start_pos: int,
            end_pos: int,
            context_window: int = 100,
        ) -> str:
            """
            Get surrounding context for a text position in the document.
            """
            if not ctx.deps.context or not ctx.deps.context.document_text:
                return ""

            text = ctx.deps.context.document_text
            start = max(0, start_pos - context_window)
            end = min(len(text), end_pos + context_window)

            return text[start:end]

        @self.agent.tool
        async def save_annotation(
            ctx: RunContext[BioCurationDependencies],
            annotation: Dict[str, Any],
        ) -> bool:
            """
            Save an annotation to the database.
            """
            if ctx.deps.db_session and ctx.deps.session_id:
                try:
                    # In production, this would save to an annotations table
                    logger.info(f"Saving annotation: {annotation}")
                    return True
                except Exception as e:
                    logger.error(f"Failed to save annotation: {e}")
                    return False
            return False

        @self.agent.tool(retries=2)
        async def validate_entity(
            ctx: RunContext[BioCurationDependencies],
            entity_text: str,
            entity_type: EntityType,
        ) -> bool:
            """
            Validate that an entity is correctly typed.
            """
            # Basic validation logic
            if not entity_text or len(entity_text) < 2:
                raise ModelRetry("Entity text too short, please provide more context")

            # Type-specific validation
            validations = {
                EntityType.GENE: lambda x: x.isupper() or any(c.isdigit() for c in x),
                EntityType.PROTEIN: lambda x: x.isupper()
                or x.endswith("ase")
                or x.endswith("in"),
                EntityType.DISEASE: lambda x: len(x.split()) > 0,
                EntityType.ORGANISM: lambda x: len(x.split()) <= 3,  # Scientific names
            }

            validator = validations.get(entity_type, lambda x: True)
            return validator(entity_text)

    async def process(
        self,
        message: str,
        deps: Optional[BioCurationDependencies] = None,
        stream: bool = False,
    ) -> BioCurationOutput:
        """
        Process a curation request.

        Args:
            message: The user's message or query
            deps: Dependencies for the agent
            stream: Whether to stream the response

        Returns:
            BioCurationOutput with structured results
        """
        if deps is None:
            deps = BioCurationDependencies()

        start_time = time.time()

        try:
            if stream:
                # Stream processing (returns async iterator)
                return await self._process_stream(message, deps)
            else:
                # Regular processing
                result = await self.agent.run(message, deps=deps)

                # Add processing metadata
                output = result.output
                output.processing_time = time.time() - start_time
                output.model_used = self.model

                # Save to history if we have a session
                if deps.db_session and deps.session_id:
                    await self._save_to_history(
                        deps.db_session,
                        deps.session_id,
                        message,
                        output.response,
                    )

                return output

        except Exception as e:
            logger.error(f"Error processing curation request: {e}")
            raise

    async def _process_stream(
        self,
        message: str,
        deps: BioCurationDependencies,
    ) -> AsyncIterator[StreamingUpdate]:
        """
        Process with streaming updates.

        Args:
            message: The user's message
            deps: Dependencies

        Yields:
            StreamingUpdate objects
        """
        async with self.agent.run_stream(message, deps=deps) as run:
            # Stream text updates
            async for text in run.stream_text():
                yield StreamingUpdate(
                    type="text",
                    content=text,
                )

            # Get final result
            result = await run.result()
            output = result.output

            # Stream entities
            for entity in output.entities:
                yield StreamingUpdate(
                    type="entity",
                    content=entity.text,
                    metadata=entity.model_dump(),
                )

            # Stream annotations
            for annotation in output.annotations:
                yield StreamingUpdate(
                    type="annotation",
                    content=annotation.text,
                    metadata=annotation.model_dump(),
                )

            # Final metadata
            yield StreamingUpdate(
                type="metadata",
                content="",
                metadata={
                    "processing_time": output.processing_time,
                    "model_used": output.model_used,
                    "confidence": output.confidence,
                },
            )

    async def _save_to_history(
        self,
        db: Session,
        session_id: str,
        user_message: str,
        assistant_response: str,
    ):
        """Save conversation to history"""
        try:
            # Save user message
            user_msg = ChatHistory(
                session_id=session_id,
                role="user",
                content=user_message,
            )
            db.add(user_msg)

            # Save assistant response
            assistant_msg = ChatHistory(
                session_id=session_id,
                role="assistant",
                content=assistant_response,
                model_provider=(
                    self.model.split(":")[0] if ":" in self.model else "unknown"
                ),
                model_name=(
                    self.model.split(":")[1] if ":" in self.model else self.model
                ),
            )
            db.add(assistant_msg)

            db.commit()
        except Exception as e:
            logger.error(f"Failed to save to history: {e}")
            db.rollback()

    def run_sync(
        self,
        message: str,
        deps: Optional[BioCurationDependencies] = None,
    ) -> BioCurationOutput:
        """
        Synchronous wrapper for processing.

        Args:
            message: The user's message
            deps: Dependencies

        Returns:
            BioCurationOutput
        """
        import asyncio

        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.process(message, deps))

    async def get_usage(self) -> Dict[str, Any]:
        """Get usage statistics for the agent"""
        # This would connect to actual usage tracking
        return {
            "total_requests": 0,
            "total_tokens": 0,
            "model": self.model,
        }
