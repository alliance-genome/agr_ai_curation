"""Read declared output structures without running an agent or inspecting results.

This is discovery, not a biological mapping or an executable flow snapshot.
Custom contracts are read only after reauthorizing their exact saved receipt.
"""

from collections.abc import Mapping
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy.orm import Session

from src.lib.agent_studio.execution_revision_service import authorize_execution_receipt
from src.lib.config.schema_discovery import resolve_output_schema
from src.lib.curation_workspace.execution_contracts import resolve_receipt_profile
from src.lib.flows.export_fields import packaged_domain_pack
from src.lib.flows.formatter_capability import resolved_formatter_format
from src.schemas.agent_execution_revision import AgentExecutionReceipt
from src.schemas.domain_validator import is_domain_validator_result_schema

from .execution_context import BenchmarkCuratorContext
from .models import FrozenStrictModel
from .pack_catalog import benchmark_pack_catalog
from .suites import _digest

OutputKind = Literal[
    "pack_fields", "profile_attributes", "flexible", "pdf_extraction", "envelope_legacy",
    "validator_result", "formatter", "text", "other", "unavailable",
]


class PackStructureSource(FrozenStrictModel):
    kind: Literal["pack"] = "pack"
    pack_id: str
    pack_version: str
    pack_label: str


class ProfileStructureSource(FrozenStrictModel):
    kind: Literal["profile"] = "profile"
    profile_id: UUID
    profile_revision_id: UUID
    revision: int
    name: str


StructureSource = Annotated[PackStructureSource | ProfileStructureSource,
                            Field(discriminator="kind")]


class BenchmarkFlowOutputContract(FrozenStrictModel):
    """A declared schema, with its representation and source identity explicit."""

    status: Literal["verified", "not_verified"]
    representation: Literal["json_schema", "profile_attributes", "pack_fields"] | None = None
    schema_definition: dict[str, Any] | None = None
    schema_digest: str | None = None
    execution_receipt: AgentExecutionReceipt | None = None
    declared_semantic_class: str | None = None
    domain_pack_id: str | None = None
    structure_source: StructureSource | None = None
    reason: str | None = None
    # A structured output contract does not prove gene/allele suitability.
    semantic_mapping_required: bool = True


def _pack_catalog(
    agent_id: str, entry: dict[str, Any],
) -> tuple[dict[str, Any], PackStructureSource] | None:
    domain_pack = packaged_domain_pack(agent_id, entry)
    if domain_pack is None:
        return None
    catalog = benchmark_pack_catalog(domain_pack)
    return catalog, PackStructureSource(
        pack_id=catalog["pack_id"], pack_version=catalog["pack_version"],
        pack_label=catalog["pack_label"],
    )


def discover_output_contract(
    session: Session,
    curator: BenchmarkCuratorContext,
    *,
    agent_id: str,
    metadata: dict[str, Any],
) -> BenchmarkFlowOutputContract:
    """Describe an authorized node's actual declared output, never its label.

    System metadata must come from the current visible-agent service. Custom
    metadata is not trusted for schemas: authorize its receipt again here so
    future chat/discovery consumers cannot bypass the revision access boundary.
    """
    receipt = None
    schema = None
    representation = None
    semantic_class = None
    pack_id = None
    source: PackStructureSource | ProfileStructureSource | None = None
    if agent_id.startswith("ca_"):
        receipt = authorize_execution_receipt(
            session, metadata.get("execution_receipt"), curator.db_user_id,
            active_group_ids=list(curator.active_groups),
        )
        if receipt.agent_key != agent_id:
            raise ValueError("Output contract belongs to another agent")
        contract = receipt.output_contract
        if contract.output_state != "structured_extraction":
            return BenchmarkFlowOutputContract(
                status="not_verified", execution_receipt=receipt,
                reason="This saved agent revision does not declare structured extraction.",
            )
        if contract.generic_profile_ref is not None:
            profile = resolve_receipt_profile(session, receipt)
            if profile is None:
                raise ValueError("Saved output profile is unavailable")
            schema = profile.attributes_schema()
            semantic_class = profile.contract.semantic_class
            pin = contract.generic_profile_ref
            source = ProfileStructureSource(
                profile_id=pin.profile_id, profile_revision_id=pin.profile_revision_id,
                revision=pin.revision, name=profile.contract.name,
            )
            representation = "profile_attributes"
        elif contract.output_schema_key:
            model = resolve_output_schema(contract.output_schema_key)
            if model is not None:
                schema = model.model_json_schema()
                representation = "json_schema"
        elif contract.domain_extraction_ref is not None:
            # Resolve declared package fields from the authorized receipt, not
            # caller-supplied curation metadata or names such as gene_a/gene_b.
            pack_id = contract.domain_extraction_ref.domain_pack_id
            found = _pack_catalog(agent_id, {"curation": {"domain_pack_id": pack_id}})
            if found is not None:
                schema, source = found
                representation = "pack_fields"
    else:
        schema_key = metadata.get("output_schema_key")
        if schema_key:
            model = resolve_output_schema(schema_key)
            if model is not None:
                schema = model.model_json_schema()
                representation = "json_schema"
        else:
            found = _pack_catalog(agent_id, metadata)
            if found is not None:
                pack_id = (metadata.get("curation") or {}).get("domain_pack_id")
                schema, source = found
                representation = "pack_fields"
    if schema is None:
        return BenchmarkFlowOutputContract(
            status="not_verified", execution_receipt=receipt,
            reason="No declared output structure is available. Define one in AI Curation before mapping fields.",
        )
    return BenchmarkFlowOutputContract(
        status="verified", representation=representation,
        schema_definition=schema, schema_digest=_digest(schema),
        execution_receipt=receipt,
        declared_semantic_class=semantic_class, domain_pack_id=pack_id,
        structure_source=source,
    )


def _validator_schema(schema_key: str | None) -> bool:
    return bool(schema_key) and is_domain_validator_result_schema(resolve_output_schema(schema_key))


def step_output_kind(
    agent_id: str, metadata: Mapping[str, Any], contract: BenchmarkFlowOutputContract,
) -> OutputKind:
    """What a step's output is, from its authorized receipt or system metadata, never reasons."""
    # Saved copies of a file formatter carry their format on the entry.
    if resolved_formatter_format(agent_id, metadata) is not None:
        return "formatter"
    if agent_id.startswith("ca_"):
        receipt = contract.execution_receipt
        if receipt is None:
            raise ValueError("A custom step's kind needs its authorized receipt")
        output = receipt.output_contract
        if output.output_state == "none":
            return "text"
        if output.output_mode == "unprofiled_generic":
            return "flexible"
        if output.output_mode == "profile_bound_generic":
            return "profile_attributes"
        if output.domain_extraction_ref is not None:
            # The receipt declares a pack builder even when its pack is not
            # loaded; the unverified contract reports that, not the kind.
            return "pack_fields"
        return "validator_result" if _validator_schema(output.output_schema_key) else "envelope_legacy"
    pack_id = (metadata.get("curation") or {}).get("domain_pack_id")
    if pack_id == "generic":
        return "pdf_extraction"
    schema_key = metadata.get("output_schema_key")
    if schema_key:
        return "validator_result" if _validator_schema(schema_key) else "other"
    if contract.representation == "pack_fields":
        return "pack_fields"
    # A declared pack that is not loaded is structured output we cannot read.
    return "other" if pack_id else "text"
