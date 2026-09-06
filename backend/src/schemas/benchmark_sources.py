"""Metadata for raw canonical-byte benchmark uploads (never source authority)."""

from typing import Literal

from pydantic import Field

from src.lib.benchmarks.models import FrozenStrictModel

FrozenDocumentContentType = Literal[
    "text/plain", "text/markdown", "application/json", "application/xml",
]


class BenchmarkSnapshotUploadMetadata(FrozenStrictModel):
    content_type: FrozenDocumentContentType
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
