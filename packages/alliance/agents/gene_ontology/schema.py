"""Gene ontology lookup agent schema."""

from typing import Optional

from pydantic import Field, StrictBool, StrictStr

from src.schemas.domain_validator import (  # type: ignore[reportMissingImports]
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
)


class GOHierarchyEntry(DomainValidatorBaseModel):
    """One typed GO hierarchy relation returned with a term."""

    go_id: StrictStr
    name: StrictStr
    relationship_type: StrictStr


class GOTermResult(DomainValidatorBaseModel):
    """One GO term row returned by the lookup."""

    go_id: StrictStr
    name: StrictStr
    aspect: StrictStr
    definition: Optional[StrictStr] = None
    is_obsolete: Optional[StrictBool] = None
    children: list[GOHierarchyEntry] = Field(default_factory=list)
    ancestors: list[GOHierarchyEntry] = Field(default_factory=list)
    synonyms: list[StrictStr] = Field(default_factory=list)


class GOTermResultEnvelope(DomainValidatorResultBase):
    """Canonical result schema for Alliance GO term validator agents."""

    __envelope_class__ = True

    results: list[GOTermResult] = Field(
        default_factory=list,
        description="Resolved GO term facts returned by the lookup",
    )
    query_summary: Optional[str] = Field(
        default=None,
        description="Brief summary of what was queried and found",
    )
    not_found: list[str] = Field(
        default_factory=list,
        description="Terms or IDs that were not found in the ontology",
    )
