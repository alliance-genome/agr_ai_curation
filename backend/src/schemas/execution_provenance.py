"""Server-captured extraction context, not a model-authored benchmark binding."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceDocumentProvenance(BaseModel):
    """Identity of the primary source artifact; sidecars are not included."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID
    provider: str | None = None
    reference_curie: str | None = None
    converted_artifact_id: str | None = None
    converted_artifact_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def require_artifact_identity(self) -> "SourceDocumentProvenance":
        if self.converted_artifact_sha256 is not None and not (
            self.provider and self.converted_artifact_id
        ):
            raise ValueError("A converted-artifact digest requires provider and artifact identity")
        return self


class ExtractionExecutionContext(BaseModel):
    """What the runtime supplied, without inferring a benchmark task definition.

    The step query is not a complete system-prompt/provider configuration capture.
    Unspecified structured scope remains unknown, including for historical output.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["extraction-execution-context/v1"] = (
        "extraction-execution-context/v1"
    )
    captured_at: datetime
    source_kind: Literal["flow", "chat"]
    flow_id: str | None = Field(default=None, min_length=1)
    step_id: str | None = Field(default=None, min_length=1)
    agent_key: str = Field(min_length=1)
    executed_query: str = Field(min_length=1)
    document: SourceDocumentProvenance | None

    @model_validator(mode="after")
    def require_source_identity(self) -> "ExtractionExecutionContext":
        if self.source_kind == "flow" and (self.flow_id is None or self.step_id is None):
            raise ValueError("Flow execution requires flow and step identity")
        if self.source_kind == "chat" and self.flow_id is not None:
            raise ValueError("Chat execution cannot claim flow identity")
        return self

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Execution capture time requires a timezone")
        return value
