"""
BioCuration Agent using PydanticAI

This agent handles biological curation tasks including entity extraction,
annotation suggestions, and contextual analysis of scientific documents.
"""

import os
import logging
from typing import Optional, List, Dict, Any, AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
import time

from pydantic_ai import Agent, RunContext, ModelRetry
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
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
    EntityExtractionOutput,
)
from .entity_extraction_agent import EntityExtractionAgent
from ..database import get_db
from ..models import ChatHistory

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


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
        max_history_messages: int = 20,
        enable_history_summary: bool = True,
        entity_confidence_threshold: float = 0.7,
    ):
        """
        Initialize the BioCuration agent.

        Args:
            model: The AI model to use (e.g., "openai:gpt-4o", "google-gla:gemini-1.5-flash")
            system_prompt: Optional custom system prompt
            max_history_messages: Maximum messages to keep in history
            enable_history_summary: Whether to summarize old messages
            entity_confidence_threshold: Minimum confidence for entity extraction
        """
        self.model = model
        self.system_prompt = system_prompt
        self.max_history_messages = max_history_messages
        self.enable_history_summary = enable_history_summary
        self.entity_confidence_threshold = entity_confidence_threshold

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

        # Set up history processors
        history_processors = []
        if self.max_history_messages > 0:
            history_processors.append(self._keep_recent_messages)
        if self.enable_history_summary:
            history_processors.append(self._summarize_old_messages)

        # Create the PydanticAI agent
        # Using str output type for streaming support
        self.agent = Agent(
            model,
            deps_type=BioCurationDependencies,
            output_type=str,  # Plain text for streaming
            system_prompt=self.system_prompt,
            history_processors=history_processors if history_processors else None,
        )

        # Create summary agent if enabled
        if self.enable_history_summary:
            # TODO: Make summary model configurable via Settings page
            # For now, use a cheaper model for summaries
            summary_model = os.getenv("SUMMARY_MODEL", "openai:gpt-4o-mini")
            self.summary_agent = Agent(
                summary_model,
                system_prompt="""Summarize this biological curation conversation.
Focus on: identified entities, key findings, annotation suggestions.
Keep the summary concise and technical.""",
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

        @self.agent.tool(name="extract_entities")
        async def extract_entities(
            ctx: RunContext[BioCurationDependencies],
            text: str,
            entity_types: Optional[List[str]] = None,
        ) -> Dict[str, Any]:
            """
            Extract biological entities from text using a specialized sub-agent.

            This tool uses the EntityExtractionAgent to identify and extract
            structured biological entities like genes, proteins, diseases, etc.

            Args:
                text: The text to extract entities from
                entity_types: Optional list of entity types to focus on

            Returns:
                Dictionary containing extracted entities and metadata
            """
            try:
                # Create entity extraction agent
                extractor = EntityExtractionAgent(
                    model="openai:gpt-4o-mini",  # Use faster model for extraction
                    min_confidence=self.entity_confidence_threshold,
                )

                # Extract entities
                result = await extractor.extract(text)

                # Filter by confidence
                filtered_entities = extractor.filter_by_confidence(result.entities)

                # Convert to serializable format
                return {
                    "entities": [
                        {
                            "text": e.text,
                            "type": e.type.value,
                            "normalized": e.normalized_form,
                            "database_id": e.database_id,
                            "confidence": e.confidence,
                            "context": e.context,
                        }
                        for e in filtered_entities
                    ],
                    "total": len(filtered_entities),
                    "summary": result.summary,
                    "breakdown": {
                        k.value: v for k, v in result.entity_breakdown.items()
                    },
                }
            except Exception as e:
                logger.error(f"Entity extraction failed: {e}")
                return {"error": str(e), "entities": [], "total": 0}

        # Store internal tool reference
        self._extract_entities_tool_func = extract_entities

    async def extract_entities_tool(
        self,
        deps: BioCurationDependencies,
        text: str,
        min_confidence: Optional[float] = None,
        use_document: bool = False,
    ) -> Dict[str, Any]:
        """
        Public method to extract entities from text.

        This is a wrapper around the tool for direct access.

        Args:
            deps: Dependencies including context
            text: Text to extract from (ignored if use_document=True)
            min_confidence: Minimum confidence threshold
            use_document: If True, use document text from context

        Returns:
            Dictionary with extracted entities
        """
        try:
            # Use document text if requested and available
            if use_document and deps.context and deps.context.document_text:
                text = deps.context.document_text

            # Create entity extraction agent
            extractor = EntityExtractionAgent(
                model="openai:gpt-4o-mini",
                min_confidence=min_confidence or self.entity_confidence_threshold,
            )

            # Extract entities
            result = await extractor.extract(text)

            # Filter by confidence
            filtered_entities = extractor.filter_by_confidence(result.entities)

            # Convert to serializable format
            return {
                "entities": [
                    {
                        "text": e.text,
                        "type": e.type.value,
                        "normalized": e.normalized_form,
                        "database_id": e.database_id,
                        "confidence": e.confidence,
                        "context": e.context,
                    }
                    for e in filtered_entities
                ],
                "total": len(filtered_entities),
                "summary": result.summary,
                "breakdown": {k.value: v for k, v in result.entity_breakdown.items()},
            }
        except Exception as e:
            logger.error(f"Entity extraction failed: {e}")
            return {"error": str(e), "entities": [], "total": 0}

    async def _keep_recent_messages(
        self, messages: List[ModelMessage]
    ) -> List[ModelMessage]:
        """
        History processor to keep only recent messages.

        Args:
            messages: Full message history

        Returns:
            Filtered message list
        """
        if len(messages) > self.max_history_messages:
            # Keep the system prompt (first message) and recent messages
            return messages[:1] + messages[-(self.max_history_messages - 1) :]
        return messages

    async def _summarize_old_messages(
        self, messages: List[ModelMessage]
    ) -> List[ModelMessage]:
        """
        History processor to summarize old messages when history gets long.

        Args:
            messages: Full message history

        Returns:
            Messages with old ones summarized
        """
        # Only summarize if we have more than 2x the max messages
        threshold = self.max_history_messages * 2
        if len(messages) > threshold and hasattr(self, "summary_agent"):
            try:
                # Take the oldest messages to summarize (keep recent ones intact)
                messages_to_summarize = messages[
                    1 : threshold // 2
                ]  # Skip system prompt
                recent_messages = messages[threshold // 2 :]

                # Create a summary of old messages
                summary_result = await self.summary_agent.run(
                    "Summarize the key points from this conversation",
                    message_history=messages_to_summarize,
                )

                # Return system prompt + summary + recent messages
                return messages[:1] + summary_result.new_messages() + recent_messages
            except Exception as e:
                logger.warning(f"Failed to summarize messages: {e}")
                # Fall back to just keeping recent messages
                return await self._keep_recent_messages(messages)

        return messages

    async def process(
        self,
        message: str,
        deps: Optional[BioCurationDependencies] = None,
        stream: bool = False,
        message_history: Optional[List[ModelMessage]] = None,
    ):
        """
        Process a curation request.

        Args:
            message: The user's message or query
            deps: Dependencies for the agent
            stream: Whether to stream the response
            message_history: Previous conversation messages

        Returns:
            For non-streaming: tuple of (BioCurationOutput, new_messages for history)
            For streaming: AsyncIterator of StreamingUpdate objects
        """
        if deps is None:
            deps = BioCurationDependencies()

        start_time = time.time()

        try:
            if stream:
                # Stream processing (returns async iterator)
                return await self._process_stream(message, deps, message_history)
            else:
                # Regular processing
                result = await self.agent.run(
                    message,
                    deps=deps,
                    message_history=message_history,
                )

                # The result.output is now a string
                text_response = result.output

                # Create a BioCurationOutput for compatibility
                # (We'll remove this later when we update the frontend)
                output = BioCurationOutput(
                    response=text_response,
                    entities=[],  # Will be populated by tools later
                    annotations=[],  # Will be populated by tools later
                    confidence=1.0,
                    requires_review=False,
                    processing_time=time.time() - start_time,
                    model_used=self.model,
                )

                # Save to history if we have a session
                if deps.db_session and deps.session_id:
                    await self._save_to_history(
                        deps.db_session,
                        deps.session_id,
                        message,
                        text_response,
                    )

                return output, result.new_messages()

        except Exception as e:
            logger.error(f"Error processing curation request: {e}")
            raise

    async def _process_stream(
        self,
        message: str,
        deps: BioCurationDependencies,
        message_history: Optional[List[ModelMessage]] = None,
        use_delta: bool = True,
    ) -> AsyncIterator[StreamingUpdate]:
        """
        Process with streaming updates.

        Now using text output for real streaming support!

        Args:
            message: The user's message
            deps: Dependencies
            message_history: Previous conversation messages
            use_delta: Whether to stream deltas (more efficient)

        Yields:
            StreamingUpdate objects
        """
        logger.info(f"Starting stream processing for message: {message[:50]}...")

        async with self.agent.run_stream(
            message,
            deps=deps,
            message_history=message_history,
        ) as stream_result:
            logger.info("Stream context created, starting text streaming...")

            # Stream the text response
            async for text_chunk in stream_result.stream_text(delta=use_delta):
                logger.debug(
                    f"Streaming {'delta' if use_delta else 'full'} text: {text_chunk[:50]}..."
                )
                yield StreamingUpdate(
                    type="text_delta" if use_delta else "text",
                    content=text_chunk,
                )

            # After streaming completes, send message history
            from pydantic_core import to_jsonable_python

            yield StreamingUpdate(
                type="history",
                content="",
                metadata={
                    "messages": to_jsonable_python(stream_result.new_messages()),
                },
            )

            # For now, we'll add entity/annotation extraction as tools later
            # Just get the chat working with streaming text first

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
        output, _ = loop.run_until_complete(self.process(message, deps))
        return output

    async def get_usage(self) -> Dict[str, Any]:
        """Get usage statistics for the agent"""
        # This would connect to actual usage tracking
        return {
            "total_requests": 0,
            "total_tokens": 0,
            "model": self.model,
        }
