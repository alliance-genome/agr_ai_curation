"""Ontology mapping lookup agent schema."""

from typing import Any, Optional

from pydantic import Field

from src.schemas.domain_validator import DomainValidatorResultBase


class OntologyMappingEnvelope(DomainValidatorResultBase):
    """Canonical result schema for Alliance ontology-mapping validator agents."""

    __envelope_class__ = True

    mappings: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Term-to-ontology mappings returned by the lookup",
    )
    unmapped_labels: Optional[list[str]] = Field(
        default=None,
        description="Terms that could not be mapped",
    )
