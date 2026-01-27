"""ChunkingStrategy model for Weaviate database control panel."""

from enum import Enum
from typing import List
from pydantic import BaseModel, Field, field_validator, ConfigDict, ValidationInfo


class StrategyName(str, Enum):
    """Strategy name - research only."""

    RESEARCH = "research"


class ChunkingMethod(str, Enum):
    """Unstructured chunking strategy values."""
    BY_TITLE = "by_title"
    BY_PARAGRAPH = "by_paragraph"
    BY_CHARACTER = "by_character"
    BY_SENTENCE = "by_sentence"


class ChunkingStrategy(BaseModel):
    """Configuration for Unstructured.io chunking parameters."""

    model_config = ConfigDict(use_enum_values=True)

    strategy_name: StrategyName = Field(..., description="Predefined strategy name")
    chunking_method: ChunkingMethod = Field(..., description="Unstructured chunking strategy")
    max_characters: int = Field(..., ge=500, le=5000, description="Maximum chunk size")
    overlap_characters: int = Field(..., ge=0, description="Character overlap between chunks")
    include_metadata: bool = Field(default=True, description="Include element metadata")
    exclude_element_types: List[str] = Field(default_factory=list, description="Elements to skip")

    @field_validator('max_characters')
    @classmethod
    def validate_max_characters(cls, v: int) -> int:
        """Validate max_characters is within range."""
        if v < 500 or v > 5000:
            raise ValueError("max_characters must be between 500 and 5000")
        return v

    @field_validator('overlap_characters')
    @classmethod
    def validate_overlap(cls, v: int, info: ValidationInfo) -> int:
        """Validate overlap is less than max_characters/2."""
        if info.data and 'max_characters' in info.data:
            max_chars = info.data['max_characters']
            if v >= max_chars / 2:
                raise ValueError("overlap_characters must be less than max_characters/2")
        return v

    @field_validator('chunking_method')
    @classmethod
    def validate_chunking_method(cls, v: ChunkingMethod) -> ChunkingMethod:
        """Validate chunking method is valid Unstructured strategy."""
        valid_methods = {ChunkingMethod.BY_TITLE, ChunkingMethod.BY_PARAGRAPH,
                        ChunkingMethod.BY_CHARACTER, ChunkingMethod.BY_SENTENCE}
        if v not in valid_methods:
            raise ValueError(f"chunking_method must be one of {valid_methods}")
        return v

    def to_dict(self) -> dict:
        """Convert model to dictionary."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict) -> 'ChunkingStrategy':
        """Create model from dictionary."""
        return cls(**data)

    @classmethod
    def get_research_strategy(cls) -> 'ChunkingStrategy':
        """Get the research document chunking strategy."""
        return cls(
            strategy_name=StrategyName.RESEARCH,
            chunking_method=ChunkingMethod.BY_TITLE,
            max_characters=1500,
            overlap_characters=200,
            include_metadata=True,
            exclude_element_types=["Footer", "Header"],
        )

    @classmethod
    def get_default_strategies(cls) -> dict[StrategyName, 'ChunkingStrategy']:
        """Return the single supported strategy."""

        return {StrategyName.RESEARCH: cls.get_research_strategy()}
