"""Gene validation agent schema."""

from typing import Any, Optional

from pydantic import Field

from src.schemas.domain_validator import DomainValidatorResultBase


class GeneResultEnvelope(DomainValidatorResultBase):
    """Canonical result schema for Alliance gene validator agents."""

    __envelope_class__ = True

    results: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved Alliance Gene facts returned by the lookup",
    )
    query_summary: Optional[str] = Field(
        default=None,
        description="Brief summary of what was queried and found",
    )
    not_found: list[str] = Field(
        default_factory=list,
        description="Symbols or IDs that were not found in the database",
    )
