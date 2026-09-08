"""Metadata for raw canonical-byte benchmark uploads (never source authority)."""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from src.lib.benchmarks.models import BenchmarkOpaqueReference, FrozenStrictModel
from src.lib.openai_agents.config import (
    get_benchmark_source_discovery_max_choices,
    get_benchmark_source_selection_max_bytes,
)

FrozenDocumentContentType = Literal[
    "text/plain", "text/markdown", "application/json", "application/xml",
]


class BenchmarkSnapshotUploadMetadata(FrozenStrictModel):
    content_type: FrozenDocumentContentType
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class _SourceSelectionModel(FrozenStrictModel):
    @field_validator("*", check_fields=False)
    @classmethod
    def bound_text(cls, value):
        if isinstance(value, str) and (
            not value.strip()
            or len(value.encode("utf-8")) > get_benchmark_source_selection_max_bytes()
        ):
            raise ValueError("Source selection text is empty or exceeds its limit")
        return value


class BenchmarkSourcePreparationRequest(_SourceSelectionModel):
    resolver: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    reference: BenchmarkOpaqueReference


class BenchmarkSourceDiscoveryRequest(_SourceSelectionModel):
    resolver: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    query: str | None = None
    locator: str | None = None
    cursor: str | None = None

    @model_validator(mode="after")
    def one_selection(self):
        if (self.query is None) == (self.locator is None):
            raise ValueError("Select a search query or a paper locator")
        return self


class BenchmarkSourcePaperChoice(_SourceSelectionModel):
    kind: Literal["paper"] = "paper"
    label: str
    locator: str


class BenchmarkSourceArtifactChoice(_SourceSelectionModel):
    kind: Literal["artifact"] = "artifact"
    label: str
    reference: BenchmarkOpaqueReference | None = None
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def preparation_state(self):
        if (self.reference is None) == (self.unavailable_reason is None):
            raise ValueError("Artifact must be preparable or explain unavailability")
        return self


class BenchmarkSourceDiscoveryPage(_SourceSelectionModel):
    choices: tuple[Annotated[
        BenchmarkSourcePaperChoice | BenchmarkSourceArtifactChoice,
        Field(discriminator="kind"),
    ], ...]
    next_cursor: str | None = None

    @field_validator("choices")
    @classmethod
    def bound_choices(cls, value):
        if len(value) > get_benchmark_source_discovery_max_choices():
            raise ValueError("Source discovery page exceeds its limit")
        return value
