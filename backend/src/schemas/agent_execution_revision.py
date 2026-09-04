"""Explicit output contracts and complete immutable custom-agent configurations."""

from __future__ import annotations

import hashlib
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.lib.agent_access import (
    normalize_allowed_group_ids,
    require_allowed_group_ids_narrowing,
)
from src.lib.group_tool_policy import parse_group_tool_policy
from src.schemas.generic_extraction_profile import canonical_json


class RevisionContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenericProfilePin(RevisionContractModel):
    profile_id: UUID
    profile_revision_id: UUID
    revision: int = Field(ge=1)
    fingerprint: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class AgentOutputContract(RevisionContractModel):
    """An entire transition value, never a patch with overloaded nulls."""

    output_state: Literal["none", "structured_extraction"]
    output_mode: (
        Literal["domain", "profile_bound_generic", "unprofiled_generic"] | None
    ) = None
    output_schema_key: str | None = None
    generic_profile_ref: GenericProfilePin | None = None

    @model_validator(mode="after")
    def consistent(self) -> AgentOutputContract:
        if self.output_state == "none":
            if any(
                value is not None
                for value in (
                    self.output_mode,
                    self.output_schema_key,
                    self.generic_profile_ref,
                )
            ):
                raise ValueError(
                    "No structured output cannot retain an extraction mode, schema or profile"
                )
        elif self.output_mode == "domain":
            if (
                not self.output_schema_key
                or not self.output_schema_key.strip()
                or self.generic_profile_ref is not None
            ):
                raise ValueError(
                    "Domain output requires exactly one packaged schema and no profile"
                )
            self.output_schema_key = self.output_schema_key.strip()
        elif self.output_mode == "profile_bound_generic":
            if self.generic_profile_ref is None or self.output_schema_key is not None:
                raise ValueError(
                    "Profile-bound output requires an exact profile revision and no packaged schema"
                )
        elif self.output_mode == "unprofiled_generic":
            if (
                self.output_schema_key is not None
                or self.generic_profile_ref is not None
            ):
                raise ValueError(
                    "Unprofiled generic output cannot retain a packaged schema or profile"
                )
        else:
            raise ValueError("Structured extraction requires an explicit output mode")
        return self


class AgentExecutionSnapshot(RevisionContractModel):
    """Saved runtime settings; per-run document context is deliberately absent.

    No field here is resolved from a mutable agent head during pinned execution.
    Active package/tool availability and caller authorization are checked again
    at execution, while tool source code remains deployment-owned.
    """

    snapshot_version: Literal[1] = 1
    model_id: str = Field(min_length=1)
    model_temperature: float
    model_reasoning: str | None
    instructions: str
    instructions_hash: str
    prompt_layer_manifest: dict[str, Any]
    group_prompt_layers: dict[str, dict[str, Any]]
    tool_ids: list[str]
    system_managed_tool_ids: list[str]
    group_tool_policy: dict[str, Any]
    allowed_group_ids: list[str]
    inherited_allowed_group_ids: list[str]
    group_rules_enabled: bool
    group_rules_component: str | None
    group_prompt_overrides: dict[str, str]
    template_source: str | None
    output_contract: AgentOutputContract
    curation: dict[str, Any] | None
    structured_finalization: dict[str, Any] | None

    @model_validator(mode="after")
    def integrity(self) -> AgentExecutionSnapshot:
        from src.lib.prompts.assembly import prompt_bundle_from_manifest

        expected = (
            "sha256:" + hashlib.sha256(self.instructions.encode("utf-8")).hexdigest()
        )
        if self.instructions_hash != expected:
            raise ValueError("Saved instructions hash mismatch")
        bundle = prompt_bundle_from_manifest(self.prompt_layer_manifest)
        main_layers = [layer for layer in bundle.layers if layer.kind == "base_prompt"]
        if len(main_layers) != 1 or main_layers[0].content != self.instructions:
            raise ValueError(
                "Saved prompt manifest must contain the exact custom instructions"
            )
        if any(
            layer.kind in ("runtime_context", "group_rules") for layer in bundle.layers
        ):
            raise ValueError(
                "Per-run context and group rules do not belong in the saved base bundle"
            )
        for group_id, manifest in self.group_prompt_layers.items():
            if group_id != group_id.strip().upper():
                raise ValueError("Saved group prompt keys must be canonical")
            group_bundle = prompt_bundle_from_manifest(manifest)
            if group_bundle.agent_id != bundle.agent_id or any(
                layer.kind != "group_rules" for layer in group_bundle.layers
            ):
                raise ValueError(
                    "Saved group bundles may contain only their group rules"
                )
        self.allowed_group_ids = normalize_allowed_group_ids(self.allowed_group_ids)
        self.inherited_allowed_group_ids = normalize_allowed_group_ids(
            self.inherited_allowed_group_ids,
            field_name="inherited_allowed_group_ids",
        )
        require_allowed_group_ids_narrowing(
            self.inherited_allowed_group_ids,
            self.allowed_group_ids,
            source_name="saved inherited access floor",
        )
        self.group_tool_policy = parse_group_tool_policy(
            self.group_tool_policy
        ).to_dict()
        if len(set(self.tool_ids)) != len(self.tool_ids) or any(
            not item.strip() for item in self.tool_ids
        ):
            raise ValueError("Saved tool IDs must be non-empty and unique")
        if not set(self.system_managed_tool_ids) <= set(self.tool_ids):
            raise ValueError("Saved system-managed tools must be included in tool_ids")
        return self

    def fingerprint(self) -> str:
        return (
            "sha256:"
            + hashlib.sha256(
                canonical_json(self.model_dump(mode="json")).encode("utf-8")
            ).hexdigest()
        )


def initial_output_contract(output_schema_key: str | None) -> AgentOutputContract:
    """Truthful current-head baseline: a missing packaged schema means none.

    This is used for baseline creation only, never to reinterpret an existing
    explicit profile/unprofiled revision.
    """
    if output_schema_key and output_schema_key.strip():
        return AgentOutputContract(
            output_state="structured_extraction",
            output_mode="domain",
            output_schema_key=output_schema_key,
        )
    return AgentOutputContract(output_state="none")
