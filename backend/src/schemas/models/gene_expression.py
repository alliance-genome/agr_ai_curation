"""Gene expression envelope schema.

Used for gene expression curation responses - organism-agnostic.
"""

from typing import List, Optional
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
from .reagent import Reagent
from .expression_pattern import ExpressionPattern
from .expression_evidence import ExpressionEvidence


class GeneExpressionEnvelope(StructuredMessageEnvelope):
    """Envelope for gene expression curation responses - organism-agnostic"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="gene_expression_specialist", description="The gene expression curation agent")
    gene_symbol: str = Field(description="Gene symbol")
    gene_id: Optional[str] = Field(default=None, description="Gene database ID (e.g., 'WB:WBGene00001062', 'FB:FBgn0000001')")
    organism: Optional[str] = Field(default=None, description="Organism (e.g., 'C. elegans', 'D. melanogaster', 'NCBITaxon:10090')")
    reagent: Reagent = Field(description="Reagent information used for expression pattern detection")
    expression_patterns: List[ExpressionPattern] = Field(description="List of expression pattern annotations with spatio-temporal pairing (labels only)")
    evidence: ExpressionEvidence = Field(description="Evidence supporting the expression patterns")
    items: List[ExtractionItem] = Field(default_factory=list, description="Normalized extraction items retained for curation")
    raw_mentions: List[MentionCandidate] = Field(default_factory=list, description="Raw mentions harvested before normalization")
    evidence_records: List[EvidenceRecord] = Field(default_factory=list, description="Evidence snippets attached to keep/exclude decisions")
    normalization_notes: List[str] = Field(default_factory=list, description="Normalization decisions and caveats")
    exclusions: List[ExclusionRecord] = Field(default_factory=list, description="Candidates excluded by policy with explicit reason codes")
    ambiguities: List[AmbiguityRecord] = Field(default_factory=list, description="Candidates requiring curator follow-up")
    run_summary: ExtractionRunSummary = Field(default_factory=ExtractionRunSummary, description="Run-level extraction counts and warnings")
    additional_notes: Optional[str] = Field(default=None, description="Any additional contextual notes or caveats")
