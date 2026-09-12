"""Immutable, explicit custom-profile connections to package-owned bindings."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MappingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ValidatorCapabilityRef(MappingModel):
    package_id: str = Field(min_length=1)
    package_version: str = Field(min_length=1)
    domain_pack_id: str = Field(min_length=1)
    domain_pack_version: str = Field(min_length=1)
    binding_id: str = Field(min_length=1)


class ProfileMappingInput(MappingModel):
    # A context input selects the package-declared selector, never a user path.
    source: Literal["field", "constant", "context"] = "field"
    field_path: str | None = None
    value: Any = None

    @model_validator(mode="after")
    def source_shape(self):
        supplied = self.model_fields_set
        if self.source == "field":
            if not self.field_path or self.value is not None:
                raise ValueError("Field input requires only a canonical field_path")
        elif self.source == "constant":
            if "value" not in supplied or self.field_path is not None:
                raise ValueError("Constant input requires only value")
        elif self.field_path is not None or self.value is not None:
            raise ValueError("Context input uses only the package-owned selector")
        return self


class ProfileMappingPolicy(MappingModel):
    unresolved: Literal["informational", "requires_curator_review", "error"]
    blocks_readiness: bool


class ProfileValidatorMapping(MappingModel):
    mapping_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    capability_ref: ValidatorCapabilityRef
    capability_fingerprint: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    inputs: dict[str, ProfileMappingInput]
    outputs: dict[str, str]
    policy: ProfileMappingPolicy
    # [] paths are explicit per-element mappings; no implicit array coercion.
    mode: Literal["whole", "per_element"] = "whole"
