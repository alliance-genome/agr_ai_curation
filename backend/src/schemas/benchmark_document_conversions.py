"""Public contract for converting papers into frozen benchmark inputs."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from src.lib.benchmarks.document_conversions import (
    CONVERSION_STAGES,
    MAX_SOURCE_REFERENCE_LENGTH,
    SOURCE_REFERENCE_PATTERN,
)
from src.lib.benchmarks.models import FrozenStrictModel
from src.lib.benchmarks.snapshots import FrozenBenchmarkInputSnapshot

BenchmarkDocumentConversionState = Literal["queued", "running", "succeeded", "failed"]
BenchmarkDocumentConversionStage = Literal["fetching_source", "extracting_text", "saving"]


class BenchmarkDocumentConversionSourceReferenceRequest(FrozenStrictModel):
    """JSON body naming one paper by an identifier the configured document source resolves."""

    source_reference: str = Field(
        min_length=1,
        max_length=MAX_SOURCE_REFERENCE_LENGTH,
        pattern=SOURCE_REFERENCE_PATTERN,
    )


class BenchmarkDocumentConversionAccepted(FrozenStrictModel):
    conversion_id: UUID
    status: BenchmarkDocumentConversionState


class BenchmarkDocumentConversionFailure(FrozenStrictModel):
    code: str
    message: str


class BenchmarkDocumentConversionProgress(FrozenStrictModel):
    """Which coarse step a running conversion is on: ``step`` of ``total_steps``."""

    stage: BenchmarkDocumentConversionStage
    step: int = Field(ge=1)
    total_steps: int = Field(ge=1)

    @classmethod
    def of(cls, stage: str) -> "BenchmarkDocumentConversionProgress":
        return cls(stage=stage, step=CONVERSION_STAGES.index(stage) + 1,
                   total_steps=len(CONVERSION_STAGES))


class BenchmarkDocumentConversionStatus(FrozenStrictModel):
    conversion_id: UUID
    status: BenchmarkDocumentConversionState
    # Only while running, once the conversion has recorded its stage.
    progress: BenchmarkDocumentConversionProgress | None
    error: BenchmarkDocumentConversionFailure | None
    snapshot: FrozenBenchmarkInputSnapshot | None
    conversion_identity: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None


__all__ = [
    "BenchmarkDocumentConversionSourceReferenceRequest",
    "BenchmarkDocumentConversionAccepted",
    "BenchmarkDocumentConversionFailure",
    "BenchmarkDocumentConversionProgress",
    "BenchmarkDocumentConversionStage",
    "BenchmarkDocumentConversionState",
    "BenchmarkDocumentConversionStatus",
]
