"""Expression pattern schema.

Single expression pattern annotation with spatio-temporal pairing - LABELS ONLY.
Ontology term ID mapping is handled by a separate ontology_mapping agent.
"""

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class ExpressionPattern(BaseModel):
    """Single expression pattern annotation with spatio-temporal pairing - LABELS ONLY

    This schema extracts human-readable labels from the PDF.
    Ontology term ID mapping is handled by a separate ontology_mapping agent.
    """
    model_config = ConfigDict(extra='forbid')

    anatomy_label: Optional[str] = Field(default=None, description="Anatomical location label (e.g., 'linker cell', 'vas deferens', 'intestine')")
    life_stage_label: Optional[str] = Field(default=None, description="Developmental stage/life-stage label (e.g., 'L3 larval stage', 'L4 larval stage', 'adult')")
    go_cc_label: Optional[str] = Field(default=None, description="GO Cellular Component label for sub-cellular localization (e.g., 'nucleus', 'cytoplasm', 'mitochondrion')")
    temporal_qualifier: Optional[str] = Field(default=None, description="Temporal qualifier (e.g., 'mid-stage II', 'early L4', 'closely following turning')")
    is_negative: bool = Field(default=False, description="True if this is negative evidence ('NOT expressed in')")
    sex_specificity: Optional[str] = Field(default=None, description="Sex specificity if mentioned (e.g., 'male only', 'hermaphrodite only')")
