"""Pipeline state models for document processing."""

from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, ConfigDict


class ProcessingStage(str, Enum):
    """Processing pipeline stages."""
    PENDING = "pending"
    UPLOAD = "upload"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    STORING = "storing"
    COMPLETED = "completed"
    FAILED = "failed"


class StageResult(BaseModel):
    """Result of a single processing stage."""

    model_config = ConfigDict(use_enum_values=True)

    stage: ProcessingStage
    success: bool
    started_at: datetime
    completed_at: datetime
    duration_seconds: float = Field(..., gt=0)
    message: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        data = self.model_dump()
        data['started_at'] = self.started_at.isoformat()
        data['completed_at'] = self.completed_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StageResult':
        """Create model from dictionary."""
        if isinstance(data.get('started_at'), str):
            data['started_at'] = datetime.fromisoformat(data['started_at'])
        if isinstance(data.get('completed_at'), str):
            data['completed_at'] = datetime.fromisoformat(data['completed_at'])
        return cls(**data)


class PipelineStatus(BaseModel):
    """Current status of document processing pipeline."""

    model_config = ConfigDict(use_enum_values=True)

    document_id: str = Field(..., description="Document being processed")
    current_stage: ProcessingStage
    started_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    progress_percentage: int = Field(default=0, ge=0, le=100)
    message: Optional[str] = None
    error_count: int = Field(default=0, ge=0)
    processing_time_seconds: Optional[float] = Field(default=None, ge=0)
    stage_results: List[StageResult] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        data = self.model_dump()
        data['started_at'] = self.started_at.isoformat()
        data['updated_at'] = self.updated_at.isoformat()
        if self.completed_at:
            data['completed_at'] = self.completed_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PipelineStatus':
        """Create model from dictionary."""
        if isinstance(data.get('started_at'), str):
            data['started_at'] = datetime.fromisoformat(data['started_at'])
        if isinstance(data.get('updated_at'), str):
            data['updated_at'] = datetime.fromisoformat(data['updated_at'])
        if data.get('completed_at') and isinstance(data['completed_at'], str):
            data['completed_at'] = datetime.fromisoformat(data['completed_at'])
        return cls(**data)


class ProcessingError(BaseModel):
    """Error during processing pipeline."""

    stage: ProcessingStage
    error_code: str
    error_message: str
    timestamp: datetime
    document_id: str
    retry_count: int = Field(default=0, ge=0)
    is_retryable: bool = True
    stack_trace: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert model to dictionary."""
        data = self.model_dump()
        data['timestamp'] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProcessingError':
        """Create model from dictionary."""
        if isinstance(data.get('timestamp'), str):
            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)
