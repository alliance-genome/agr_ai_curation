"""Structured input/output contracts for the curation prep agent.

These models define the handoff between upstream conversations/extraction
results and the agent that proposes normalized curation candidates. The output
contract is intended for OpenAI structured-output enforcement, so arbitrary
JSON payloads are represented using closed helper models that can be
deterministically reconstructed into adapter-owned JSONB payloads downstream.
"""

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
    CurationExtractionResultRecord,
    EvidenceAnchor,
)


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CurationPrepBaseModel(BaseModel):
    """Base model with strict object schemas for structured-output use."""

    model_config = ConfigDict(extra='forbid')

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict[str, Any]:
        """Generate a structured-output-friendly JSON schema."""

        schema = super().model_json_schema(mode='serialization', **kwargs)
        return cls._normalize_structured_output_schema(schema)

    @classmethod
    def _normalize_structured_output_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        """Apply strict-object rules for OpenAI structured outputs."""

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


def _parse_strict_json(value: str, *, field_name: str) -> Any:
    """Parse strict JSON while rejecting NaN and Infinity values."""

    try:
        parsed = json.loads(
            value,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"{field_name} must not contain non-finite numbers")
            ),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field_name} must contain valid JSON") from exc

    # Re-serialize to ensure the parsed shape remains JSON-safe.
    json.dumps(parsed, allow_nan=False)
    return parsed


