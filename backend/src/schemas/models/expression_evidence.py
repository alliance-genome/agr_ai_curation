"""Expression evidence schema.

Evidence supporting gene expression patterns from literature.
"""

from typing import List
from pydantic import BaseModel, Field, ConfigDict


class ExpressionEvidence(BaseModel):
    """Evidence supporting expression pattern"""
    model_config = ConfigDict(extra='forbid')

    text: str = Field(description="Full paragraph(s) from paper with evidence")
    page_numbers: List[int] = Field(default_factory=list, description="Page numbers where evidence was found")
    figure_references: List[str] = Field(default_factory=list, description="Figure references mentioned (e.g., 'Fig 2A', 'Figure 3')")
    internal_citations: List[str] = Field(default_factory=list, description="Internal citations (e.g., 'Mason et al., 2008', 'Pereira et al., 2019')")
