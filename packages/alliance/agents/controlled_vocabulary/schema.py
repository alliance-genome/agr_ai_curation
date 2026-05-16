"""Controlled vocabulary validation agent schema."""

from typing import Any, Optional

from pydantic import Field, StrictBool, StrictInt, StrictStr

from src.schemas.domain_validator import (
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
)


class ControlledVocabularyCandidateDetail(DomainValidatorBaseModel):
    """Controlled vocabulary candidate facts preserved alongside generic candidates."""

    internal_id: StrictInt = Field(description="Internal vocabularyterm ID")
    vocabulary: StrictStr = Field(description="Vocabulary name returned by lookup")
    term_name: StrictStr = Field(description="Canonical vocabulary term name")
    vocabulary_label: Optional[StrictStr] = Field(
        default=None, description="Curator-facing vocabulary label"
    )
    abbreviation: Optional[StrictStr] = Field(
        default=None, description="Vocabulary term abbreviation"
    )
    definition: Optional[StrictStr] = Field(
        default=None, description="Vocabulary term definition"
    )
    obsolete: StrictBool = Field(
        default=False, description="Whether the vocabulary term is obsolete"
    )
    synonyms: list[StrictStr] = Field(
        default_factory=list,
        description="Controlled vocabulary synonyms returned by lookup",
    )
    match_type: Optional[StrictStr] = Field(
        default=None,
        description="Lookup match type, such as exact_name, abbreviation, synonym, or partial_name",
    )
    matched_value: Optional[StrictStr] = Field(
        default=None, description="Input value or returned value that matched this candidate"
    )
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Vocabulary policy, binding, and lookup context used for the candidate",
    )


class ControlledVocabularyValidationResult(DomainValidatorResultBase):
    """Canonical result schema for Alliance controlled vocabulary validators."""

    __envelope_class__ = True

    controlled_vocabulary_candidates: list[ControlledVocabularyCandidateDetail] = Field(
        default_factory=list,
        description="Vocabulary-specific candidates considered or resolved by lookup",
    )
