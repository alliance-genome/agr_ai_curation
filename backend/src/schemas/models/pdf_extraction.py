"""PDF extraction envelope schema.

Alias for PDF specialist envelope (used for PDF extraction specialist responses).
"""

from typing import List
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope
from .citation import Citation


class PdfExtractionEnvelope(StructuredMessageEnvelope):
    """Envelope for PDF extraction specialist responses (alias for PdfSpecialistEnvelope)"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="pdf_extraction_specialist", description="The PDF extraction agent")
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
