"""Disease validation agent schema."""

from typing import Any, Optional

from pydantic import Field

from src.schemas.domain_validator import DomainValidatorResultBase


class DiseaseResultEnvelope(DomainValidatorResultBase):
    """Canonical result schema for Alliance disease validator agents."""

    __envelope_class__ = True

    results: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved disease ontology facts returned by the lookup",
    )
    query_summary: Optional[str] = Field(
        default=None,
        description="Brief summary of what was queried and found",
    )
    not_found: list[str] = Field(
        default_factory=list,
        description="Terms or IDs that were not found in the ontology",
    )
