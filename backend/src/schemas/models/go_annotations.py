"""GO annotations envelope schema.

Used by GO annotations specialist.
"""

from typing import List
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope


class GoAnnotationsEnvelope(StructuredMessageEnvelope):
    """Envelope for GO annotations specialist responses"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="go_annotations_specialist", description="The GO annotations agent")
    findings: str = Field(description="GO annotation information")
    annotations: List[str] = Field(
        default_factory=list,
        description="GO annotation details"
    )
    gene_products: List[str] = Field(
        default_factory=list,
        description="Gene products annotated"
    )
