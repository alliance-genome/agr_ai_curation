"""Reagent schema.

Used for expression pattern detection in gene expression curation.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class Reagent(BaseModel):
    """Reagent information for expression pattern detection"""
    model_config = ConfigDict(extra='forbid')

    type: str = Field(description="Reagent type: transcriptional_fusion, crispr_knockin, antibody, in_situ, rt_pcr, etc.")
    name: str = Field(description="Reagent name with full genotype details (e.g., 'fsIs2 [dmd-3::YFP, cc::GFP]')")
    genotype: Optional[str] = Field(default=None, description="Full genotype information if available")
    strain: Optional[str] = Field(default=None, description="Strain information (e.g., 'DF268', 'OH15732')")
