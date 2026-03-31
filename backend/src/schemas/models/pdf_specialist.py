"""PDF specialist envelope schema.

Used by PDF extraction specialist for document responses.
"""

from typing import List
from pydantic import Field, ConfigDict

from .base import (
    StructuredMessageEnvelope,
    ExtractionItem,
    MentionCandidate,
    EvidenceRecord,
    ExclusionRecord,
    AmbiguityRecord,
    ExtractionRunSummary,
)
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
    items: List[ExtractionItem] = Field(default_factory=list, description="Normalized extraction items retained for curation")
    raw_mentions: List[MentionCandidate] = Field(default_factory=list, description="Raw mentions harvested before normalization")
    evidence_records: List[EvidenceRecord] = Field(
        default_factory=list,
        description="Canonical verified evidence registry populated from record_evidence tool calls",
    )
    normalization_notes: List[str] = Field(default_factory=list, description="Normalization decisions and caveats")
    exclusions: List[ExclusionRecord] = Field(default_factory=list, description="Candidates excluded by policy with explicit reason codes")
    ambiguities: List[AmbiguityRecord] = Field(default_factory=list, description="Candidates requiring curator follow-up")
    run_summary: ExtractionRunSummary = Field(default_factory=ExtractionRunSummary, description="Run-level extraction counts and warnings")