def _validate_field_path(path: str, *, field_name: str) -> str:
    """Require dot-delimited paths to avoid empty or padded segments."""

    segments = path.split(".")
    if any(not segment or segment != segment.strip() for segment in segments):
        raise ValueError(
            f"{field_name} must use dot-delimited segments without empty or padded path components"
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


class CurationPrepConversationRole(str, Enum):
    """Supported message roles carried into the prep agent prompt."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class CurationPrepConversationMessage(CurationPrepBaseModel):
    """Flattened conversation message provided to the prep agent."""

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
    """Reusable evidence record provided to the prep agent."""

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
        description="Candidate field paths this evidence may support",
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
    profile_keys: list[NonEmptyString] = Field(
        default_factory=list,
        description="Optional adapter profiles or subdomains in scope",
    )
    domain_keys: list[NonEmptyString] = Field(
        default_factory=list,
        description="Domain identifiers or categories confirmed for this run",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Additional scope confirmation notes",
    )

    @model_validator(mode="after")
    def validate_confirmed_scope(self) -> "CurationPrepScopeConfirmation":
        """Require at least one scoped target when confirmation is true."""

        if self.confirmed and not (self.adapter_keys or self.profile_keys or self.domain_keys):
            raise ValueError("Confirmed scope must include at least one adapter, profile, or domain")

        return self


class CurationPrepAdapterFieldHint(CurationPrepBaseModel):
    """Adapter-owned field requirement and vocabulary guidance."""

    field_key: NonEmptyString = Field(description="Adapter-owned normalized field key")
    required: bool = Field(description="Whether the field must be filled for this adapter")
    label: NonEmptyString | None = Field(
        default=None,
        description="Human-friendly field label",
    )
    value_type: NonEmptyString | None = Field(
        default=None,
        description="Expected adapter-owned value type or shape hint",
    )
    description: str | None = Field(
        default=None,
        description="Additional adapter guidance for this field",
    )
    controlled_vocabulary: list[NonEmptyString] = Field(
        default_factory=list,
        description="Controlled-vocabulary hints or preferred normalized values",
    )
    normalization_hints: list[str] = Field(
        default_factory=list,
        description="Free-text normalization notes for this field",
    )


class CurationPrepAdapterMetadata(CurationPrepBaseModel):
    """Adapter metadata the prep agent uses to shape candidates."""

    adapter_key: NonEmptyString = Field(description="Adapter key the metadata applies to")
    profile_key: NonEmptyString | None = Field(
        default=None,
        description="Optional profile or subdomain key",
    )
    required_field_keys: list[NonEmptyString] = Field(
        default_factory=list,
        description="Required field keys the agent should attempt to populate",
    )
    field_hints: list[CurationPrepAdapterFieldHint] = Field(
        default_factory=list,
        description="Field-level requirement, type, and vocabulary hints",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Additional adapter-specific instructions",
    )


class CurationPrepExtractedFieldValueType(str, Enum):
    """Strict-schema-safe representation of adapter-owned field values."""

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    NULL = "null"
    JSON = "json"


class CurationPrepExtractedField(CurationPrepBaseModel):
    """Single extracted field entry that can be rehydrated into JSONB."""

    field_path: NonEmptyString = Field(
        description="Dot-delimited path inside the adapter-owned normalized payload",
    )
    value_type: CurationPrepExtractedFieldValueType = Field(
        description="Value carrier used for this field",
    )
    string_value: str | None = Field(
        default=None,
        description="String value when value_type is string",
    )
    number_value: float | None = Field(
        default=None,
        description="Number value when value_type is number",
    )
    boolean_value: bool | None = Field(
        default=None,
        description="Boolean value when value_type is boolean",
    )
    json_value: str | None = Field(
        default=None,
        description="Serialized JSON object or array when value_type is json",
    )

    @field_validator("field_path")
    @classmethod
    def validate_field_path(cls, value: str) -> str:
        """Require well-formed adapter-owned field paths."""

        return _validate_field_path(value, field_name="field_path")

    @model_validator(mode="after")
    def validate_value_slot(self) -> "CurationPrepExtractedField":
        """Ensure exactly one compatible value carrier is used."""

        if self.value_type is CurationPrepExtractedFieldValueType.STRING:
            if self.string_value is None:
                raise ValueError("string_value is required when value_type is string")
            if self.number_value is not None or self.boolean_value is not None or self.json_value is not None:
                raise ValueError("Only string_value may be set when value_type is string")
            return self

        if self.value_type is CurationPrepExtractedFieldValueType.NUMBER:
            if self.number_value is None:
                raise ValueError("number_value is required when value_type is number")
            if self.string_value is not None or self.boolean_value is not None or self.json_value is not None:
                raise ValueError("Only number_value may be set when value_type is number")
            return self

        if self.value_type is CurationPrepExtractedFieldValueType.BOOLEAN:
            if self.boolean_value is None:
                raise ValueError("boolean_value is required when value_type is boolean")
            if self.string_value is not None or self.number_value is not None or self.json_value is not None:
                raise ValueError("Only boolean_value may be set when value_type is boolean")
            return self

        if self.value_type is CurationPrepExtractedFieldValueType.NULL:
            if any(
                value is not None
                for value in (
                    self.string_value,
                    self.number_value,
                    self.boolean_value,
                    self.json_value,
                )
            ):
                raise ValueError("No value slot may be set when value_type is null")
            return self

        if self.json_value is None:
            raise ValueError("json_value is required when value_type is json")
        if self.string_value is not None or self.number_value is not None or self.boolean_value is not None:
            raise ValueError("Only json_value may be set when value_type is json")

        parsed = _parse_strict_json(self.json_value, field_name="json_value")
        if not isinstance(parsed, (dict, list)):
            raise ValueError("json_value must decode to a JSON object or array")

        return self

    def to_python_value(self) -> Any:
        """Convert the strict carrier back to a plain JSON-compatible value."""

        if self.value_type is CurationPrepExtractedFieldValueType.STRING:
            return self.string_value
        if self.value_type is CurationPrepExtractedFieldValueType.NUMBER:
            return self.number_value
        if self.value_type is CurationPrepExtractedFieldValueType.BOOLEAN:
            return self.boolean_value
        if self.value_type is CurationPrepExtractedFieldValueType.NULL:
            return None
        return _parse_strict_json(self.json_value or "", field_name="json_value")


class CurationPrepEvidenceReference(CurationPrepBaseModel):
    """Reference linking an extracted value back to a specific evidence anchor."""

    field_path: NonEmptyString = Field(
        description="Field path inside extracted_fields supported by this evidence",
    )
    evidence_record_id: NonEmptyString = Field(
        description="Identifier from the input evidence_records collection",
    )
    extraction_result_id: NonEmptyString | None = Field(
        default=None,
        description="Extraction result associated with the evidence reference when available",
    )
    anchor: EvidenceAnchor = Field(
        description="Snippet/page/section/figure anchor supporting the extracted value",
    )
    rationale: str | None = Field(
        default=None,
        description="Optional short explanation for why the evidence supports the field value",
    )

    @field_validator("field_path")
    @classmethod
    def validate_field_path(cls, value: str) -> str:
        """Require well-formed field paths for evidence references."""

        return _validate_field_path(value, field_name="field_path")


class CurationPrepAmbiguity(CurationPrepBaseModel):
    """Open ambiguity the prep agent could not resolve confidently."""

    field_path: NonEmptyString = Field(description="Field path affected by the ambiguity")
    description: NonEmptyString = Field(description="Why the ambiguity remains unresolved")
    candidate_values: list[str] = Field(
        default_factory=list,
        description="Alternative values the agent considered",
    )
    evidence_record_ids: list[NonEmptyString] = Field(
        default_factory=list,
        description="Evidence record identifiers relevant to the ambiguity",
    )

    @field_validator("field_path")
    @classmethod
    def validate_field_path(cls, value: str) -> str:
        """Require well-formed field paths for unresolved ambiguities."""

        return _validate_field_path(value, field_name="field_path")


class CurationPrepCandidate(CurationPrepBaseModel):
    """Structured candidate emitted by the curation prep agent."""

    adapter_key: NonEmptyString = Field(description="Adapter key this candidate targets")
    profile_key: NonEmptyString | None = Field(
        default=None,
        description="Optional adapter profile or subdomain key",
    )
    extracted_fields: list[CurationPrepExtractedField] = Field(
        min_length=1,
        description=(
            "Strict-schema-safe field entries that reconstruct into the adapter-owned "
            "normalized JSONB payload"
        ),
    )
    evidence_references: list[CurationPrepEvidenceReference] = Field(
        default_factory=list,
        description="Evidence anchors supporting extracted field values",
    )
    conversation_context_summary: NonEmptyString = Field(
        description="Condensed explanation of how the candidate was derived from conversation context",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Overall candidate confidence score from 0.0 to 1.0",
    )
    unresolved_ambiguities: list[CurationPrepAmbiguity] = Field(
        default_factory=list,
        description="Ambiguities the agent could not confidently resolve",
    )

    @model_validator(mode="after")
    def validate_candidate_shape(self) -> "CurationPrepCandidate":
        """Require evidence and a reconstructable extracted-field payload."""

        if not self.evidence_references:
            raise ValueError("evidence_references must contain at least one reference")

        extracted_payload = self.to_extracted_fields_dict()
        for evidence_reference in self.evidence_references:
            if not _path_exists_in_payload(extracted_payload, evidence_reference.field_path):
                raise ValueError(
                    "evidence_references.field_path must resolve to an extracted field value"
                )

        return self

    def to_extracted_fields_dict(self) -> dict[str, Any]:
        """Rehydrate extracted field entries into an adapter-owned JSONB dict."""

        result: dict[str, Any] = {}

        for field in self.extracted_fields:
            path_segments = str(field.field_path).split(".")
            cursor = result

            for segment in path_segments[:-1]:
                existing = cursor.get(segment)
                if existing is None:
                    cursor[segment] = {}
                    existing = cursor[segment]
                elif not isinstance(existing, dict):
                    raise ValueError(
                        f"Field path '{field.field_path}' conflicts with an existing non-object value"
                    )
                cursor = existing

            leaf_key = path_segments[-1]
            if leaf_key in cursor:
                raise ValueError(f"Duplicate extracted field path '{field.field_path}' is not allowed")
            cursor[leaf_key] = field.to_python_value()

        if not result:
            raise ValueError("extracted_fields must contain at least one field")

        return result


class CurationPrepTokenUsage(CurationPrepBaseModel):
    """Run-level token accounting for prep structured output."""

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


class CurationPrepAgentInput(CurationPrepBaseModel):
    """Structured input consumed by the curation prep agent."""

    conversation_history: list[CurationPrepConversationMessage] = Field(
        default_factory=list,
        description="Conversation messages from the chat or flow session",
    )
    extraction_results: list[CurationExtractionResultRecord] = Field(
        min_length=1,
        description="Persisted extraction envelopes in scope for this prep run",
    )
    evidence_records: list[CurationPrepEvidenceRecord] = Field(
        default_factory=list,
        description="Evidence anchors and snippets available to support candidate extraction",
    )
    scope_confirmation: CurationPrepScopeConfirmation = Field(
        description="Confirmed scope describing which adapters or domains are in play",
    )
    adapter_metadata: list[CurationPrepAdapterMetadata] = Field(
        min_length=1,
        description="Adapter metadata including required fields and vocabulary hints",
    )


class CurationPrepAgentOutput(CurationPrepBaseModel):
    """Structured output emitted by the curation prep agent."""

    candidates: list[CurationPrepCandidate] = Field(
        default_factory=list,
        description="Candidates prepared for deterministic normalization and review-session creation",
    )
    run_metadata: CurationPrepRunMetadata = Field(
        description="Run-level metadata associated with candidate generation",
    )


__all__ = [
    "CurationPrepAdapterFieldHint",
    "CurationPrepAdapterMetadata",
    "CurationPrepAgentInput",
    "CurationPrepAgentOutput",
    "CurationPrepAmbiguity",
    "CurationPrepBaseModel",
    "CurationPrepCandidate",
    "CurationPrepConversationMessage",
    "CurationPrepConversationRole",
    "CurationPrepEvidenceRecord",
    "CurationPrepEvidenceReference",
    "CurationPrepExtractedField",
    "CurationPrepExtractedFieldValueType",
    "CurationPrepRunMetadata",
    "CurationPrepScopeConfirmation",
    "CurationPrepTokenUsage",
]
