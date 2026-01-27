"""Ontology mapping item schema.

Single ontology term mapping from label to CURIE.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


class OntologyMapping(BaseModel):
    """Single ontology term mapping from label to CURIE"""
    model_config = ConfigDict(extra='forbid')

    label: str = Field(description="Original label from PDF (e.g., 'linker cell', 'L3 larval stage', 'nucleus')")
    curie: Optional[str] = Field(default=None, description="Mapped ontology term CURIE (e.g., 'WBbt:0005062', 'WBls:0000035', 'GO:0005634')")
    name: Optional[str] = Field(default=None, description="Canonical term name from database (may differ from label)")
    ontology_type: Optional[str] = Field(default=None, description="Ontology type (e.g., 'WBBTTerm', 'WBLSTerm', 'GOTerm')")
    confidence: str = Field(default="high", description="Confidence level: 'high' (exact match), 'medium' (fuzzy match), 'low' (no match found)")
    alternatives: List[str] = Field(default_factory=list, description="Alternative CURIEs if mapping is ambiguous")
