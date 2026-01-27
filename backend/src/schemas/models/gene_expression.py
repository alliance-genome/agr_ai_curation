"""Gene expression envelope schema.

Used for gene expression curation responses - organism-agnostic.
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope
from .reagent import Reagent
from .expression_pattern import ExpressionPattern
from .expression_evidence import ExpressionEvidence


class GeneExpressionEnvelope(StructuredMessageEnvelope):
    """Envelope for gene expression curation responses - organism-agnostic"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="gene_expression_specialist", description="The gene expression curation agent")
    gene_symbol: str = Field(description="Gene symbol")
    gene_id: Optional[str] = Field(default=None, description="Gene database ID (e.g., 'WB:WBGene00001062', 'FB:FBgn0000001')")
    organism: Optional[str] = Field(default=None, description="Organism (e.g., 'C. elegans', 'D. melanogaster', 'NCBITaxon:6239')")
    reagent: Reagent = Field(description="Reagent information used for expression pattern detection")
    expression_patterns: List[ExpressionPattern] = Field(description="List of expression pattern annotations with spatio-temporal pairing (labels only)")
    evidence: ExpressionEvidence = Field(description="Evidence supporting the expression patterns")
    additional_notes: Optional[str] = Field(default=None, description="Any additional contextual notes or caveats")
