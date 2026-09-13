"""Read declared output structures without running an agent or inspecting results.

This is discovery, not a biological mapping or an executable flow snapshot.
Custom contracts are read only after reauthorizing their exact saved receipt.
"""

from typing import Any, Literal

from sqlalchemy.orm import Session

from src.lib.agent_studio.execution_revision_service import authorize_execution_receipt
from src.lib.config.schema_discovery import resolve_output_schema
from src.lib.curation_workspace.execution_contracts import resolve_receipt_profile
from src.lib.flows.export_fields import packaged_export_fields, source_catalog
from src.schemas.agent_execution_revision import AgentExecutionReceipt

from .execution_context import BenchmarkCuratorContext
from .models import FrozenStrictModel
from .suites import _digest


class BenchmarkFlowOutputContract(FrozenStrictModel):
    """A declared schema, with its representation and source identity explicit."""

    status: Literal["verified", "not_verified"]
    representation: Literal["json_schema", "profile_attributes", "pack_fields"] | None = None
    schema_definition: dict[str, Any] | None = None
    schema_digest: str | None = None
    execution_receipt: AgentExecutionReceipt | None = None
    declared_semantic_class: str | None = None
    domain_pack_id: str | None = None
    reason: str | None = None
    # A structured output contract does not prove gene/allele suitability.
    semantic_mapping_required: bool = True


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
            fields = packaged_export_fields(agent_id, {"curation": {"domain_pack_id": pack_id}})
            if fields:
                schema = source_catalog(fields)
                representation = "pack_fields"
    else:
        schema_key = metadata.get("output_schema_key")
        if schema_key:
            model = resolve_output_schema(schema_key)
            if model is not None:
                schema = model.model_json_schema()
                representation = "json_schema"
        else:
            fields = packaged_export_fields(agent_id, metadata)
            if fields:
                pack_id = (metadata.get("curation") or {}).get("domain_pack_id")
                schema = source_catalog(fields)
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
    )
