"""Reference validation agent schema."""

from typing import Any, Literal, Optional

from pydantic import Field

from src.schemas.domain_validator import DomainValidatorResultBase


ReferenceMatchType = Literal[
    "exact_identifier",
    "exact_title",
    "fuzzy_search",
    "ambiguous",
    "no_match",
    "upstream_failure",
]


class ReferenceValidationResult(DomainValidatorResultBase):
    """Canonical result schema for Alliance reference validator agents."""

    reference_id: Optional[int | str] = Field(
        default=None,
        description="Resolved Alliance reference primary identifier when available",
    )
    curie: Optional[str] = Field(
        default=None,
        description="Resolved Alliance reference CURIE such as AGRKB:101000000924191",
    )
    title: Optional[str] = Field(
        default=None,
        description="Resolved reference title when returned by the API-backed lookup",
    )
    short_citation: Optional[str] = Field(
        default=None,
        description="Resolved short citation when returned by the API-backed lookup",
    )
    cross_references: list[str] = Field(
        default_factory=list,
        description="PMID, DOI, MOD, or other source cross references returned by the API",
    )
    source: Optional[str] = Field(
        default=None,
        description="API source for the resolved reference, expected to be literature_es",
    )
    match_type: Optional[ReferenceMatchType] = Field(
        default=None,
        description="Reference lookup path or unresolved classification used for the decision",
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Validator confidence for the selected reference when unambiguous",
    )
    ambiguity: Optional[dict[str, Any]] = Field(
        default=None,
        description="Ambiguity details from the reference lookup tool",
    )
    no_match: Optional[dict[str, Any]] = Field(
        default=None,
        description="No-match details from the reference lookup tool",
    )
    candidate_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Raw API-backed candidate references preserved for curator review",
    )
    failure_classification: Optional[str] = Field(
        default=None,
        description="Upstream tool failure classification, when lookup could not complete",
    )
