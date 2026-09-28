"""Public contract for converting papers into frozen benchmark inputs."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from src.lib.benchmarks.document_conversions import (
    MAX_SOURCE_REFERENCE_LENGTH,
    SOURCE_REFERENCE_PATTERN,
)
from src.lib.benchmarks.models import FrozenStrictModel
from src.lib.benchmarks.snapshots import FrozenBenchmarkInputSnapshot

BenchmarkDocumentConversionState = Literal["queued", "running", "succeeded", "failed"]


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


class BenchmarkDocumentConversionStatus(FrozenStrictModel):
    conversion_id: UUID
    status: BenchmarkDocumentConversionState
    error: BenchmarkDocumentConversionFailure | None
    snapshot: FrozenBenchmarkInputSnapshot | None
    conversion_identity: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None


__all__ = [
    "BenchmarkDocumentConversionSourceReferenceRequest",
    "BenchmarkDocumentConversionAccepted",
    "BenchmarkDocumentConversionFailure",
    "BenchmarkDocumentConversionState",
    "BenchmarkDocumentConversionStatus",
]
