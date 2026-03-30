"""Shared schemas for curation prep preview, output, and replay contracts."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .curation_workspace import (
    CurationEvidenceSource,
    EvidenceAnchor,
)


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CurationPrepBaseModel(BaseModel):
    """Base model with strict object schemas for prep payloads."""

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict[str, Any]:
        """Generate a strict-object-friendly JSON schema."""

        schema = super().model_json_schema(mode="serialization", **kwargs)
        return cls._normalize_structured_output_schema(schema)

    @classmethod
    def _normalize_structured_output_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        """Apply strict-object rules recursively."""

        if not isinstance(schema, dict):
            return schema

        if schema.get("type") == "object":
            if "properties" in schema:
                property_names = list(schema["properties"].keys())
                if property_names:
                    schema["required"] = property_names
                schema["additionalProperties"] = False

                for property_name, property_schema in list(schema["properties"].items()):
                    if isinstance(property_schema, dict) and "$ref" in property_schema:
                        schema["properties"][property_name] = {"$ref": property_schema["$ref"]}
                    elif isinstance(property_schema, dict):
                        schema["properties"][property_name] = cls._normalize_structured_output_schema(
                            property_schema
                        )
            elif "additionalProperties" not in schema:
                schema["additionalProperties"] = False

        if "$defs" in schema:
            for definition_name, definition_schema in list(schema["$defs"].items()):
                if isinstance(definition_schema, dict):
                    schema["$defs"][definition_name] = cls._normalize_structured_output_schema(
                        definition_schema
                    )

        if "items" in schema and isinstance(schema["items"], dict):
            schema["items"] = cls._normalize_structured_output_schema(schema["items"])

        for key, value in list(schema.items()):
            if key in {"properties", "$defs", "items"}:
                continue
            if isinstance(value, dict):
                schema[key] = cls._normalize_structured_output_schema(value)
            elif isinstance(value, list):
                schema[key] = [
                    cls._normalize_structured_output_schema(item) if isinstance(item, dict) else item
                    for item in value
                ]

        return schema


def _validate_field_path(
    path: str,
    *,
    field_name: str,
    allow_numeric_segments: bool = True,
) -> str:
    """Require dot-delimited paths to avoid empty or padded segments."""

    segments = path.split(".")
    if any(not segment or segment != segment.strip() for segment in segments):
        raise ValueError(
            f"{field_name} must use dot-delimited segments without empty or padded path components"
        )
    if segments[0].isdigit():
        raise ValueError(f"{field_name} must start with a named top-level field")
    if not allow_numeric_segments and any(segment.isdigit() for segment in segments):
        raise ValueError(
            f"{field_name} must not use numeric path segments; list elements should be nested"
        )
    return path


def _path_exists_in_payload(payload: Any, field_path: str) -> bool:
    """Return whether a dot-delimited field path resolves inside a JSON payload."""

    current = payload
    for segment in field_path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                return False
            current = current[segment]
            continue

        if isinstance(current, list):
            if not segment.isdigit():
                return False
            index = int(segment)
            if index < 0 or index >= len(current):
                return False
            current = current[index]
            continue

        return False

    return True


def _ensure_json_compatible(value: Any, *, field_name: str) -> Any:
    """Require a payload to remain JSON-safe."""

    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must contain only JSON-compatible values") from exc
    return value


class CurationPrepConversationRole(str, Enum):
    """Supported message roles carried into prep context summaries."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class CurationPrepConversationMessage(CurationPrepBaseModel):
    """Flattened conversation message retained for prep-adjacent flows."""

    role: CurationPrepConversationRole = Field(description="Conversation role")
    content: NonEmptyString = Field(description="Plain-text message content")
    message_id: NonEmptyString | None = Field(
        default=None,
        description="Stable message identifier when available",
    )
    created_at: datetime | None = Field(
        default=None,
        description="Message timestamp when available",
    )


class CurationPrepEvidenceRecord(CurationPrepBaseModel):
    """Evidence record carried directly on a prep candidate."""

    evidence_record_id: NonEmptyString = Field(description="Stable evidence record identifier")
    source: CurationEvidenceSource = Field(
        default=CurationEvidenceSource.EXTRACTED,
        description="How the evidence record entered prep context",
    )
    extraction_result_id: NonEmptyString | None = Field(
        default=None,
        description="Extraction result that surfaced this evidence record when available",
    )
    field_paths: list[NonEmptyString] = Field(
        default_factory=list,
        description="Candidate payload field paths this evidence supports",
    )
    anchor: EvidenceAnchor = Field(
        description="Resolved snippet/page/section/figure anchor for this evidence record",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Non-blocking evidence notes or caveats",
    )

    @field_validator("field_paths")
    @classmethod
    def validate_field_paths(cls, value: list[str]) -> list[str]:
        """Require well-formed field paths for evidence-to-field mapping."""

        for field_path in value:
            _validate_field_path(field_path, field_name="field_paths")
        return value


class CurationPrepScopeConfirmation(CurationPrepBaseModel):
    """Confirmed scoping context for the prep run."""

    confirmed: bool = Field(
        description="Whether the user or flow has confirmed the prep scope",
    )
    adapter_keys: list[NonEmptyString] = Field(
        default_factory=list,
        description="Adapters in scope for this prep run",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Additional scope confirmation notes",
    )

    @model_validator(mode="after")
    def validate_confirmed_scope(self) -> "CurationPrepScopeConfirmation":
        """Require at least one scoped target when confirmation is true."""

        if self.confirmed and not self.adapter_keys:
            raise ValueError("Confirmed scope must include at least one adapter")

        return self


