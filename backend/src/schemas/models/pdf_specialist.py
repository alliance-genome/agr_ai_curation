"""PDF specialist envelope schema.

Used by PDF extraction specialist for document responses.
"""

from typing import List
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope
from .citation import Citation


class PdfSpecialistEnvelope(StructuredMessageEnvelope):
    """Envelope for PDF extraction specialist responses"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="pdf_specialist", description="The PDF specialist agent")
    extracted_content: str = Field(description="Content extracted from the document")
    citations: List[Citation] = Field(
        default_factory=list,
        description="Citations with page numbers and relevance scores"
    )
    search_queries: List[str] = Field(
        default_factory=list,
        description="Search queries used to find content"
    )
    chunks_found: int = Field(default=0, description="Number of relevant chunks found")
