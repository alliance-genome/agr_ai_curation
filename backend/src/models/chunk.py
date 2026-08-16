"""DocumentChunk model for Weaviate database control panel."""

from enum import Enum
import logging
from typing import Optional, Dict, Any, List, Literal
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)


class ElementType(str, Enum):
    """Unstructured element type values."""
    TITLE = "Title"
    NARRATIVE_TEXT = "NarrativeText"
    TABLE = "Table"
    IMAGE = "Image"
    LIST_ITEM = "ListItem"
    FOOTER = "Footer"
    HEADER = "Header"


class ChunkBoundingBox(BaseModel):
    """PDFX-provided bounding box for a document item."""

    left: float
    top: float
    right: float
    bottom: float
    coord_origin: Literal['BOTTOMLEFT', 'TOPLEFT', 'BOTTOMRIGHT', 'TOPRIGHT'] = 'BOTTOMLEFT'

    @field_validator('right')
    @classmethod
    def validate_right(cls, v: float, info: ValidationInfo) -> float:
        left = info.data.get('left') if info.data else None
        if left is not None and v <= left:
            logger.error("BoundingBox validation failed: right (%s) must be greater than left (%s)", v, left)
            raise ValueError(f"right ({v}) must be greater than left ({left})")
        return v

    @field_validator('bottom')
    @classmethod
    def validate_bottom(cls, v: float, info: ValidationInfo) -> float:
        top = info.data.get('top') if info.data else None
        coord_origin = info.data.get('coord_origin', 'BOTTOMLEFT') if info.data else 'BOTTOMLEFT'

        if top is not None:
            # Allow bottom == top for flat lines/elements (zero height)
            if v == top:
                logger.warning(
                    "BoundingBox has zero height: top=%s, bottom=%s. This is allowed for flat elements.",
                    top,
                    v,
                )
                return v

            # For BOTTOMLEFT or BOTTOMRIGHT origins, top > bottom (Y increases upward)
            # For TOPLEFT or TOPRIGHT origins, bottom > top (Y increases downward)
            if coord_origin in ['BOTTOMLEFT', 'BOTTOMRIGHT']:
                if v > top:
                    logger.error(
                        "BoundingBox validation failed: For %s coordinates, bottom (%s) must be <= top (%s) because Y increases upward. Full bbox data: left=%s, top=%s, right=%s, bottom=%s, origin=%s",
                        coord_origin,
                        v,
                        top,
                        info.data.get("left"),
                        top,
                        info.data.get("right"),
                        v,
                        coord_origin,
                    )
                    raise ValueError(f"For {coord_origin} coordinates, bottom must be less than or equal to top (got bottom={v}, top={top})")
            else:  # TOPLEFT or TOPRIGHT
                if v < top:
                    logger.error(
                        "BoundingBox validation failed: For %s coordinates, bottom (%s) must be >= top (%s) because Y increases downward. Full bbox data: left=%s, top=%s, right=%s, bottom=%s, origin=%s",
                        coord_origin,
                        v,
                        top,
                        info.data.get("left"),
                        top,
                        info.data.get("right"),
                        v,
                        coord_origin,
                    )
                    raise ValueError(f"For {coord_origin} coordinates, bottom must be greater than or equal to top (got bottom={v}, top={top})")
        return v


class ChunkDocItemProvenance(BaseModel):
    """Provenance entry referencing a PDFX document item."""

    element_id: str = Field(..., description="PDFX element identifier")
    page: int = Field(..., ge=1, description="1-indexed page number")
    doc_item_label: Optional[str] = Field(None, description="PDFX doc_item_label for this element")
    bbox: ChunkBoundingBox


class FigureLocatorAnnotation(BaseModel):
    """One ingestion-time semantic figure/table locator mapped to chunk text."""

    text: str = Field(..., min_length=1, description="Verbatim locator text from the chunk")
    char_start: int = Field(..., ge=0, description="Inclusive chunk-local character offset")
    char_end: int = Field(..., gt=0, description="Exclusive chunk-local character offset")
    cardinality: Literal["single", "multiple", "uncertain"]
    kind: Literal["figure", "table", "unknown"]
    number: Optional[str] = None
    panels: List[str] = Field(default_factory=list)
    canonical_reference: Optional[str] = None

    @model_validator(mode="after")
    def validate_semantics(self) -> "FigureLocatorAnnotation":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.cardinality == "single" and not self.canonical_reference:
            raise ValueError("single locators require canonical_reference")
        if self.cardinality != "single" and self.canonical_reference is not None:
            raise ValueError("only single locators may have canonical_reference")
        return self