class CurationPrepCandidate(CurationPrepBaseModel):
    """Structured candidate prepared for deterministic normalization."""

    adapter_key: NonEmptyString = Field(description="Adapter key this candidate targets")
    payload: dict[str, Any] = Field(
        description="Adapter-owned candidate payload carried directly as JSON",
    )
    evidence_records: list[CurationPrepEvidenceRecord] = Field(
        default_factory=list,
        description="Evidence anchors supporting the candidate payload",
    )
    conversation_context_summary: NonEmptyString = Field(
        description="Condensed explanation of how the candidate was derived",
    )

    @field_validator("payload")
    @classmethod
    def validate_payload_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Require non-empty JSON-compatible candidate payloads."""

        if not value:
            raise ValueError("payload must contain at least one field")
        _ensure_json_compatible(value, field_name="payload")
        return value

    @model_validator(mode="after")
    def validate_candidate_shape(self) -> "CurationPrepCandidate":
        """Require evidence records to resolve against the candidate payload."""

        if not self.evidence_records:
            raise ValueError("evidence_records must contain at least one record")

        for evidence_record in self.evidence_records:
            for field_path in evidence_record.field_paths:
                if not _path_exists_in_payload(self.payload, field_path):
                    raise ValueError(
                        "evidence_records.field_paths must resolve to payload field values"
                    )

        return self


class CurationPrepTokenUsage(CurationPrepBaseModel):
    """Run-level token accounting for prep output."""

    input_tokens: int = Field(default=0, ge=0, description="Prompt/input token count")
    output_tokens: int = Field(default=0, ge=0, description="Completion/output token count")
    total_tokens: int = Field(default=0, ge=0, description="Total token count reported for the run")

    @model_validator(mode="after")
    def validate_total_tokens(self) -> "CurationPrepTokenUsage":
        """Keep token totals internally consistent."""

        if self.total_tokens < (self.input_tokens + self.output_tokens):
            raise ValueError("total_tokens must be greater than or equal to input_tokens + output_tokens")

        return self


class CurationPrepRunMetadata(CurationPrepBaseModel):
    """Run-level metadata emitted with prep output."""

    model_name: NonEmptyString = Field(description="Model identifier used for the prep run")
    token_usage: CurationPrepTokenUsage = Field(
        default_factory=CurationPrepTokenUsage,
        description="Token usage reported for the run",
    )
    processing_notes: list[str] = Field(
        default_factory=list,
        description="Run-level processing notes or caveats",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Run-level warnings that downstream consumers may surface",
    )


class CurationPrepAgentOutput(CurationPrepBaseModel):
    """Structured output emitted by curation prep."""

    candidates: list[CurationPrepCandidate] = Field(
        default_factory=list,
        description="Candidates prepared for deterministic normalization and review-session creation",
    )
    run_metadata: CurationPrepRunMetadata = Field(
        description="Run-level metadata associated with candidate generation",
    )


class CurationPrepChatPreviewResponse(CurationPrepBaseModel):
    """Curator-facing summary shown before prep is invoked."""

    ready: bool = Field(description="Whether the current chat context can be prepared")
    summary_text: NonEmptyString = Field(description="Confirmation text shown in the dialog")
    candidate_count: int = Field(
        default=0,
        ge=0,
        description="Total candidate annotations discussed in the current chat context",
    )
    extraction_result_count: int = Field(
        default=0,
        ge=0,
        description="Number of persisted extraction envelopes discovered for the session",
    )
    conversation_message_count: int = Field(
        default=0,
        ge=0,
        description="Flattened user/assistant message count in the current chat history",
    )
    adapter_keys: list[NonEmptyString] = Field(
        default_factory=list,
        description="Adapters discovered from persisted extraction results",
    )
    blocking_reasons: list[str] = Field(
        default_factory=list,
        description="Reasons the prep run cannot start yet",
    )


class CurationPrepChatRunRequest(CurationPrepBaseModel):
    """Confirmed prep request submitted from the chat UI."""

    session_id: NonEmptyString = Field(description="Current chat session identifier")
    adapter_keys: list[NonEmptyString] = Field(
        default_factory=list,
        description="Confirmed adapters to include in the prep run",
    )


class CurationPrepChatRunResponse(CurationPrepBaseModel):
    """Result summary returned after prep finishes."""

    summary_text: NonEmptyString = Field(description="User-facing completion summary")
    document_id: NonEmptyString = Field(
        description="Resolved document identifier for the prepared review candidates",
    )
    candidate_count: int = Field(
        default=0,
        ge=0,
        description="Number of candidates produced by prep",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings surfaced from the prep run metadata",
    )
    processing_notes: list[str] = Field(
        default_factory=list,
        description="Concise processing notes from the prep run metadata",
    )
    adapter_keys: list[NonEmptyString] = Field(
        default_factory=list,
        description="Adapters that were in scope for the confirmed prep run",
    )


__all__ = [
    "CurationPrepAgentOutput",
    "CurationPrepBaseModel",
    "CurationPrepCandidate",
    "CurationPrepChatPreviewResponse",
    "CurationPrepChatRunRequest",
    "CurationPrepChatRunResponse",
    "CurationPrepConversationMessage",
    "CurationPrepConversationRole",
    "CurationPrepEvidenceRecord",
    "CurationPrepRunMetadata",
    "CurationPrepScopeConfirmation",
    "CurationPrepTokenUsage",
]
