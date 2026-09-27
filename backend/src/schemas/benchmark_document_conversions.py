"""Public contract for converting papers into frozen benchmark inputs."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from src.lib.benchmarks.models import FrozenStrictModel
from src.lib.benchmarks.snapshots import FrozenBenchmarkInputSnapshot

BenchmarkDocumentConversionState = Literal["queued", "running", "succeeded", "failed"]


class BenchmarkDocumentConversionAbcRequest(FrozenStrictModel):
    """JSON body naming one Alliance literature (ABC) paper by its curie."""

    abc_reference: str = Field(pattern=r"^AGRKB:[0-9]+$", max_length=64)


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
    "BenchmarkDocumentConversionAbcRequest",
    "BenchmarkDocumentConversionAccepted",
    "BenchmarkDocumentConversionFailure",
    "BenchmarkDocumentConversionState",
    "BenchmarkDocumentConversionStatus",
]