class FigureLocatorResolution(BaseModel):
    """Versioned result of the ingestion-time locator classifier."""

    schema_version: Literal[1] = 1
    prompt_version: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    reasoning: str = Field(..., min_length=1)
    status: Literal["resolved", "uncertain"]
    annotations: List[FigureLocatorAnnotation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self) -> "FigureLocatorResolution":
        if self.status == "uncertain" and self.annotations:
            raise ValueError("uncertain resolutions cannot contain mapped annotations")
        return self


class ProviderSemanticRange(BaseModel):
    """Chunk-local range where one provider reference is applicable."""

    char_start: int = Field(..., ge=0)
    char_end: int = Field(..., gt=0)

    @model_validator(mode="after")
    def validate_range(self) -> "ProviderSemanticRange":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        return self


class ProviderFigureReference(BaseModel):
    """Deterministic reference derived from structured provider sidecar fields."""

    schema_version: Literal[1] = 1
    raw_label: Optional[str] = None
    raw_number: Optional[str] = None
    status: Literal["single", "multiple", "conflict", "invalid"]
    kind: Optional[Literal["figure", "table"]] = None
    number: Optional[str] = None
    panels: List[str] = Field(default_factory=list)
    canonical_reference: Optional[str] = None
    semantic_ranges: List[ProviderSemanticRange] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_reference(self) -> "ProviderFigureReference":
        if self.status == "single":
            if not self.kind or not self.number or not self.canonical_reference:
                raise ValueError("single provider references require kind, number, and canonical_reference")
        elif self.canonical_reference is not None:
            raise ValueError("non-single provider references cannot have canonical_reference")
        return self


class ChunkMetadata(BaseModel):
    """Chunk-specific metadata."""

    character_count: int = Field(..., ge=0)
    word_count: int = Field(..., ge=0)
    has_table: bool = False
    has_image: bool = False
    chunking_strategy: Optional[str] = None
    section_path: Optional[List[str]] = None
    content_type: Optional[str] = None
    doc_items: List[ChunkDocItemProvenance] = Field(
        default_factory=list,
        description="PDFX provenance entries contributing to this chunk",
    )
    figure_locator_resolution: Optional[FigureLocatorResolution] = Field(
        None,
        description="Versioned ingestion-time semantic locator annotations",
    )
    provider_figure_reference: Optional[ProviderFigureReference] = Field(
        None,
        description="Structured provider-sidecar figure/table reference",
    )


class DocumentChunk(BaseModel):
    """Represents a chunk of a PDF document after Unstructured.io processing."""

    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(..., description="UUID chunk identifier")
    document_id: str = Field(..., description="Parent document UUID")
    chunk_index: int = Field(..., ge=0, description="Order within document")
    content: str = Field(..., min_length=1, description="Extracted text content")
    element_type: ElementType
    page_number: Optional[int] = Field(
        None,
        gt=0,
        description="One-based source page number when known",
    )
    section_title: Optional[str] = None
    section_path: Optional[List[str]] = None
    # New hierarchy fields from LLM-based section resolution
    parent_section: Optional[str] = Field(None, description="Top-level section (e.g., Methods, Results, TITLE)")
    subsection: Optional[str] = Field(None, description="Subsection name if applicable")
    is_top_level: Optional[bool] = Field(None, description="True if major section, False if subsection")
    doc_items: List[ChunkDocItemProvenance] = Field(default_factory=list)
    metadata: ChunkMetadata

    @field_validator('content')
    @classmethod
    def validate_content(cls, v: str) -> str:
        """Validate content is not empty."""
        if not v.strip():
            raise ValueError("Content must not be empty")
        return v

    @field_validator('chunk_index')
    @classmethod
    def validate_chunk_index(cls, v: int) -> int:
        """Validate chunk index is non-negative."""
        if v < 0:
            raise ValueError("Chunk index must be non-negative")
        return v

    @field_validator('page_number')
    @classmethod
    def validate_page_number(cls, v: Optional[int]) -> Optional[int]:
        """Validate a known page number is positive."""
        if v is not None and v <= 0:
            raise ValueError("Page number must be positive")
        return v

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DocumentChunk':
        """Create model from dictionary."""
        return cls(**data)
