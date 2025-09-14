"""
Pydantic models for agent inputs and outputs
"""

from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum


class EntityType(str, Enum):
    """Types of biological entities"""

    GENE = "gene"
    PROTEIN = "protein"
    DISEASE = "disease"
    PHENOTYPE = "phenotype"
    CHEMICAL = "chemical"
    PATHWAY = "pathway"
    ORGANISM = "organism"
    CELL_TYPE = "cell_type"
    ANATOMICAL = "anatomical"
    OTHER = "other"


class HighlightColor(str, Enum):
    """Available highlight colors for annotations"""

    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    PURPLE = "purple"
    ORANGE = "orange"
    PINK = "pink"


class ExtractedEntity(BaseModel):
    """A biological entity extracted from text"""

    text: str = Field(description="The entity text as it appears in the document")
    type: EntityType = Field(description="The type of biological entity")
    normalized_form: Optional[str] = Field(
        None, description="Normalized or standard form of the entity"
    )
    database_id: Optional[str] = Field(
        None, description="Database identifier (e.g., NCBI Gene ID, UniProt ID)"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score for the extraction"
    )
    context: Optional[str] = Field(
        None, description="Surrounding text context for the entity"
    )


class AnnotationSuggestion(BaseModel):
    """Suggested annotation for a text segment"""

    text: str = Field(description="The text to be annotated")
    start_position: Optional[int] = Field(
        None, description="Starting character position in the document"
    )
    end_position: Optional[int] = Field(
        None, description="Ending character position in the document"
    )
    color: HighlightColor = Field(
        description="Suggested highlight color for the annotation"
    )
    category: str = Field(description="Category or reason for the annotation")
    note: Optional[str] = Field(
        None, description="Additional notes about the annotation"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence score for the suggestion"
    )


class EntityExtractionOutput(BaseModel):
    """Output from entity extraction task"""

    entities: List[ExtractedEntity] = Field(
        description="List of extracted biological entities"
    )
    summary: str = Field(description="Brief summary of the extraction results")
    total_entities: int = Field(description="Total number of entities found")
    entity_breakdown: Dict[EntityType, int] = Field(
        description="Count of entities by type"
    )


class BioCurationOutput(BaseModel):
    """Comprehensive output from biocuration agent"""

    # Core response
    response: str = Field(description="Main response text addressing the user's query")

    # Entity extraction
    entities: List[ExtractedEntity] = Field(
        default_factory=list, description="Biological entities mentioned in the context"
    )

    # Annotation suggestions
    annotations: List[AnnotationSuggestion] = Field(
        default_factory=list, description="Suggested annotations for the document"
    )

    # Metadata
    confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence in the curation"
    )
    requires_review: bool = Field(
        default=False, description="Whether this curation requires human review"
    )
    curation_category: Optional[str] = Field(
        None, description="Category of curation task performed"
    )

    # Additional insights
    key_findings: List[str] = Field(
        default_factory=list, description="Key findings or insights from the analysis"
    )
    references: List[str] = Field(
        default_factory=list, description="References or citations mentioned"
    )

    # Processing metadata
    processing_time: Optional[float] = Field(
        None, description="Time taken to process in seconds"
    )
    model_used: Optional[str] = Field(
        None, description="AI model used for this curation"
    )


class CurationContext(BaseModel):
    """Context information for curation tasks"""

    document_text: Optional[str] = Field(
        None, description="Full or partial text of the document being curated"
    )
    document_id: Optional[str] = Field(
        None, description="Unique identifier for the document"
    )
    document_type: Optional[str] = Field(
        None, description="Type of document (e.g., research paper, review, abstract)"
    )
    selected_text: Optional[str] = Field(
        None, description="Specific text selected by the user"
    )
    page_number: Optional[int] = Field(
        None, description="Current page number in the document"
    )
    existing_annotations: List[AnnotationSuggestion] = Field(
        default_factory=list, description="Existing annotations in the document"
    )
    user_preferences: Dict[str, Any] = Field(
        default_factory=dict, description="User preferences for curation"
    )


class StreamingUpdate(BaseModel):
    """Simplified update for streaming responses - only text and status"""

    type: Literal[
        "text_delta",  # Streaming conversational text
        "status",  # Tool status update
        "tool_complete",  # Tool finished with complete results
        "complete",  # Everything done
    ] = Field(description="Type of update")
    content: str = Field(description="Content of the update")
    metadata: Optional[Dict[str, Any]] = Field(
        None, description="Optional metadata (complete tool results, not streamed)"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="Timestamp of the update"
    )


class AgentRequest(BaseModel):
    """Request to an agent"""

    message: str = Field(description="User's message or query")
    context: Optional[CurationContext] = Field(
        None, description="Context for the curation task"
    )
    session_id: Optional[str] = Field(
        None, description="Session ID for conversation continuity"
    )
    stream: bool = Field(default=False, description="Whether to stream the response")
    include_entities: bool = Field(
        default=True, description="Whether to extract entities"
    )
    include_annotations: bool = Field(
        default=True, description="Whether to suggest annotations"
    )
    model_preference: Optional[str] = Field(
        None, description="Preferred AI model to use"
    )
    message_history: Optional[List[Dict[str, Any]]] = Field(
        None, description="Serialized message history for conversation context"
    )


class AgentResponse(BaseModel):
    """Response from an agent"""

    output: BioCurationOutput = Field(description="The agent's output")
    session_id: str = Field(description="Session ID for conversation continuity")
    usage: Optional[Dict[str, Any]] = Field(None, description="Token usage information")
    model: str = Field(description="Model that was used")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="Response timestamp"
    )
    message_history: Optional[List[Dict[str, Any]]] = Field(
        None, description="Updated message history for next request"
    )
