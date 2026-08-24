"""Models package for Weaviate database control panel."""

from .document import (
    PDFDocument,
    ProcessingStatus,
    EmbeddingStatus,
    DocumentMetadata
)
from .chunk import (
    DocumentChunk,
    ElementType,
    ChunkBoundingBox,
    ChunkDocItemProvenance,
    ChunkMetadata
)
from .strategy import (
    ChunkingStrategy,
    StrategyName,
    ChunkingMethod
)
from .pipeline import (
    ProcessingStage,
    PipelineStatus,
    StageResult,
    ProcessingError
)
from .api_schemas import (
    DocumentFilter,
    PaginationParams,
    DocumentListRequest,
    DocumentListResponse,
    PaginationInfo,
    # ALL-789 made the flat DocumentResponse the canonical detail response.
    DocumentResponse,
    ChunkListResponse,
    OperationResult,
    EmbeddingConfiguration,
    WeaviateSettings,
    AvailableModel,
    AvailableModelsResponse,
    SettingsResponse,
    ReprocessRequest,
    ReembedRequest,
    SortOrder,
    SortBy
)

__all__ = [
    # Document models
    'PDFDocument',
    'ProcessingStatus',
    'EmbeddingStatus',
    'DocumentMetadata',

    # Chunk models
    'DocumentChunk',
    'ElementType',
    'ChunkBoundingBox',
    'ChunkDocItemProvenance',
    'ChunkMetadata',

    # Strategy models
    'ChunkingStrategy',
    'StrategyName',
    'ChunkingMethod',

    # Pipeline models
    'ProcessingStage',
    'PipelineStatus',
    'StageResult',
    'ProcessingError',

    # API schemas
    'DocumentFilter',
    'PaginationParams',
    'DocumentListRequest',
    'DocumentListResponse',
    'PaginationInfo',
    # The retired rich detail response family was removed after ALL-789.
    'DocumentResponse',
    'ChunkListResponse',
    'OperationResult',
    'EmbeddingConfiguration',
    'WeaviateSettings',
    'AvailableModel',
    'AvailableModelsResponse',
    'SettingsResponse',
    'ReprocessRequest',
    'ReembedRequest',
    'SortOrder',
    'SortBy'
]
