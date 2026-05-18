"""Generic ontology term validation agent schema."""

from typing import Any, Optional

from pydantic import Field, StrictStr

from src.schemas.domain_validator import (
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
)


class OntologyTermCandidateDetail(DomainValidatorBaseModel):
    """Ontology term candidate facts preserved alongside generic candidates."""

    curie: StrictStr = Field(description="Ontology term CURIE")
    label: Optional[StrictStr] = Field(
        default=None, description="Preferred term label or name returned by lookup"
    )
    ontology_type: Optional[StrictStr] = Field(
        default=None, description="Curation DB ontologytermtype or API ontology type"
    )
    ontology_family: Optional[StrictStr] = Field(
        default=None,
        description=(
            "Request ontology family, such as phenotype, evidence, taxon, "
            "anatomy, stage, disease, condition, or go"
        ),
    )
    namespace: Optional[StrictStr] = Field(
        default=None, description="Ontology namespace returned by lookup"
    )
    accepted_prefix: Optional[StrictStr] = Field(
        default=None, description="Accepted CURIE prefix that matched this candidate"
    )
    definition: Optional[StrictStr] = Field(
        default=None, description="Definition returned by lookup when available"
    )
    synonyms: list[StrictStr] = Field(
        default_factory=list,
        description="Synonyms or alternate labels returned by lookup",
    )
    match_type: Optional[StrictStr] = Field(
        default=None,
        description=(
            "Lookup match type, such as curie, exact_label, synonym, or "
            "partial_label"
        ),
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Organism, provider, aspect, or policy context used for the lookup",
    )


class OntologyTermValidationResult(DomainValidatorResultBase):
    """Canonical result schema for generic Alliance ontology term validators."""

    __envelope_class__ = True

    ontology_term_candidates: list[OntologyTermCandidateDetail] = Field(
        default_factory=list,
        description="Ontology-specific candidates considered or resolved by lookup",
    )
