"""DocumentChunk model for Weaviate database control panel."""

from enum import Enum
import logging
from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field, field_validator, ConfigDict, ValidationInfo

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
    """Docling-provided bounding box for a document item."""

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
            logger.error(f"❌ BoundingBox validation failed: right ({v}) must be greater than left ({left})")
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
                logger.warning(f"⚠️ BoundingBox has zero height: top={top}, bottom={v}. This is allowed for flat elements.")
                return v

            # For BOTTOMLEFT or BOTTOMRIGHT origins, top > bottom (Y increases upward)
            # For TOPLEFT or TOPRIGHT origins, bottom > top (Y increases downward)
            if coord_origin in ['BOTTOMLEFT', 'BOTTOMRIGHT']:
                if v > top:
                    logger.error(f"❌ BoundingBox validation failed: For {coord_origin} coordinates, "
                               f"bottom ({v}) must be less than or equal to top ({top}) because Y increases upward. "
                               f"Full bbox data: left={info.data.get('left')}, top={top}, "
                               f"right={info.data.get('right')}, bottom={v}, origin={coord_origin}")
                    raise ValueError(f"For {coord_origin} coordinates, bottom must be less than or equal to top (got bottom={v}, top={top})")
            else:  # TOPLEFT or TOPRIGHT
                if v < top:
                    logger.error(f"❌ BoundingBox validation failed: For {coord_origin} coordinates, "
                               f"bottom ({v}) must be greater than or equal to top ({top}) because Y increases downward. "
                               f"Full bbox data: left={info.data.get('left')}, top={top}, "
                               f"right={info.data.get('right')}, bottom={v}, origin={coord_origin}")
                    raise ValueError(f"For {coord_origin} coordinates, bottom must be greater than or equal to top (got bottom={v}, top={top})")
        return v


class ChunkDocItemProvenance(BaseModel):
    """Provenance entry referencing a Docling document item."""

    element_id: str = Field(..., description="Docling element identifier")
    page: int = Field(..., ge=1, description="1-indexed page number")
    doc_item_label: Optional[str] = Field(None, description="Docling doc_item_label for this element")
    bbox: ChunkBoundingBox


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
        description="Docling provenance entries contributing to this chunk",
    )


class DocumentChunk(BaseModel):
    """Represents a chunk of a PDF document after Unstructured.io processing."""

    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(..., description="UUID chunk identifier")
    document_id: str = Field(..., description="Parent document UUID")
    chunk_index: int = Field(..., ge=0, description="Order within document")
    content: str = Field(..., min_length=1, description="Extracted text content")
    element_type: ElementType
    page_number: int = Field(..., gt=0, description="Source page number")
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
    def validate_page_number(cls, v: int) -> int:
        """Validate page number is positive."""
        if v <= 0:
            raise ValueError("Page number must be positive")
        return v

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DocumentChunk':
        """Create model from dictionary."""
        return cls(**data)
