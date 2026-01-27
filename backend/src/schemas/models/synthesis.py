"""Synthesis envelope schema.

Used for final synthesis of PDF content and user query.
"""

from typing import List, Optional
from pydantic import Field, ConfigDict

from .base import StructuredMessageEnvelope


class SynthesisEnvelope(StructuredMessageEnvelope):
    """Envelope for final synthesis of PDF content and user query"""
    model_config = ConfigDict(extra='forbid')

    actor: str = Field(default="synthesis_agent", description="The synthesis agent")
    final_response: str = Field(description="The synthesized response combining all information")
    sources_used: List[str] = Field(
        default_factory=list,
        description="List of sources integrated into the response"
    )
    confidence_level: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Overall confidence in the synthesized response"
    )
